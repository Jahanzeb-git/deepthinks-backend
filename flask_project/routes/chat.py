import json
import logging
import re
from flask import Blueprint, request, jsonify, Response, stream_with_context, current_app
from together import Together
from auth import optional_token_required
from memory import MemoryManager
from db import get_db_connection, get_unauthorized_request_count, increment_unauthorized_request_count
from routes.together_key_routes import decrypt_key


chat_bp = Blueprint('chat_bp', __name__)

THINK_TAG_REGEX = re.compile(r'<think>.*?</think>', re.DOTALL)

BASE_SYSTEM_PROMPT = """
# Core Instructions (DO NOT OVERRIDE)
You are Deepthinks, a context-aware AI assistant with advanced memory capabilities.
Your primary goal is to provide accurate, relevant, and coherent responses by effectively utilizing the memory system described below.
## Memory System
- **LONG-TERM MEMORY**: Appears as "Here is a summary of the conversation so far:" containing:
    - `interactions`: An array of past conversation summaries, verbatim context which needed for verbatism for interaction, and priority score which states priority between 0-10 of interaction to be recalled in future.
    - `important_details`: A list of key facts, user preferences, and other persistent information.
- **SHORT-TERM MEMORY**: The most recent user/assistant message exchanges, provided for immediate context.
## Important Guidelines
1.  **Prioritize Memory**: Always use the long-term and short-term memory to inform your responses.
2.  **Trust Recent Information**: If recent user messages contradict long-term memory, the most recent information takes precedence.
3.  **Be Context-Aware**: Do not explicitly mention your memory system as its proprietary. Use the context it provides to have natural, informed conversations.
4.  **Using Timestamps**: Timestamps are provided in the memory, use when needed for Time related scenarios or when explicitly asked. Make sure to convert timestamp to Pakistan standard time.
5.  **Advanced Markdown Formatting**: Enforce a markdown style by using semantically correct, hierarchical headings to mirror content structure; leverage advanced features—fenced code blocks, advanced tables structure that should render correctly for any structured data, task lists, blockquotes for key insights, inline admonitions (e.g., > **Note:**), footnotes, and consistent bolding/italicizing conventions—so that every response adheres to our unique, professional markdown specification.
6. **Equations Rendering** Always render Mathematical Equations, Formulations and calculations in KaTeX for better Readability.
7. **Ask Clarifying Questions Selectively**:
    -When the user’s request is ambiguous, incomplete, or has multiple possible interpretations, pause before responding and ask at most two concise clarifying questions.
    -Do not ask clarifying questions for every prompt—only when the context is insufficient to generate a precise, accurate, or user-aligned response.
    -If user request can be narrow-down or then ask questions one by one to understand what exactly user want before diving into solution.
    -If the intent is reasonably clear, proceed without asking and answer confidently.
# User Information
The user's preferred name is: {user_name}
# User-Defined Persona
**User-defined persona**: Use this user-defined persona for shaping your Tone and behavior requested by user.
{user_persona}
"""

