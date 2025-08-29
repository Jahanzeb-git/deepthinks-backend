# Deepthinks Chatbot Backend

A production-ready, containerized Flask backend for a sophisticated conversational AI chatbot. This application features a state-of-the-art, token-aware memory system, multiple operational modes (including a specialized code generation mode), secure JWT-based authentication with Google OAuth, and integration with the Together AI API. The project is served with a Gunicorn WSGI server for high-concurrency and is fully containerized with Docker for easy and reliable deployment.

## Core Features

- **Secure Authentication**: Robust user authentication system using JSON Web Tokens (JWT), with support for both traditional email/password signup and Google OAuth 2.0.
- **Multi-Mode Operation**: The chatbot can operate in different modes (`default`, `reason`, `code`), each utilizing a different underlying LLM and system prompt for optimized performance on various tasks.
- **Advanced Code Generation**: A specialized `code` mode that uses a powerful coding model and a structured JSON output to deliver production-ready code, complete with file names, versioning, and explanations.
- **Token-Aware Memory System**: A custom, dynamic memory architecture that intelligently manages context length based on token consumption, ensuring optimal performance and preventing context overflow (see details below).
- **File Uploads & Processing**: Supports file uploads, including text extraction from PDFs, DOCX, and XLSX files, and image handling for vision-enabled AI models.
- **Token Usage Analytics**: Endpoints for tracking and analyzing token usage per user, model, and session.
- **Conversation Sharing**: Functionality to create shareable, password-protected links for conversations.
- **Production-Ready Deployment**: Containerized with Docker and served by a Gunicorn WSGI server to handle multiple concurrent users efficiently.

## The Token-Aware Memory System

The backend features a highly advanced memory system designed to provide the AI with a deep, persistent understanding of the conversation. Unlike a simple static context window, this system dynamically adapts to the flow of the conversation based on token usage.

### Dynamic Short-Term Memory

The core of the system is a short-term memory buffer that holds recent interactions. However, the size of this buffer is not fixed. Instead of a static `k` number of turns, the system uses a **token-based adaptive threshold**. Summarization is triggered when the cumulative token count of the interactions in the buffer exceeds a configurable threshold (`MAX_CONTEXT_TOKENS`), ensuring that the context sent to the LLM is always within its processing limits.

### Adaptive Pruning & Context Smoothing

When the token threshold is reached, an adaptive pruning process is initiated:

1.  **Dynamic `k` Calculation**: The system first calculates a dynamic number of interactions to keep (`dynamic_k`) based on the recent token consumption pattern. This is achieved using an **exponential smoothing algorithm** that gives more weight to recent, more relevant interactions. The formula for the smoothed average tokens per interaction is:

    *AvgTokens = (Î± * CurrentTokens) + ((1 - Î±) * PreviousAvg)*

    Where `Î±` is the `SMOOTHING_FACTOR`. This allows the system to adapt quickly to changes in conversation density (e.g., switching from short questions to long code snippets).

2.  **Intelligent Retention**: Based on `dynamic_k`, the system decides which interactions to summarize and which to retain in their raw form in the short-term buffer.

3.  **Summarization**: The interactions marked for pruning are sent to a specialized summarizer LLM, which integrates them into the persistent, long-term memory JSON object.

This entire process ensures a fluid and highly efficient use of the context window, providing the AI with the most relevant information without sacrificing performance or risking context overflow.

## Technology Stack

- **Backend**: Flask, Gunicorn
- **Database**: SQLite (with WAL mode enabled)
- **Containerization**: Docker
- **Core Libraries**: `PyJWT`, `google-auth`, `together`, `pydantic`, `tiktoken`, `python-magic`, `pypdf`, `python-docx`, `openpyxl`.

## Configuration

To run the application, you must create a `.env` file. The application will not start without it.

Create a file named `.env` with the following variables:

```
# A strong, random string for signing JWT tokens
JWT_SECRET_KEY=your_very_secret_jwt_key

# Your Google OAuth 2.0 Client ID
GOOGLE_CLIENT_ID=your_google_client_id.apps.googleusercontent.com

# Your API key for the Together AI platform
TOGETHER_API_KEY=your_together_api_key

# A key for encrypting user-provided API keys (must be 32 url-safe base64-encoded bytes)
TOGETHER_KEY_ENC_KEY=your_fernet_encryption_key

# Set to 'development' to enable Flask's debug mode (optional)
# FLASK_ENV=development
```

## Deployment Guide

This application is designed to be deployed as a Docker container. The following command will pull the latest image from Docker Hub and run it in a detached, production-ready state.

### Prerequisites

1.  **Docker Installed**: You must have Docker installed on your server.
2.  **`.env` File**: You must have a valid `.env` file (as described above) on your server.
3.  **Database File**: You need to create an empty file for the SQLite database (e.g., `touch deepthinks.db`).

### Run Command

Execute the following command on your server. Remember to replace the placeholder paths with the **absolute paths** to your `.env` and database files.

```bash
docker run -d \
  -p 5000:5000 \
  --name deepthinks-backend-container \
  --restart=always \
  --env-file /path/to/your/.env \
  -v /path/to/your/database/deepthinks.db:/app/deepthinks.db \
  jahanzeb833/deepthinks-backend:latest
```