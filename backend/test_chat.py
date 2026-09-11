import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from main import app

client = TestClient(app)

# ============================================================
# Tests endpoints de base
# ============================================================

def test_health():
    """L'API doit retourner status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_stats_structure():
    """L'endpoint stats doit retourner les bonnes clés."""
    response = client.get("/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total_conversations" in data
    assert "total_messages" in data
    assert "avg_response_time_seconds" in data
    assert "top_questions" in data

def test_dashboard_returns_html():
    """Le dashboard doit retourner du HTML."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "NovaMart" in response.text

def test_contact_missing_fields():
    """Contact sans champs requis doit retourner 422."""
    response = client.post("/contact", json={})
    assert response.status_code == 422

def test_contact_without_smtp_config():
    """Contact sans config SMTP doit retourner success:False."""
    with patch("config.SMTP_EMAIL", None), \
         patch("config.SMTP_PASSWORD", None), \
         patch("config.SUPPORT_EMAIL", None):
        response = client.post("/contact", json={
            "name": "Test",
            "email": "test@test.com",
            "message": "Test message",
            "session_id": "test"
        })
    assert response.status_code == 200
    assert response.json()["success"] == False

# ============================================================
# Tests du pipeline RAG (avec mock AWS + Anthropic)
# ============================================================

MOCK_PASSAGES = [
    "Les délais de livraison en France sont de 2 à 4 jours ouvrés.",
    "La livraison express est disponible en 1 à 2 jours ouvrés."
]

def test_chat_mock_response():
    """Le chatbot doit retourner une réponse non vide avec mock."""
    with patch("chat._retrieve", return_value=MOCK_PASSAGES), \
         patch("chat._generate", return_value="Délais : 2 à 4 jours ouvrés."):
        response = client.post("/chat", json={
            "message": "Quels sont vos délais de livraison ?",
            "session_id": "test-unit-1"
        })
    assert response.status_code == 200
    data = response.json()
    assert "response" in data
    assert "session_id" in data
    assert len(data["response"]) > 0

def test_chat_no_context_response():
    """Sans passages, le chatbot doit retourner NO_CONTEXT_RESPONSE."""
    with patch("chat._retrieve", return_value=[]):
        response = client.post("/chat", json={
            "message": "Question sans contexte",
            "session_id": "test-unit-2"
        })
    assert response.status_code == 200
    assert "pas trouvé" in response.json()["response"].lower() or \
           "no_context" in response.json()["response"].lower() or \
           "documentation" in response.json()["response"].lower()

def test_chat_session_id_generated():
    """Un session_id vide doit être auto-généré."""
    with patch("chat._retrieve", return_value=MOCK_PASSAGES), \
         patch("chat._generate", return_value="Réponse test"):
        response = client.post("/chat", json={
            "message": "Test",
            "session_id": ""
        })
    assert response.status_code == 200
    assert len(response.json()["session_id"]) > 0

def test_chat_french_language():
    """Le chatbot doit répondre en français sur une question française."""
    with patch("chat._retrieve", return_value=MOCK_PASSAGES), \
         patch("chat._generate", return_value="Bonjour ! Les délais sont de 2 à 4 jours."):
        response = client.post("/chat", json={
            "message": "Quels sont vos délais ?",
            "session_id": "test-fr"
        })
    assert response.status_code == 200
    # La réponse mockée contient du français
    assert "délais" in response.json()["response"].lower() or \
           "bonjour" in response.json()["response"].lower()
