"""Request-time checks for optional generation resources."""

from fastapi import HTTPException


def require_model_client(client):
    if client is None:
        raise HTTPException(503, "Configure GROQ_API_KEY for debate and research generation")
    return client
