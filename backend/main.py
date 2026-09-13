"""Point d'entree FastAPI du chatbot support NovaMart."""

from __future__ import annotations

import json
import secrets
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

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import resend as resend_client

import config
from chat import chat as _chat, stream_chat as _stream_chat

# Trace chaque execution du pipeline RAG (retrieve() + generate()) vers LangSmith.
chat = traceable(name="novamart-rag-pipeline")(_chat)
stream_chat = traceable(name="novamart-rag-pipeline-stream")(_stream_chat)

app = FastAPI(title="NovaMart Support Chatbot", version="0.1.0")

# Rate limiting : 20 requetes / minute / IP sur les endpoints chat.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # DEV uniquement - a restreindre en production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTP Basic Auth pour /dashboard.
security = HTTPBasic()


def verify_dashboard_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(
        credentials.username.encode("utf8"),
        (os.getenv("DASHBOARD_USERNAME", "admin")).encode("utf8")
    )
    correct_password = secrets.compare_digest(
        credentials.password.encode("utf8"),
        (os.getenv("DASHBOARD_PASSWORD", "novamart2026")).encode("utf8")
    )
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Accès non autorisé",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


class ChatResponse(BaseModel):
    response: str
    session_id: str


class ContactRequest(BaseModel):
    name: str
    email: str
    message: str
    session_id: str = ""


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/stats")
def get_stats():
    from chat import ANALYTICS
    import datetime
    questions = ANALYTICS["questions"]
    times = ANALYTICS["response_times"]
    from collections import Counter
    top_questions = Counter(questions).most_common(5)
    avg_time = round(sum(times) / len(times), 2) if times else 0

    return {
        "total_conversations": ANALYTICS["total_conversations"],
        "total_messages": ANALYTICS["total_messages"],
        "avg_response_time_seconds": avg_time,
        "top_questions": [{"question": q, "count": c} for q, c in top_questions],
        "hourly_conversations": ANALYTICS["hourly_conversations"],
        "start_time": ANALYTICS["start_time"],
        "uptime_hours": round(
            (datetime.datetime.now() - datetime.datetime.fromisoformat(
                ANALYTICS["start_time"]
            )).total_seconds() / 3600, 1
        ) if ANALYTICS["start_time"] else 0,
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(username: str = Depends(verify_dashboard_auth)):
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


@app.post("/contact")
def contact_agent(payload: ContactRequest):
    resend_api_key = config.RESEND_API_KEY
    support_email = config.SUPPORT_EMAIL

    if not all([resend_api_key, support_email]):
        return {"success": False, "error": "Email not configured"}

    try:
        resend_client.api_key = resend_api_key

        params = {
            "from": "NovaMart Support <onboarding@resend.dev>",
            "to": [support_email],
            "subject": f"[NovaMart Support] Demande de {payload.name}",
            "html": f"""
                <h2>Nouvelle demande de contact via le chatbot NovaMart</h2>
                <p><strong>Nom :</strong> {payload.name}</p>
                <p><strong>Email :</strong> {payload.email}</p>
                <p><strong>Session ID :</strong> {payload.session_id}</p>
                <hr>
                <p><strong>Message :</strong></p>
                <p>{payload.message}</p>
                <hr>
                <p><em>Envoyé automatiquement par le chatbot NovaMart Support.</em></p>
            """
        }

        email = resend_client.Emails.send(params)
        return {"success": True, "id": email.get("id", "")}

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/chat", response_model=ChatResponse)
@limiter.limit("20/minute")
def chat_endpoint(request: Request, payload: ChatRequest) -> ChatResponse:
    session_id = payload.session_id or str(uuid.uuid4())
    answer = chat(payload.message, session_id)
    return ChatResponse(response=answer, session_id=session_id)


@app.post("/chat/stream")
@limiter.limit("20/minute")
async def chat_stream_endpoint(request: Request, payload: ChatRequest) -> StreamingResponse:
    session_id = payload.session_id or str(uuid.uuid4())

    def event_stream():
        # Premier evenement : session_id (pour que le client persiste la memoire).
        yield f"data: {json.dumps({'session_id': session_id})}\n\n"
        # Ensuite les chunks de texte normaux.
        for chunk in stream_chat(payload.message, session_id):
            yield f"data: {json.dumps(chunk)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
