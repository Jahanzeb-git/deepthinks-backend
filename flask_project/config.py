import os

# --- Configuration ---
# --- BASE DIRECTORY ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# --- CORE SETTINGS ---
DATABASE = os.path.join(BASE_DIR, 'deepthinks.db')
SECRET_KEY = os.getenv('JWT_SECRET_KEY')

# ---JWT SETTING ---
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_DAYS = 180

# --- API KEYS AND SERVICE CONFIG ---
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
TOGETHER_API_KEY = os.getenv('TOGETHER_API_KEY')

# -- LLM MODEL CONFIG ---
DEFAULT_LLM = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
REASON_LLM = "Qwen/Qwen3-235B-A22B-fp8-tput"
SUMMARIZER_LLM = "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"

# --- MEMORY MANAGEMENT ---
# The number of recent interactions to keep in the buffer before summarizing.
# An interaction is one user prompt and one AI response.
SHORT_TERM_MEMORY_K = 4 # Four Interactions are used as is for detailed context supported by model massive context window.s
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
                    "priority_score": {"type": "float"}
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