def get_user_chat_settings(user_id):
    conn = get_db_connection()
    try:
        settings = conn.execute(
            "SELECT temperature, top_p, system_prompt, what_we_call_you, together_api_key FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
        if settings:
            return {
                "temperature": settings['temperature'] if settings['temperature'] is not None else 0.7,
                "top_p": settings['top_p'] if settings['top_p'] is not None else 1.0,
                "system_prompt": settings['system_prompt'] or "You are a helpful assistant.",
                "what_we_call_you": settings['what_we_call_you'] or "User",
                "together_api_key": (decrypt_key(settings['together_api_key']) if settings['together_api_key'] else None)
            }
    finally:
        conn.close()
    return {"temperature": 0.7, "top_p": 1.0, "system_prompt": "You are a helpful assistant.", "what_we_call_you": "User", "together_api_key": None}

@chat_bp.route('/chat', methods=['POST'])
@optional_token_required
def chat(current_user):
    data = request.json or {}
    session_id = data.get('session_id')
    query = data.get('query', '').strip()

    if not session_id or not query:
        return jsonify({"error": "session_id and query are required"}), 400

    is_vision_request = False

    if current_user:
        user_id = current_user['id']
        cache_key = f"{user_id}-{session_id}"
        if cache_key in current_app.file_cache:
            file_data = current_app.file_cache.pop(cache_key)

            if file_data.get("is_image"):
                is_vision_request = True
                image_url = file_data['content']
                # The user's text query and the image URL will be used later
            else:
                filename = file_data['filename']
                file_content = file_data['content']
                query = f"This is a content of {filename}, i have send please take this as reference and proceed with the request as: {query}"
                query = f"{file_content}\n\n{query}"

        reason = bool(data.get('reason', False))
        chat_settings = get_user_chat_settings(user_id)
        api_key = chat_settings.get('together_api_key') or current_app.config['TOGETHER_API_KEY']
    else:
        user_id = session_id
        request_count = get_unauthorized_request_count(user_id)
        if request_count >= 2:
            return jsonify({"error": "You have Hit the Limit Please Sign in to Continue!"}), 429
        increment_unauthorized_request_count(user_id)
        reason = False
        chat_settings = {"temperature": 0.7, "top_p": 1.0, "system_prompt": "You are a helpful assistant.", "what_we_call_you": "User"}
        api_key = current_app.config['TOGETHER_API_KEY']

    memory = MemoryManager(user_id, session_id)
    client = Together(api_key=api_key)

    if is_vision_request:
        model_name = "Qwen/Qwen2.5-VL-72B-Instruct"
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": query},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
        }]
    else:
        context_messages = memory.get_context()
        context_messages.append({"role": "user", "content": query})
        model_name = current_app.config['REASON_LLM'] if reason else current_app.config['DEFAULT_LLM']
        final_system_prompt = BASE_SYSTEM_PROMPT.format(
            user_name=chat_settings['what_we_call_you'],
            user_persona=chat_settings['system_prompt']
        )
        messages = [{"role": "system", "content": final_system_prompt}] + context_messages

    def generate_and_update_memory():
        chunks = []
        generation_completed_normally = False
        try:
            stream = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=chat_settings['temperature'],
                top_p=chat_settings['top_p'],
                max_tokens=4096 if reason else 1200,
                stream=True
            )
            for token_obj in stream:
                if token_obj.choices:
                    delta = token_obj.choices[0].delta.content or ''
                    chunks.append(delta)
                    yield f"data: {json.dumps({'token': delta, 'trace': reason})}\n\n".encode()

            generation_completed_normally = True

        except GeneratorExit:
            logging.warning(f"Client disconnected, generation for session {session_id} was interrupted.")

        except Exception as e:
            logging.error(f"Streaming error: {e}", exc_info=True)
            yield f"data: {json.dumps({'error': 'Streaming failed', 'details': str(e)})}\n\n".encode()

        finally:
            if generation_completed_normally:
                full_answer = ''.join(chunks).strip()
                if full_answer:
                    # For vision requests, the 'query' is just the text part.
                    # We might want to save a more descriptive placeholder for the image in memory.
                    memory_query = f"Analyzed an image with the prompt: {query}" if is_vision_request else query

                    if reason and not is_vision_request:
                        cleaned_answer = THINK_TAG_REGEX.sub('', full_answer).strip()
                        memory.add_interaction(memory_query, cleaned_answer, full_response_for_history=full_answer)
                    else:
                        memory.add_interaction(memory_query, full_answer)

                    memory.save_to_db()
                    logging.info(f"Successfully saved full response for session {session_id}")
                    yield f"data: {json.dumps({'status': 'done'})}\n\n".encode()
            else:
                logging.info(f"Generation for session {session_id} did not complete normally. No data will be saved to memory.")

            yield b"event: end-of-stream\ndata: {}\n\n"

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"
    }
    return Response(stream_with_context(generate_and_update_memory()), headers=headers)