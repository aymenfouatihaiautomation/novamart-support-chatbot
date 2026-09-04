"""Logique du chatbot NovaMart — RAG hybride.

Approche en deux etapes :
  1. retrieve() (AWS Bedrock) -> recupere les passages de la Knowledge Base.
  2. Anthropic Messages API   -> genere une reponse a partir du contexte assemble.
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
    "Tu es l'assistant support de NovaMart, une boutique d'electronique en ligne. "
    "Reponds UNIQUEMENT en te basant sur le contexte fourni. Si la reponse n'est "
    "pas dans le contexte, dis que tu ne sais pas."
)


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


def _generate(message: str, passages: list[str]) -> str:
    """Etape 2 — genere une reponse a partir du contexte via l'API Anthropic."""
    import anthropic

    contexte = "\n\n---\n\n".join(passages)
    user_message = f"Contexte:\n{contexte}\n\nQuestion: {message}"

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=GENERATION_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    return response.content[0].text


def stream_chat(message: str) -> Generator[str, None, None]:
    """Version streaming de la generation, pour l'endpoint SSE /chat/stream.

    Yield le texte de la reponse au fil de l'eau (fragments de tokens).
    """
    import anthropic

    passages = _retrieve(message)
    if not passages:
        yield NO_CONTEXT_RESPONSE
        return

    contexte = "\n\n---\n\n".join(passages)
    user_message = f"Contexte:\n{contexte}\n\nQuestion: {message}"

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    with client.messages.stream(
        model=GENERATION_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text in stream.text_stream:
            yield text


def chat(message: str, session_id: str) -> str:
    """Repond a `message` via retrieve() (AWS) puis l'API Anthropic.

    Si les credentials AWS ou l'ID de Knowledge Base ne sont pas configures,
    retourne une reponse mockee pour permettre le developpement local.
    """
    if not config.aws_credentials_configured():
        return MOCK_RESPONSE

    try:
        passages = _retrieve(message)
        if not passages:
            return NO_CONTEXT_RESPONSE
        return _generate(message, passages)

    except Exception as exc:  # pragma: no cover - depend de l'environnement AWS
        return f"Je suis NovaMart Support. Une erreur est survenue : {exc}"
