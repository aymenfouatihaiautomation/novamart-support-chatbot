# Tests de charge — NovaMart Chatbot

## Prérequis
- k6 installé : https://k6.io/docs/get-started/installation/
- Le serveur doit tourner localement ou sur Railway

## Lancer les tests

### Smoke test (vérification de base)
k6 run tests/load/smoke-test.js

### Load test (charge normale)
k6 run tests/load/load-test.js

### Stress test (limites du système)
k6 run tests/load/stress-test.js

### Tester contre Railway (production)
k6 run -e BASE_URL=https://web-production-fb44b.up.railway.app tests/load/smoke-test.js

## Métriques importantes
- p95 < 30s : 95% des requêtes répondent en moins de 30 secondes
- error rate < 10% : moins de 10% d'erreurs acceptables
