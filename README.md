# NovaMart Support Chatbot

Chatbot de support client pour **NovaMart** (e-commerce electronique & accessoires),
propulse par Amazon Bedrock Knowledge Bases (RetrieveAndGenerate).

## Structure

```
novamart-support-chatbot/
├── backend/     API FastAPI + logique Bedrock
├── frontend/    Widget de chat embeddable + page de demo
├── documents/   Base de connaissances (FAQ, politiques, catalogue) en .txt
└── scripts/     Utilitaires (upload S3)
```

## Demarrage rapide

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env   # puis renseigner les valeurs
python main.py
```

L'API ecoute sur `http://localhost:8000` (`GET /health`, `POST /chat`).

Sans credentials AWS valides, `/chat` renvoie une reponse mockee — utile pour
developper le frontend sans compte AWS.

## Frontend

Ouvrir `frontend/index.html` dans un navigateur (servi via un petit serveur
statique de preference) pour tester le widget.

## Documents & Knowledge Base

1. Editer les fichiers de `documents/`.
2. `python scripts/upload_to_s3.py --bucket <bucket> --prefix novamart/`
3. Resynchroniser la data source de la Knowledge Base Bedrock.

> Les documents sont en texte brut pour l'instant. Conversion PDF possible plus tard.

## TODO

- [ ] Placeholder — a completer.
