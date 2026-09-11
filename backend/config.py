"""Chargement des variables d'environnement pour la configuration boto3 / Bedrock."""

import os

from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "eu-west-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

BEDROCK_KNOWLEDGE_BASE_ID = os.getenv("BEDROCK_KNOWLEDGE_BASE_ID")
BEDROCK_MODEL_ARN = os.getenv(
    "BEDROCK_MODEL_ARN",
    "arn:aws:bedrock:eu-west-1::foundation-model/anthropic.claude-sonnet-4-5",
)

# Modele utilise par converse() pour la generation. Claude Sonnet 4.5 sur Bedrock
# n'est disponible qu'via un inference profile (pas d'acces on-demand), d'ou le
# prefixe de region "us.".
BEDROCK_GENERATION_MODEL_ID = os.getenv(
    "BEDROCK_GENERATION_MODEL_ID",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
)

# Cle API Anthropic — utilisee pour l'etape de generation (hors Bedrock).
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# SMTP (Gmail) — utilise par /contact pour le handoff vers un agent humain.
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL")


def _is_real_value(value: str | None) -> bool:
    """True si la valeur existe, n'est pas vide et n'est pas un placeholder 'your_...'."""
    return bool(value) and not value.startswith("your_")


def aws_credentials_configured() -> bool:
    """Retourne True si les credentials AWS et l'ID de la Knowledge Base sont presents.

    Les valeurs vides ou de type placeholder ('your_key_here', ...) sont
    considerees comme non configurees.
    """
    return all(
        [
            _is_real_value(AWS_ACCESS_KEY_ID),
            _is_real_value(AWS_SECRET_ACCESS_KEY),
            _is_real_value(BEDROCK_KNOWLEDGE_BASE_ID),
        ]
    )


def boto3_client_kwargs() -> dict:
    """Arguments communs pour instancier un client boto3."""
    kwargs = {"region_name": AWS_REGION}
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY
    return kwargs
