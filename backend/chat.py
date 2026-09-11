"""Logique du chatbot NovaMart — RAG hybride avec memoire conversationnelle.

Approche en deux etapes :
  1. retrieve() (AWS Bedrock) -> recupere les passages de la Knowledge Base.
  2. Anthropic Messages API   -> genere une reponse a partir du contexte + historique.

L'historique par session est persiste dans Redis (Upstash), avec un fallback
en memoire process si Redis est indisponible. Limite aux 10 derniers echanges
pour rester sous le context window.
"""

from __future__ import annotations

import datetime
import json
import os
import time
from typing import Generator

import redis

import config

MOCK_RESPONSE = "Je suis NovaMart Support. [MOCK - AWS not configured]"
NO_CONTEXT_RESPONSE = (
    "Je n'ai pas trouvé d'information sur ce sujet dans notre documentation. [NO_CONTEXT]"
)

# Nombre de refus [NO_CONTEXT] consecutifs (meme session) avant de proposer
# le handoff vers un agent humain.
HANDOFF_THRESHOLD = 2

GENERATION_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "Tu es l'assistant support de NovaMart, une boutique "
    "d'électronique en ligne. Tu as deux modes de réponse :\n\n"
    "1. QUESTIONS SUR NOVAMART : Si le message est une vraie question "
    "sur les produits, livraisons, retours ou politiques de NovaMart, "
    "réponds UNIQUEMENT en te basant sur le contexte fourni. Si "
    "l'information n'est pas dans le contexte, dis-le honnêtement.\n\n"
    "2. MESSAGES CONVERSATIONNELS : Si le message est une réponse "
    "courte (oui, non, ok, merci, j'ai une autre question, etc.) "
    "ou une continuation naturelle de la conversation, réponds de "
    "façon naturelle et chaleureuse sans chercher dans les documents. "
    "Invite l'utilisateur à poser sa prochaine question.\n\n"
    "LANGUE : Détecte automatiquement la langue du message de "
    "l'utilisateur et réponds TOUJOURS dans cette même langue. "
    "Si le message est en arabe → réponds en arabe. "
    "Si en anglais → réponds en anglais. "
    "Si en français → réponds en français. "
    "Si en espagnol → réponds en espagnol. "
    "Adapte aussi le ton et les formules de politesse à la culture "
    "de la langue détectée.\n\n"
    "Sois toujours poli, chaleureux et concis."
)

# 10 echanges = 20 messages (user + assistant).
MAX_HISTORY_MESSAGES = 20

# --- Memoire conversationnelle : Redis (Upstash) + fallback en memoire ---
REDIS_URL = os.getenv("REDIS_URL")
redis_client = None


def get_redis():
    global redis_client
    if redis_client is None and REDIS_URL:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return redis_client


# Fallback si Redis non disponible : session_id -> [{"role", "content"}, ...]
CONVERSATION_HISTORY: dict[str, list[dict[str, str]]] = {}


def get_history(session_id: str) -> list[dict]:
    r = get_redis()
    if r:
        try:
            data = r.get(f"chat:{session_id}")
            return json.loads(data) if data else []
        except Exception:
            pass
    return CONVERSATION_HISTORY.get(session_id, [])


def save_history(session_id: str, history: list[dict]) -> None:
    # Garde max 20 messages
    if len(history) > MAX_HISTORY_MESSAGES:
        history = history[-MAX_HISTORY_MESSAGES:]
    r = get_redis()
    if r:
        try:
            # Expire apres 24 heures
            r.setex(f"chat:{session_id}", 86400, json.dumps(history))
            return
        except Exception:
            pass
    CONVERSATION_HISTORY[session_id] = history


# --- Compteur de refus consecutifs -> handoff vers agent humain ---
REFUSAL_COUNTS: dict[str, int] = {}  # fallback en memoire : session_id -> compteur


def _get_refusal_count(session_id: str) -> int:
    r = get_redis()
    if r:
        try:
            val = r.get(f"refusals:{session_id}")
            return int(val) if val else 0
        except Exception:
            pass
    return REFUSAL_COUNTS.get(session_id, 0)


def _set_refusal_count(session_id: str, count: int) -> None:
    r = get_redis()
    if r:
        try:
            r.setex(f"refusals:{session_id}", 86400, str(count))
            return
        except Exception:
            pass
    REFUSAL_COUNTS[session_id] = count


REFUSAL_PHRASES = [
    "je ne sais pas",
    "je n'ai pas trouvé",
    "pas dans notre documentation",
    "ne peut pas vous aider",
    "ne suis pas en mesure",
    "hors de ma compétence",
    "uniquement l'assistant de novamart",
    "je suis uniquement",
    "[no_context]",
]


def _is_refusal(response: str) -> bool:
    response_lower = response.lower()
    return any(phrase in response_lower for phrase in REFUSAL_PHRASES)


