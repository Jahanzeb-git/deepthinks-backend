import json
import logging
import re
import tiktoken
from flask import Blueprint, request, jsonify, Response, stream_with_context, current_app
from together import Together
from auth import optional_token_required
from memory import TokenAwareMemoryManager
from db import get_db_connection, get_unauthorized_request_count, increment_unauthorized_request_count
from routes.together_key_routes import decrypt_key
from pydantic import BaseModel, Field
from typing import List, Optional


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
5.  **Advanced Markdown Formatting**: Enforce a markdown style by using semantically correct, hierarchical headings to mirror content structure; leverage advanced featuresâ€"fenced code blocks, advanced tables structure that should render correctly for any structured data, task lists, blockquotes for key insights, inline admonitions (e.g., > **Note:**), footnotes, and consistent bolding/italicizing conventionsâ€"so that every response adheres to our unique, professional markdown specification.
6. **Equations Rendering**:  Always render Mathematical Equations, Formulations and calculations in KaTeX for better Readability.
7. **Ask Clarifying Questions Selectively**:
    -When the user's request is ambiguous, incomplete, or has multiple possible interpretations, pause before responding and ask at most two concise clarifying questions.
    -Do not ask clarifying questions for every promptâ€"only when the context is insufficient to generate a precise, accurate, or user-aligned response.
    -If user request can be narrow-down or then ask questions one by one to understand what exactly user want before diving into solution.
    -If the intent is reasonably clear, proceed without asking and answer confidently.
8. **Coding Requirement**: - If the user has a coding-related request, recommend using the Deepcode feature. This mode leverages the most powerful open-source coding model available.
- Prompt the user to enable Deepcode by toggling the Deepcode switch in the app.
- When Deepcode is enabled, memory will automatically switch to JSON format (this indicates the mode change).
# User Information
The user's preferred name is: {user_name}
# User-Defined Persona
**User-defined persona**: Use this user-defined persona for shaping your Tone and behavior requested by user.
{user_persona}
"""

# JSON Schema for code responses (separate from template)
CODE_JSON_SCHEMA = """{
  "Text": "Optional explanation or description of the solution before files",
  "Files": [
    {
      "FileName": "filename.ext",
      "FileVersion": "int: Relevant file Version number, increment if already generated same file from previous file version."
      "FileCode": "complete file content here",
      "FileText": "Any Text required like explanation, note or anything for that file."
    }
  ],
  "Conclusion": "Any text like explanation, description, conclusion, a guide, or anything else needed after project files."
}"""

CODE_SYSTEM_PROMPT_TEMPLATE = """
# Core Instructions (DO NOT OVERRIDE)
You are Deepthinks, a context-aware AI assistant with advanced memory capabilities and specialized code generation expertise.
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
5.  **Markdown Formatting**: The 'Text' field is provided for the JSON schema in which you have to include any Text as you want based on Project, anything you have to ask to user, provide documentation, or explanation. Make sure this Text should be in Markdown so that front-end can render accordingly.
6.  **Warning**: Do NOT include Text outside JSON format.
## Code Generation Specific Guidelines
7. **Output Format**: You MUST respond ONLY in valid JSON format. No other format is acceptable.
8. **JSON Schema**: Your response must follow this exact structure:
   ```json
   {json_schema}
   ```
