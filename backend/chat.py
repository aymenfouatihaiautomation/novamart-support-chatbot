"""Logique du chatbot NovaMart — RAG hybride avec memoire conversationnelle.

Approche en deux etapes :
  1. retrieve() (AWS Bedrock) -> recupere les passages de la Knowledge Base.
  2. Groq (Llama 3.3 70B)     -> genere une reponse a partir du contexte + historique.

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

# "llama-3.3-70b-versatile" n'existe plus dans le catalogue Groq (modele retire,
# 404 model_not_found). Remplace par le plus proche disponible sur ce compte.
GENERATION_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = (
    "Tu es l'assistant support de NovaMart, une boutique "
    "d'électronique en ligne. Tu as deux modes de réponse :\n\n"
    "1. QUESTIONS SUR NOVAMART : Si le message est une question "
    "sur les produits, livraisons, retours ou politiques de NovaMart, "
    "réponds UNIQUEMENT en te basant sur le contexte fourni.\n\n"
    "REFUS STRICT : Si la question ne concerne pas NovaMart "
    "(culture générale, recettes, politique, sport, etc.), "
    "tu DOIS obligatoirement répondre UNIQUEMENT cette phrase : "
    "'Je suis uniquement l assistant NovaMart. Je ne peux pas "
    "répondre à cette question.' "
    "N'ajoute RIEN d'autre. Pas d'explication, pas de suggestion, "
    "pas de reformulation. JUSTE cette phrase exacte.\n\n"
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


# --- Analytics persistees dans Redis (survivent aux redemarrages) ---
ANALYTICS_KEY = "novamart:analytics"


def get_analytics() -> dict:
    """Récupère les analytics depuis Redis ou retourne les valeurs par défaut."""
    r = get_redis()
    if r:
        try:
            data = r.get(ANALYTICS_KEY)
            if data:
                return json.loads(data)
        except Exception:
            pass
    # Fallback mémoire locale
    return {
        "total_conversations": 0,
        "total_messages": 0,
        "questions": [],
        "response_times": [],
        "hourly_conversations": {},
        "start_time": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }


def save_analytics(analytics: dict) -> None:
    """Sauvegarde les analytics dans Redis sans expiration."""
    r = get_redis()
    if r:
        try:
            r.set(ANALYTICS_KEY, json.dumps(analytics))
            return
        except Exception:
            pass


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


def _build_retrieval_query(message: str, history: list[dict]) -> str:
    """Enrichit la requête de recherche avec le contexte récent."""
    # Si message court (moins de 10 mots) et historique disponible
    words = message.split()
    if len(words) <= 6 and len(history) >= 2:
        # Récupère le dernier message utilisateur comme contexte
        last_user_msgs = [
            m["content"] for m in history[-4:]
            if m["role"] == "user"
        ]
        if last_user_msgs:
            # Combine contexte + question actuelle
            context = last_user_msgs[-1]
            return f"{context} {message}"
    return message


def _build_messages(message: str, passages: list[str], history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Assemble les messages envoyes a Claude : historique + tour actuel + contexte RAG."""
    contexte = "\n\n---\n\n".join(passages)
    user_message = f"Contexte:\n{contexte}\n\nQuestion: {message}"
    return list(history) + [{"role": "user", "content": user_message}]


def _generate(message: str, passages: list[str], history: list[dict[str, str]]) -> str:
    """Etape 2 — genere une reponse a partir du contexte + historique via l'API Groq."""
    from groq import Groq

    client = Groq(api_key=config.GROQ_API_KEY)
    response = client.chat.completions.create(
        model=GENERATION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
        ] + _build_messages(message, passages, history),
        max_tokens=1024,
        temperature=0.7,
    )

    return response.choices[0].message.content


def chat(message: str, session_id: str) -> str:
    """Repond a `message` via retrieve() (AWS) puis l'API Groq, avec memoire.

    Si les credentials AWS ou l'ID de Knowledge Base ne sont pas configures,
    retourne une reponse mockee pour permettre le developpement local.
    """
    if not config.aws_credentials_configured():
        return MOCK_RESPONSE

    start = time.time()
    history = get_history(session_id)
    analytics = get_analytics()

    if analytics["start_time"] is None:
        analytics["start_time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    analytics["total_messages"] += 1
    if not history:  # historique vide avant ajout -> premier message de la session
        analytics["total_conversations"] += 1
        hour_key = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H")
        analytics["hourly_conversations"][hour_key] = (
            analytics["hourly_conversations"].get(hour_key, 0) + 1
        )
    analytics["questions"].append(message)
    del analytics["questions"][:-100]

    history.append({"role": "user", "content": message})

    try:
        # history[:-1] = echanges precedents (sans le message qu'on vient d'ajouter
        # a la ligne ci-dessus) : evite que la requete s'auto-duplique avec elle-meme.
        retrieval_query = _build_retrieval_query(message, history[:-1])
        passages = _retrieve(retrieval_query)
        if not passages:
            answer = NO_CONTEXT_RESPONSE
        else:
            answer = _generate(message, passages, history[:-1])

        answer = _track_refusal(session_id, answer)

        history.append({"role": "assistant", "content": answer})
        save_history(session_id, history)
        return answer

    except Exception as exc:  # pragma: no cover - depend de l'environnement AWS
        # Rien n'a ete persiste (pas d'appel a save_history) -> pas de tour orphelin.
        return f"Je suis NovaMart Support. Une erreur est survenue : {exc}"

    finally:
        analytics["response_times"].append(round(time.time() - start, 3))
        del analytics["response_times"][:-100]
        save_analytics(analytics)


def stream_chat(message: str, session_id: str) -> Generator[str, None, None]:
    """Version streaming de la generation (endpoint SSE /chat/stream), avec memoire.

    Yield le texte de la reponse au fil de l'eau (fragments de tokens).
    """
    from groq import Groq

    history = get_history(session_id)

    # Ici history ne contient pas encore le tour actuel -> pas de decalage a gerer.
    retrieval_query = _build_retrieval_query(message, history)
    passages = _retrieve(retrieval_query)
    if not passages:
        answer = _track_refusal(session_id, NO_CONTEXT_RESPONSE)
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": answer})
        save_history(session_id, history)
        yield answer
        return

    # history ne contient pas encore le tour actuel -> on le passe tel quel.
    messages = _build_messages(message, passages, history)

    client = Groq(api_key=config.GROQ_API_KEY)
    stream = client.chat.completions.create(
        model=GENERATION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
        ] + messages,
        max_tokens=1024,
        temperature=0.7,
        stream=True,
    )

    full = ""
    for chunk in stream:
        text = chunk.choices[0].delta.content or ""
        if text:
            full += text
            yield text

    tracked = _track_refusal(session_id, full)
    if tracked != full:
        # _track_refusal a ajoute un marqueur (ex. " [HANDOFF]") apres coup :
        # on l'envoie comme dernier chunk pour que le frontend le detecte.
        yield tracked[len(full):]
        full = tracked

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": full})
    save_history(session_id, history)
