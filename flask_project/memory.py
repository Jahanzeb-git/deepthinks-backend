import json
import logging
import sqlite3
from datetime import datetime, timezone
from flask import current_app
from together import Together
from db import get_db_connection

class Summarizer:
    def __init__(self):
        self.client = Together(api_key=current_app.config['TOGETHER_API_KEY'])
        self.model = current_app.config['SUMMARIZER_LLM']

    def summarize(self, previous_summary_json, conversation_log):
        if not conversation_log:
            return previous_summary_json

        formatted_log = ""
        if previous_summary_json:
            try:
                summary_data = json.loads(previous_summary_json)
                formatted_log += f"Previous Summary:\n"
                formatted_log += f"- Interactions: {json.dumps(summary_data.get('interactions', []))}\n"
                formatted_log += f"- Important Details: {', '.join(summary_data.get('important_details', []))}\n\n"
            except (json.JSONDecodeError, TypeError):
                logging.warning(f"Could not parse previous summary: {previous_summary_json}")

        for interaction in conversation_log:
            formatted_log += f"[{interaction['timestamp']}] USER: {interaction['prompt']}\n"
            formatted_log += f"[{interaction['timestamp']}] ASSISTANT: {interaction['response']}\n\n"

        system_prompt = """
You are an expert conversation summarizer and memory curator. Your sole job is to analyze a chronological log of user-assistant interactions and create a concise JSON summary. The log may contain a previous summary and new raw interactions. You must synthesize all the provided information into a new, single JSON object, consolidating all details. You must be retain exactly what must be retained for future conversations, not to retell or decorate.

The JSON object must have the following structure:
{
  "interactions": [
    {"timestamp": "ISO8601 string of the original interaction",
     "summary": "A contextual summary of the interaction.",
     "verbatim_context": "A short snippet of key Narative context linked to this interaction, preserving tone and detail remarkably well.",
     "priority_score": "A Priority score between 0-10, stating priority of interaction to be recalled in future."},
    ...
  ],
  "important_details": ["A list of key facts, entities, or user preferences mentioned across all interactions., or anything else you prioritize that can be referenced or recall later."],
}

Rules:
1.  Incorporate the `Previous Summary` into the new summary. Do not just append.
2.  Do not repeat any detail in interactions that also appears in important_details.
3.  Summarize each new user-assistant turn into a summary.
4.  Important: Consolidate and Retain all "key facts", "User Preferences", "Anything that have higher priority to be recalled in future" from both the previous summary and new interactions into the `important_details` array. Do not repeat details.
5.  Your output MUST be only the JSON object, with no other text or explanations.
"""
        user_prompt = f"Here is the conversation log to summarize:\n\n{formatted_log}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                max_tokens=2048,
                response_format={"type": "json_object", "schema": current_app.config['CONVERSATION_SUMMARY_SCHEMA']},
            )
            summary_json_str = response.choices[0].message.content
            json.loads(summary_json_str) # Validate JSON
            return summary_json_str
        except Exception as e:
            logging.error(f"Summarization LLM call failed: {e}", exc_info=True)
            return previous_summary_json


class MemoryManager:
    def __init__(self, user_id, session_number):
        self.user_id = user_id
        self.session_number = session_number
        self.summarizer = Summarizer()
        self.summary_json = None
        self.history_buffer = []
        self._load_from_db()

    def _log_interaction_to_db(self, prompt, response, timestamp):
        conn = get_db_connection()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO chat_history (user_id, session_number, prompt, response, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (self.user_id, self.session_number, prompt, response, timestamp)
                )
        except sqlite3.Error as e:
            logging.error(f"Failed to log interaction to chat_history: {e}", exc_info=True)
        finally:
            conn.close()

    def _load_from_db(self):
        conn = get_db_connection()
        row = conn.execute(
            "SELECT summary_json, history_buffer FROM conversation_memory WHERE user_id = ? AND session_number = ?",
            (self.user_id, self.session_number)
        ).fetchone()
        conn.close()
        if row:
            self.summary_json = row['summary_json']
            if row['history_buffer']:
                self.history_buffer = json.loads(row['history_buffer'])

    def save_to_db(self):
        conn = get_db_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO conversation_memory (user_id, session_number, summary_json, history_buffer, last_updated)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, session_number) DO UPDATE SET
                    summary_json = excluded.summary_json,
                    history_buffer = excluded.history_buffer,
                    last_updated = excluded.last_updated
                """,
                (self.user_id, self.session_number, self.summary_json, json.dumps(self.history_buffer), datetime.now(timezone.utc).isoformat())
            )
        conn.close()

    def add_interaction(self, prompt, response, full_response_for_history=None):
        timestamp = datetime.now(timezone.utc).isoformat()
        # Use the full, detailed response for history if provided, otherwise use the standard response.
        db_response = full_response_for_history if full_response_for_history is not None else response
        self._log_interaction_to_db(prompt, db_response, timestamp)

        # The history_buffer for in-context learning should only contain the clean, summarized response.
        self.history_buffer.append({"prompt": prompt, "response": response, "timestamp": timestamp})

        if len(self.history_buffer) > current_app.config['SHORT_TERM_MEMORY_K']:
            self.prune()

    def prune(self):
        logging.info(f"[MemoryManager] Pruning triggered for user {self.user_id}, session {self.session_number}")
        k = current_app.config['SHORT_TERM_MEMORY_K']
        to_summarize = self.history_buffer[:-k]
        retained = self.history_buffer[-k:]
        new_summary = self.summarizer.summarize(self.summary_json, to_summarize)
        if new_summary:
            self.summary_json = new_summary
            self.history_buffer = retained
            logging.info("[MemoryManager] Pruning complete with new summary.")
        else:
            logging.warning("[MemoryManager] Summarization failed — buffer retained.")

    def get_context(self):
        messages = []
        if self.summary_json:
            try:
                summary_data = json.loads(self.summary_json)
                combined_summary_text = "Here is a summary of the conversation so far:\n"
                interactions = summary_data.get('interactions', [])
                details = summary_data.get('important_details', [])
                if interactions:
                    combined_summary_text += f"- Key topics discussed: {json.dumps(interactions)}\n"
                if details:
                    combined_summary_text += f"- Important details to remember: {', '.join(details)}\n"
                messages.append({"role": "system", "content": combined_summary_text})
            except (json.JSONDecodeError, TypeError):
                logging.warning(f"Could not parse summary JSON for context: {self.summary_json}")

        for interaction in self.history_buffer:
            if interaction['prompt']:
                messages.append({"role": "user", "content": interaction['prompt']})
            if interaction['response']:
                 messages.append({"role": "assistant", "content": interaction['response']})
        return messages