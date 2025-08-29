import json
import logging
import sqlite3
from datetime import datetime, timezone
from flask import current_app
from together import Together
from db import get_db_connection

class TokenAwareMemoryManager:
    def __init__(self, user_id, session_number):
        self.user_id = user_id
        self.session_number = session_number
        self.summarizer = Summarizer()
        self.summary_json = None
        self.history_buffer = []
        self.token_buffer = []  # Stores token counts for each interaction

        # Dynamic memory configuration
        self.tok_K = current_app.config.get('MAX_CONTEXT_TOKENS', 3000)
        self.min_interactions = current_app.config.get('MIN_INTERACTIONS_BEFORE_SUMMARY', 2)
        self.max_interactions = current_app.config.get('MAX_INTERACTIONS_LIMIT', 50)
        self.smoothing_factor = current_app.config.get('SMOOTHING_FACTOR', 0.8)
        self.safety_margin = current_app.config.get('SAFETY_MARGIN', 0.9)  # 90% of tok_K

        # Adaptive threshold
        self.adaptive_threshold = self.tok_K * self.safety_margin
        self.current_token_sum = 0

        self._load_from_db()

    def _calculate_dynamic_threshold(self):
        """
        Calculates dynamic Short_Term_Memory_K based on token consumption patterns

        Algorithm:
        1. Track cumulative tokens until threshold is reached
        2. Use exponential smoothing to adapt to token patterns
        3. Apply safety margins and constraints
        """
        if not self.token_buffer:
            return self.min_interactions

        # Calculate average tokens per interaction with exponential smoothing
        if len(self.token_buffer) == 1:
            avg_tokens = self.token_buffer[0]
        else:
            # Exponential smoothing: newer interactions have higher weight
            weights = [self.smoothing_factor ** (len(self.token_buffer) - 1 - i)
                      for i in range(len(self.token_buffer))]
            weighted_sum = sum(token * weight for token, weight in zip(self.token_buffer, weights))
            weight_sum = sum(weights)
            avg_tokens = weighted_sum / weight_sum if weight_sum > 0 else self.token_buffer[-1]

        # Calculate optimal interaction count
        optimal_k = max(1, int(self.adaptive_threshold / avg_tokens))

        # Apply constraints
        dynamic_k = max(self.min_interactions, min(self.max_interactions, optimal_k))

        logging.info(f"Dynamic threshold calculation: avg_tokens={avg_tokens:.1f}, "
                    f"optimal_k={optimal_k}, constrained_k={dynamic_k}")

        return dynamic_k

    def _should_trigger_summarization(self):
        """
        Smart summarization trigger based on token consumption

        Returns:
        - True if summarization should be triggered
        - False otherwise
        """
        if len(self.history_buffer) < self.min_interactions:
            return False

        # Calculate current cumulative token count
        self.current_token_sum = sum(self.token_buffer)

        # Trigger if we exceed the adaptive threshold
        if self.current_token_sum >= self.adaptive_threshold:
            logging.info(f"Summarization triggered: {self.current_token_sum} tokens >= "
                        f"{self.adaptive_threshold} threshold with {len(self.history_buffer)} interactions")
            return True

        # Also trigger if we hit max interactions limit (safety net)
        if len(self.history_buffer) >= self.max_interactions:
            logging.info(f"Summarization triggered: Max interactions limit ({self.max_interactions}) reached")
            return True

        return False

    def _adaptive_prune(self):
        """
        Performs intelligent pruning based on token analysis
        """
        logging.info(f"[TokenAwareMemory] Adaptive pruning for user {self.user_id}, session {self.session_number}")

        # Calculate how many interactions to keep based on current patterns
        dynamic_k = self._calculate_dynamic_threshold()

        # Determine optimal retention strategy
        if len(self.history_buffer) <= dynamic_k:
            # If current buffer is within optimal size, summarize older half
            split_point = max(1, len(self.history_buffer) // 2)
            to_summarize = self.history_buffer[:split_point]
            to_summarize_tokens = self.token_buffer[:split_point]
            retained = self.history_buffer[split_point:]
            retained_tokens = self.token_buffer[split_point:]
        else:
            # Standard approach: keep last dynamic_k interactions
            to_summarize = self.history_buffer[:-dynamic_k]
            to_summarize_tokens = self.token_buffer[:-dynamic_k]
            retained = self.history_buffer[-dynamic_k:]
            retained_tokens = self.token_buffer[-dynamic_k:]

        # Perform summarization
        new_summary = self.summarizer.summarize(self.summary_json, to_summarize)

        if new_summary:
            self.summary_json = new_summary
            self.history_buffer = retained
            self.token_buffer = retained_tokens

            summarized_tokens = sum(to_summarize_tokens)
            retained_tokens_sum = sum(retained_tokens)

            logging.info(f"[TokenAwareMemory] Pruning complete: "
                        f"Summarized {len(to_summarize)} interactions ({summarized_tokens} tokens), "
                        f"Retained {len(retained)} interactions ({retained_tokens_sum} tokens), "
                        f"Dynamic K set to {dynamic_k}")
        else:
            logging.warning("[TokenAwareMemory] Summarization failed — buffer retained.")

    def add_interaction(self, prompt, response, response_token_count, full_response_for_history=None):
        """
        Adds interaction with token-aware management

        Args:
            prompt: User input
            response: AI response (for context)
            response_token_count: Token count of the response
            full_response_for_history: Full response for database storage
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        # Store in database
        db_response = full_response_for_history if full_response_for_history is not None else response
        self._log_interaction_to_db(prompt, db_response, timestamp, response_token_count)

        # Add to memory buffers
        self.history_buffer.append({
            "prompt": prompt,
            "response": response,
            "timestamp": timestamp,
            "token_count": response_token_count
        })
        self.token_buffer.append(response_token_count)

        logging.info(f"Added interaction: {len(self.history_buffer)} total, "
                    f"{sum(self.token_buffer)} total tokens, "
                    f"new response: {response_token_count} tokens")

        # Check if summarization should be triggered
        if self._should_trigger_summarization():
            self._adaptive_prune()

    def _log_interaction_to_db(self, prompt, response, timestamp, token_count):
        """Enhanced database logging with token tracking"""
        conn = get_db_connection()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO chat_history (user_id, session_number, prompt, response, timestamp, token_count) VALUES (?, ?, ?, ?, ?, ?)",
                    (self.user_id, self.session_number, prompt, response, timestamp, token_count)
                )
        except sqlite3.Error as e:
            logging.error(f"Failed to log interaction to chat_history: {e}", exc_info=True)
        finally:
            conn.close()

    def _load_from_db(self):
        """Enhanced loading with token buffer reconstruction"""
        conn = get_db_connection()

        # Load memory summary
        row = conn.execute(
            "SELECT summary_json, history_buffer FROM conversation_memory WHERE user_id = ? AND session_number = ?",
            (self.user_id, self.session_number)
        ).fetchone()

        if row:
            self.summary_json = row['summary_json']
            if row['history_buffer']:
                loaded_buffer = json.loads(row['history_buffer'])
                self.history_buffer = loaded_buffer

                # Reconstruct token buffer from history buffer
                self.token_buffer = [interaction.get('token_count', 0) for interaction in self.history_buffer]
                logging.info(f"Loaded {len(self.history_buffer)} interactions from memory, "
                           f"{sum(self.token_buffer)} total tokens")

        conn.close()

    def save_to_db(self):
        """Enhanced saving with token information"""
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
                (self.user_id, self.session_number, self.summary_json,
                 json.dumps(self.history_buffer), datetime.now(timezone.utc).isoformat())
            )
        conn.close()

    def get_context(self):
        """Context generation with token awareness"""
        messages = []

        # Add long-term memory summary
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

        # Add short-term memory
        for interaction in self.history_buffer:
            if interaction['prompt']:
                messages.append({"role": "user", "content": interaction['prompt']})
            if interaction['response']:
                messages.append({"role": "assistant", "content": interaction['response']})

        # Log current context statistics
        current_tokens = sum(self.token_buffer) if self.token_buffer else 0
        logging.info(f"Context generated: {len(self.history_buffer)} interactions, "
                    f"{current_tokens} tokens, threshold: {self.adaptive_threshold}")

        return messages

    def get_memory_stats(self):
        """Returns current memory statistics for monitoring"""
        return {
            "current_interactions": len(self.history_buffer),
            "current_tokens": sum(self.token_buffer) if self.token_buffer else 0,
            "token_threshold": self.adaptive_threshold,
            "has_summary": bool(self.summary_json),
            "avg_tokens_per_interaction": (sum(self.token_buffer) / len(self.token_buffer)) if self.token_buffer else 0,
            "dynamic_k": self._calculate_dynamic_threshold() if self.token_buffer else self.min_interactions,
            "user_id": self.user_id,
            "session_number": self.session_number
        }


class Summarizer:
    """Enhanced summarizer remains the same as your current implementation"""
    def __init__(self):
        self.client = Together(api_key=current_app.config['TOGETHER_API_KEY'])
        self.model = current_app.config['SUMMARIZER_LLM']

    def summarize(self, previous_summary_json, conversation_log):
        # Your existing summarization logic remains unchanged
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
     "verbatim_context": "A short snippet of key Narrative context linked to this interaction, preserving tone and detail remarkably well.",
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
6.  If the Log is in JSON (Code Mode Response) then correctly retain information related to file name with correct file version number.
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
            logging.info(f"Successfully generated summary: {len(summary_json_str)} characters")
            return summary_json_str
        except Exception as e:
            logging.error(f"Summarization LLM call failed: {e}", exc_info=True)
            return previous_summary_json


# Legacy MemoryManager class for backward compatibility
class MemoryManager(TokenAwareMemoryManager):
    """
    Legacy memory manager that provides the old interface
    while using the new token-aware system underneath
    """
    def __init__(self, user_id, session_number):
        super().__init__(user_id, session_number)

    def add_interaction(self, prompt, response, full_response_for_history=None):
        """
        Legacy method signature - estimates tokens server-side
        """
        # Import here to avoid circular imports
        from chat import count_tokens, current_app

        # Estimate tokens for the response
        model_name = current_app.config.get('DEFAULT_LLM', 'meta-llama/Llama-3.3-70B-Instruct-Turbo')
        estimated_tokens = count_tokens(response, model_name)

        # Call the new token-aware method
        super().add_interaction(prompt, response, estimated_tokens, full_response_for_history)