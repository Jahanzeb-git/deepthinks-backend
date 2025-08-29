# Deepthinks Chatbot Backend

A production-ready, containerized Flask backend for a conversational AI chatbot. This application features secure JWT-based authentication with Google OAuth, integration with the Together AI API for large language model responses, and a persistent SQLite database running in high-concurrency WAL mode. The project is served with a Gunicorn WSGI server for easy and reliable deployment.

## Core Features

- **Secure Authentication**: Robust user authentication system using JSON Web Tokens (JWT), with support for both traditional email/password signup and Google OAuth 2.0.
- **RESTful API**: A comprehensive API for chat interactions, session management, user settings, and conversation history.
- **Together AI Integration**: Seamlessly connects with the Together AI platform to leverage various large language models for generating chat responses.
- **Advanced Memory System**: A custom, sophisticated memory architecture to provide deep contextual understanding across long conversations (see details below).
- **File Uploads & Processing**: Supports file uploads, including text extraction from PDFs, DOCX, and XLSX files, and image handling for vision-enabled AI models.
- **Token Usage Analytics**: Endpoints for tracking and analyzing token usage per user, model, and session.
- **Conversation Sharing**: Functionality to create shareable, password-protected links for conversations.
- **Production-Ready Deployment**: Containerized with Docker and served by a Gunicorn WSGI server to handle multiple concurrent users efficiently.

## The Memory System

The backend features a custom memory system designed to provide the AI with a deep, persistent understanding of the conversation, blending immediate context with long-term knowledge.

### Short-Term Memory

The short-term memory acts as a high-fidelity buffer, holding the last **k** user-assistant interactions (a turn consists of one user prompt and one AI response). This buffer provides the immediate context for the AI's next response. The value of `k` is configurable in `config.py` (`SHORT_TERM_MEMORY_K`).

### Long-Term Memory

Long-term memory is a structured JSON object stored persistently in the database for each conversation session. It is designed to store a condensed, summarized history of the entire conversation. It contains:
- `interactions`: An array of summarized past conversation turns, including a contextual summary, a verbatim snippet for preserving key details, and a priority score.
- `important_details`: A list of key facts, entities, and user preferences that are extracted and consolidated over the course of the conversation.

### Summarization & Pruning (Context Smoothing)

To prevent the context from growing indefinitely, a pruning mechanism is triggered when the short-term memory buffer exceeds `k` interactions. The process works as follows:

1.  The oldest interactions in the short-term buffer are selected for summarization.
2.  These interactions, along with the existing long-term memory summary, are sent to a specialized summarizer LLM.
3.  The LLM generates a new, updated JSON summary that intelligently integrates the old summary with the new interactions.
4.  The application then updates the long-term memory with this new summary and prunes the summarized interactions from the short-term buffer.

This process can be thought of as a **context smoothing algorithm**, where the raw, verbose conversation history is continuously distilled into a dense, structured, and highly relevant summary, ensuring the AI always has the most important context available without exceeding token limits.

## Technology Stack

- **Backend**: Flask, Gunicorn
- **Database**: SQLite (with WAL mode enabled)
- **Containerization**: Docker
- **Core Libraries**: `PyJWT` for authentication, `together` for AI integration, `google-auth` for OAuth, `pypdf`, `python-docx`, `openpyxl` for file processing.

## Configuration

To run the application, you must create a `.env` file in the `flask_project` directory. The application will not start without it.

Create a file named `.env` with the following variables:

```
# A strong, random string for signing JWT tokens
JWT_SECRET_KEY=your_very_secret_jwt_key

# Your Google OAuth 2.0 Client ID
GOOGLE_CLIENT_ID=your_google_client_id.apps.googleusercontent.com

# Your API key for the Together AI platform
TOGETHER_API_KEY=your_together_api_key

# Set to 'development' to enable Flask's debug mode (optional)
# FLASK_ENV=development
```

## Deployment Guide

This application is designed to be deployed as a Docker container. The following command will pull the image from Docker Hub and run it in a detached, production-ready state.

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

This command will:
- Run the container in detached mode (`-d`).
- Map port 5000 on your server to port 5000 in the container (`-p 5000:5000`).
- Name the container `deepthinks-backend-container` for easy management.
- Ensure the container always restarts if it stops (`--restart=always`).
- Securely pass your secrets to the application using the `.env` file.
- Persist the database by mounting it from your server's filesystem into the container.

```
