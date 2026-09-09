"""Logique du chatbot NovaMart — RAG hybride avec memoire conversationnelle.

Approche en deux etapes :
  1. retrieve() (AWS Bedrock) -> recupere les passages de la Knowledge Base.
  2. Anthropic Messages API   -> genere une reponse a partir du contexte + historique.

L'historique par session est garde en memoire process (CONVERSATION_HISTORY),
limite aux 10 derniers echanges pour rester sous le context window.
"""

from __future__ import annotations

from typing import Generator

import config

MOCK_RESPONSE = "Je suis NovaMart Support. [MOCK - AWS not configured]"
NO_CONTEXT_RESPONSE = (
    "Je n'ai pas trouve d'information sur ce sujet dans notre documentation."
)

GENERATION_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "Tu es l'assistant support de NovaMart, une boutique d'électronique "
    "en ligne. Tu as deux modes de réponse :\n\n"
    "1. QUESTIONS SUR NOVAMART : Si le message est une vraie question sur "
    "les produits, livraisons, retours ou politiques de NovaMart, réponds "
    "UNIQUEMENT en te basant sur le contexte fourni. Si l'information "
    "n'est pas dans le contexte, dis-le honnêtement.\n\n"
    "2. MESSAGES CONVERSATIONNELS : Si le message est une réponse courte "
    "(oui, non, ok, merci, je comprends, j'ai une autre question, etc.) "
    "ou une continuation naturelle de la conversation, réponds de façon "
    "naturelle et chaleureuse sans chercher dans les documents. "
    "Invite l'utilisateur à poser sa prochaine question.\n\n"
    "Sois toujours poli, chaleureux et concis. "
    "Tu parles français uniquement."
)

# Memoire conversationnelle : session_id -> [{"role": str, "content": str}, ...]
CONVERSATION_HISTORY: dict[str, list[dict[str, str]]] = {}

# 10 echanges = 20 messages (user + assistant).
MAX_HISTORY_MESSAGES = 20


def _trim_history(history: list[dict[str, str]]) -> None:
    """Ne garde que les MAX_HISTORY_MESSAGES derniers messages (in place)."""
    if len(history) > MAX_HISTORY_MESSAGES:
        del history[:-MAX_HISTORY_MESSAGES]


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

    history = CONVERSATION_HISTORY.setdefault(session_id, [])
    history.append({"role": "user", "content": message})

    try:
        passages = _retrieve(message)
        if not passages:
            answer = NO_CONTEXT_RESPONSE
        else:
            # history[:-1] = echanges precedents (sans le message qu'on vient d'ajouter).
            answer = _generate(message, passages, history[:-1])

        history.append({"role": "assistant", "content": answer})
        _trim_history(history)
        return answer

    except Exception as exc:  # pragma: no cover - depend de l'environnement AWS
        if history and history[-1]["role"] == "user":
            history.pop()  # ne pas laisser un tour utilisateur orphelin
        return f"Je suis NovaMart Support. Une erreur est survenue : {exc}"


def stream_chat(message: str, session_id: str) -> Generator[str, None, None]:
    """Version streaming de la generation (endpoint SSE /chat/stream), avec memoire.

    Yield le texte de la reponse au fil de l'eau (fragments de tokens).
    """
    import anthropic

    history = CONVERSATION_HISTORY.setdefault(session_id, [])

    passages = _retrieve(message)
    if not passages:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": NO_CONTEXT_RESPONSE})
        _trim_history(history)
        yield NO_CONTEXT_RESPONSE
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

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": full})
    _trim_history(history)