9. **Code Quality**: Generate production-ready, well-commented, and properly structured code.
10. **Clarification**: When code requirements are ambiguous, ask specific technical clarifying questions within the JSON Text field.
11. **Best Practices**: Follow industry best practices, security guidelines, and proper error handling in generated code.
12. **File Organization**: If problem required multiple files then add respective files in 'Files' list in JSON, Create logical file structures as artifacts.
13. ***File Versioning*: (Important) If you are generating a new file, set the 'FileVersion' to "1". If you are editing a previously generated file, you MUST increment its 'FileVersion' by one.
14. **No Assumptions**: Do not make assumptions about technical requirements - ask for clarification when needed with pause.
# User Information
The user's preferred name is: {user_name}
"""

# Pydantic models for JSON schema
class CodeFile(BaseModel):
    FileName: str = Field(description="The name of the file including extension")
    FileVersion: Optional[str] = Field(description="The version of the file, e.g., '1'")
    FileCode: str = Field(description="The complete content of the file")
    FileText: Optional[str] = Field(description="Any text required like explanation, note or anything for that file")

class CodeResponse(BaseModel):
    Text: Optional[str] = Field(description="Optional explanation or description of the solution before files")
    Files: List[CodeFile] = Field(description="List of generated code files")
    Conclusion: Optional[str] = Field(description="Any text like explanation, description, conclusion, a guide, or anything else needed after project files")

# Token counting utilities
def get_tokenizer_for_model(model_name):
    """Get appropriate tokenizer for the model."""
    try:
        # Map model names to tiktoken encodings
        model_tokenizer_map = {
            # Llama models
            "meta-llama/Llama-3.3-70B-Instruct-Turbo": "cl100k_base",
            "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free": "cl100k_base",
            "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo": "cl100k_base",
            "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo": "cl100k_base",

            # Qwen models
            "Qwen/Qwen3-235B-A22B-fp8-tput": "cl100k_base",
            "Qwen/Qwen2.5-VL-72B-Instruct": "cl100k_base",
            "Qwen/Qwen2.5-72B-Instruct-Turbo": "cl100k_base",
            "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8": "cl100k_base",

            # Default fallback
            "default": "cl100k_base"
        }

        encoding_name = model_tokenizer_map.get(model_name, "cl100k_base")
        return tiktoken.get_encoding(encoding_name)

    except Exception as e:
        logging.warning(f"Failed to get tokenizer for {model_name}: {e}. Using default.")
        return tiktoken.get_encoding("cl100k_base")

def count_tokens(text, model_name):
    """Count tokens in text using appropriate tokenizer for the model."""
    try:
        if not text or not isinstance(text, str):
            return 0

        tokenizer = get_tokenizer_for_model(model_name)
        return len(tokenizer.encode(text))

    except Exception as e:
        logging.error(f"Token counting failed for model {model_name}: {e}")
        # Fallback: rough estimation (4 chars per token average)
        return max(1, len(text) // 4)

def count_message_tokens(messages, model_name):
    """Count tokens in a list of messages."""
    try:
        total_tokens = 0
        for message in messages:
            # Count role tokens (small overhead)
            total_tokens += 4  # Approximate overhead per message

            # Count content tokens
            content = message.get('content', '')
            if isinstance(content, str):
                total_tokens += count_tokens(content, model_name)
            elif isinstance(content, list):
                # Handle multimodal content (vision models)
                for item in content:
                    if item.get('type') == 'text':
                        total_tokens += count_tokens(item.get('text', ''), model_name)
                    elif item.get('type') == 'image_url':
                        total_tokens += 765  # Approximate tokens for image processing

        return total_tokens

    except Exception as e:
        logging.error(f"Message token counting failed: {e}")
        # Fallback estimation
        total_text = ' '.join([str(msg.get('content', '')) for msg in messages])
        return max(10, len(total_text) // 4)

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

def validate_reason_parameter(reason):
    """Validate and normalize the reason parameter."""
    if reason is None:
        return "default"

    if isinstance(reason, bool):
        # Handle legacy boolean format
        return "reason" if reason else "default"

    if isinstance(reason, str):
        reason = reason.lower().strip()
        if reason in ["code", "reason", "default"]:
            return reason
        else:
            logging.warning(f"Invalid reason parameter: {reason}. Defaulting to 'default'")
            return "default"

    logging.warning(f"Unexpected reason parameter type: {type(reason)}. Defaulting to 'default'")
    return "default"

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

        # Validate and normalize reason parameter
        reason = validate_reason_parameter(data.get('reason'))
        chat_settings = get_user_chat_settings(user_id)
        api_key = chat_settings.get('together_api_key') or current_app.config['TOGETHER_API_KEY']
    else:
        user_id = session_id
        request_count = get_unauthorized_request_count(user_id)
        if request_count >= 2:
            return jsonify({"error": "You have Hit the Limit Please Sign in to Continue!"}), 429
        increment_unauthorized_request_count(user_id)
        reason = "default"  # Unauthorized users get default mode only
        chat_settings = {"temperature": 0.7, "top_p": 1.0, "system_prompt": "You are a helpful assistant.", "what_we_call_you": "User"}
        api_key = current_app.config['TOGETHER_API_KEY']

    memory = TokenAwareMemoryManager(user_id, session_id)
    client = Together(api_key=api_key)

    # Determine model based on mode
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

        # Select model and system prompt based on reason
        if reason == "code":
            model_name = current_app.config['CODE_LLM']
            final_system_prompt = CODE_SYSTEM_PROMPT_TEMPLATE.format(
                json_schema=CODE_JSON_SCHEMA,
                user_name=chat_settings['what_we_call_you']
            )
        elif reason == "reason":
            model_name = current_app.config['REASON_LLM']
            final_system_prompt = BASE_SYSTEM_PROMPT.format(
                user_name=chat_settings['what_we_call_you'],
                user_persona=chat_settings['system_prompt']
            )
        else:  # default
            model_name = current_app.config['DEFAULT_LLM']
            final_system_prompt = BASE_SYSTEM_PROMPT.format(
                user_name=chat_settings['what_we_call_you'],
                user_persona=chat_settings['system_prompt']
            )

        messages = [{"role": "system", "content": final_system_prompt}] + context_messages

    def generate_and_update_memory():
        chunks = []
        generation_completed_normally = False

        try:
            # Count input tokens before generation
            input_token_count = count_message_tokens(messages, model_name)
            logging.info(f"Input tokens: {input_token_count} for model {model_name} (mode: {reason})")

            # Prepare request parameters
            request_params = {
                "model": model_name,
                "messages": messages,
                "temperature": chat_settings['temperature'],
                "top_p": chat_settings['top_p'],
                "max_tokens": 6000,
                "stream": True
            }

            # Add JSON schema for code mode
            if reason == "code":
                request_params["response_format"] = {
                    "type": "json_schema",
                    "schema": CodeResponse.model_json_schema(),
                }

            stream = client.chat.completions.create(**request_params)

            for token_obj in stream:
                if token_obj.choices:
                    delta = token_obj.choices[0].delta.content or ''
                    chunks.append(delta)
                    yield f"data: {json.dumps({'token': delta, 'mode': reason})}\n\n".encode()

            generation_completed_normally = True

        except GeneratorExit:
            logging.warning(f"Client disconnected, generation for session {session_id} was interrupted.")

        except Exception as e:
            logging.error(f"Streaming error: {e}", exc_info=True)
            error_response = {
                'error': 'Generation failed',
                'details': str(e),
                'mode': reason
            }
            yield f"data: {json.dumps(error_response)}\n\n".encode()

        finally:
            if generation_completed_normally:
                full_answer = ''.join(chunks).strip()
                if full_answer:
                    # Count output tokens after generation
                    output_token_count = count_tokens(full_answer, model_name)
                    logging.info(f"Output tokens: {output_token_count} for model {model_name} (mode: {reason})")

                    # For vision requests, the 'query' is just the text part.
                    # We might want to save a more descriptive placeholder for the image in memory.
                    memory_query = f"Analyzed an image with the prompt: {query}" if is_vision_request else query

                    # Handle different modes for memory storage
                    if reason == "code":
                        # For code mode, validate JSON and store entire response
                        try:
                            # Validate JSON structure
                            json.loads(full_answer)
                            memory.add_interaction(memory_query, full_answer, output_token_count)
                            logging.info(f"Added code interaction: {output_token_count} tokens")
                        except json.JSONDecodeError as e:
                            logging.error(f"Invalid JSON in code response: {e}")
                            # Store with error note
                            error_noted_response = f"[JSON_PARSE_ERROR] {full_answer}"
                            memory.add_interaction(memory_query, error_noted_response, output_token_count)
                            # Send error to frontend
                            yield f"data: {json.dumps({'error': 'Invalid JSON generated', 'mode': reason})}\n\n".encode()

                    elif reason == "reason" and not is_vision_request:
                        # For reasoning mode, clean <think> tags
                        cleaned_answer = THINK_TAG_REGEX.sub('', full_answer).strip()
                        cleaned_output_tokens = count_tokens(cleaned_answer, model_name)
                        memory.add_interaction(memory_query, cleaned_answer, cleaned_output_tokens, full_response_for_history=full_answer)
                        logging.info(f"Added reasoning interaction: {cleaned_output_tokens} tokens (cleaned from {output_token_count})")

                    else:
                        # Default mode
                        memory.add_interaction(memory_query, full_answer, output_token_count)
                        logging.info(f"Added default interaction: {output_token_count} tokens")

                    memory.save_to_db()
                    logging.info(f"Successfully saved full response for session {session_id} (mode: {reason})")

                    # Send memory stats for debugging
                    memory_stats = memory.get_memory_stats()
                    memory_stats['mode'] = reason
                    yield f"data: {json.dumps({'memory_stats': memory_stats})}\n\n".encode()
                    yield f"data: {json.dumps({'status': 'done', 'mode': reason})}\n\n".encode()
            else:
                logging.info(f"Generation for session {session_id} did not complete normally. No data will be saved to memory.")

            yield b"event: end-of-stream\ndata: {}\n\n"

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"
    }
    return Response(stream_with_context(generate_and_update_memory()), headers=headers)


@chat_bp.route('/memory-stats/<session_id>', methods=['GET'])
@optional_token_required
def get_memory_stats(current_user, session_id):
    """Debug endpoint to view memory statistics."""
    if not current_user:
        return jsonify({"error": "Authentication required"}), 401

    try:
        memory = TokenAwareMemoryManager(current_user['id'], session_id)
        stats = memory.get_memory_stats()
        return jsonify(stats)
    except Exception as e:
        logging.error(f"Failed to get memory stats: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve memory stats"}), 500