def _track_refusal(session_id: str, answer: str) -> str:
    """Detecte les refus consecutifs (marqueur [NO_CONTEXT] ou tournures de
    refus generees par le LLM) et ajoute [HANDOFF] une fois le seuil atteint.
    Remet le compteur a 0 des qu'une reponse normale est generee.
    """
    if _is_refusal(answer):
        count = _get_refusal_count(session_id) + 1
        _set_refusal_count(session_id, count)
        if count >= HANDOFF_THRESHOLD:
            answer = f"{answer} [HANDOFF]"
    else:
        _set_refusal_count(session_id, 0)
    return answer


# Analytics basique (en memoire process).
ANALYTICS: dict = {
    "total_conversations": 0,
    "total_messages": 0,
    "questions": [],  # 100 dernieres questions
    "response_times": [],  # 100 derniers temps de reponse (secondes)
    "hourly_conversations": {},  # {"2026-09-10 14": 5, ...}
    "start_time": None,  # timestamp du demarrage du serveur
}


def _agent_runtime_client():
    """Client boto3 pour l'API de retrieval de la Knowledge Base."""
    import boto3

    return boto3.client("bedrock-agent-runtime", **config.boto3_client_kwargs())


def _retrieve(message: str) -> list[str]:
    """Etape 1 — recupere jusqu'a 5 passages pertinents depuis la Knowledge Base."""
    client = _agent_runtime_client()
    # La KB NovaMart est une "managed knowledge base" : elle exige
    # managedSearchConfiguration (vectorSearchConfiguration est rejete par l'API).
    response = client.retrieve(
        knowledgeBaseId=config.BEDROCK_KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": message},
        retrievalConfiguration={"managedSearchConfiguration": {"numberOfResults": 5}},
    )

    passages: list[str] = []
    for result in response.get("retrievalResults", []):
        text = result.get("content", {}).get("text", "").strip()
        if text:
            passages.append(text)
    return passages


def _build_messages(message: str, passages: list[str], history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Assemble les messages envoyes a Claude : historique + tour actuel + contexte RAG."""
    contexte = "\n\n---\n\n".join(passages)
    user_message = f"Contexte:\n{contexte}\n\nQuestion: {message}"
    return list(history) + [{"role": "user", "content": user_message}]


def _generate(message: str, passages: list[str], history: list[dict[str, str]]) -> str:
    """Etape 2 — genere une reponse a partir du contexte + historique via l'API Anthropic."""
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=GENERATION_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=_build_messages(message, passages, history),
    )

    return response.content[0].text


def chat(message: str, session_id: str) -> str:
    """Repond a `message` via retrieve() (AWS) puis l'API Anthropic, avec memoire.

    Si les credentials AWS ou l'ID de Knowledge Base ne sont pas configures,
    retourne une reponse mockee pour permettre le developpement local.
    """
    if not config.aws_credentials_configured():
        return MOCK_RESPONSE

    start = time.time()
    history = get_history(session_id)

    if ANALYTICS["start_time"] is None:
        ANALYTICS["start_time"] = datetime.datetime.now().isoformat()

    ANALYTICS["total_messages"] += 1
    if not history:  # historique vide avant ajout -> premier message de la session
        ANALYTICS["total_conversations"] += 1
        hour_key = datetime.datetime.now().strftime("%Y-%m-%d %H")
        ANALYTICS["hourly_conversations"][hour_key] = (
            ANALYTICS["hourly_conversations"].get(hour_key, 0) + 1
        )
    ANALYTICS["questions"].append(message)
    del ANALYTICS["questions"][:-100]

    history.append({"role": "user", "content": message})

    try:
        passages = _retrieve(message)
        if not passages:
            answer = NO_CONTEXT_RESPONSE
        else:
            # history[:-1] = echanges precedents (sans le message qu'on vient d'ajouter).
            answer = _generate(message, passages, history[:-1])

        answer = _track_refusal(session_id, answer)

        history.append({"role": "assistant", "content": answer})
        save_history(session_id, history)
        return answer

    except Exception as exc:  # pragma: no cover - depend de l'environnement AWS
        # Rien n'a ete persiste (pas d'appel a save_history) -> pas de tour orphelin.
        return f"Je suis NovaMart Support. Une erreur est survenue : {exc}"

    finally:
        ANALYTICS["response_times"].append(round(time.time() - start, 3))
        del ANALYTICS["response_times"][:-100]


def stream_chat(message: str, session_id: str) -> Generator[str, None, None]:
    """Version streaming de la generation (endpoint SSE /chat/stream), avec memoire.

    Yield le texte de la reponse au fil de l'eau (fragments de tokens).
    """
    import anthropic

    history = get_history(session_id)

    passages = _retrieve(message)
    if not passages:
        answer = _track_refusal(session_id, NO_CONTEXT_RESPONSE)
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": answer})
        save_history(session_id, history)
        yield answer
        return

    # history ne contient pas encore le tour actuel -> on le passe tel quel.
    messages = _build_messages(message, passages, history)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    full = ""
    with client.messages.stream(
        model=GENERATION_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            full += text
            yield text

    _track_refusal(session_id, full)  # reponse normale -> remet le compteur a 0

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": full})
    save_history(session_id, history)
