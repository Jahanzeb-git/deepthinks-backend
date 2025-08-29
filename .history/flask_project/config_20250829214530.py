import os

# --- Configuration ---
# --- BASE DIRECTORY ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# --- CORE SETTINGS ---
DATABASE = os.path.join(BASE_DIR, 'deepthinks.db')
SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'd9d41e7391af3dc0868618f136b94f7d')

# ---JWT SETTING ---
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_DAYS = 180

# --- API KEYS AND SERVICE CONFIG ---
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '1061742468081-1g389n9i177f9vk95eg88tsornpfsbm4.apps.googleusercontent.com')
TOGETHER_API_KEY = os.getenv('TOGETHER_API_KEY','76fc8053194685a65fb8d82f723d046e9c99d79a803efbe88a55a2169f2ba63d')

# -- LLM MODEL CONFIG ---
DEFAULT_LLM = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
REASON_LLM = "Qwen/Qwen3-235B-A22B-Thinking-2507"
CODE_LLM = "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"
SUMMARIZER_LLM = "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"

# --- TOKEN-AWARE MEMORY MANAGEMENT ---
# Maximum context tokens to maintain before triggering summarization
MAX_CONTEXT_TOKENS = 3000

# Minimum number of interactions before allowing summarization
MIN_INTERACTIONS_BEFORE_SUMMARY = 2

# Maximum interactions to keep in buffer (safety limit)
MAX_INTERACTIONS_LIMIT = 50

# Exponential smoothing factor for adaptive threshold calculation
# Higher values (closer to 1.0) give more weight to recent interactions
SMOOTHING_FACTOR = 0.8

# Safety margin as percentage of MAX_CONTEXT_TOKENS
# System will trigger summarization at 90% of max to leave headroom
SAFETY_MARGIN = 0.9

# --- LEGACY MEMORY SETTINGS (for backward compatibility) ---
# The number of recent interactions to keep in the buffer before summarizing.
# An interaction is one user prompt and one AI response.
SHORT_TERM_MEMORY_K = 4 # Four Interactions are used as is for detailed context supported by model massive context window.

# --- CONVERSATION SUMMARY SCHEMA ---
CONVERSATION_SUMMARY_SCHEMA = { # Conversation summary schema...
    "type": "object",
    "properties": {
        "interactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string", "format": "date-time"},
                    "summary": {"type": "string"},
                    "verbatim_context": {"type": "string"},
                    "priority_score": {"type": "number"}
                },
                "required": ["timestamp", "summary"]
            }
        },
        "important_details": {
            "type": "array",
            "items": {"type": "string"}
        }
    },
    "required": ["interactions", "important_details"]
}