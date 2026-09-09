"""Point d'entree FastAPI du chatbot support NovaMart."""

from __future__ import annotations

import json
import uuid

# Railway lance `uvicorn backend.main:app` : on ajoute backend/ au sys.path pour
# que `from chat import ...` resolve, que le module soit importe comme
# `backend.main` ou comme `main`. Doit s'executer avant l'import de `chat`.
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv

# Charge .env au demarrage : variables LangSmith
# (LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY, LANGCHAIN_PROJECT) lues par le SDK
# langsmith, + credentials AWS / Anthropic.
load_dotenv()

from langsmith import traceable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chat import chat as _chat, stream_chat as _stream_chat

# Trace chaque execution du pipeline RAG (retrieve() + generate()) vers LangSmith.
chat = traceable(name="novamart-rag-pipeline")(_chat)
stream_chat = traceable(name="novamart-rag-pipeline-stream")(_stream_chat)

app = FastAPI(title="NovaMart Support Chatbot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # DEV uniquement - a restreindre en production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


class ChatResponse(BaseModel):
    response: str
    session_id: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(payload: ChatRequest) -> ChatResponse:
    session_id = payload.session_id or str(uuid.uuid4())
    answer = chat(payload.message, session_id)
    return ChatResponse(response=answer, session_id=session_id)


@app.post("/chat/stream")
def chat_stream_endpoint(payload: ChatRequest) -> StreamingResponse:
    def event_stream():
        for chunk in stream_chat(payload.message):
            yield f"data: {json.dumps(chunk)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
