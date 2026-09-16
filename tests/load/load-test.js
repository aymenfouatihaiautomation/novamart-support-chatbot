import http from 'k6/http';
import { check, sleep } from 'k6';

// Load test — montée progressive jusqu'à 10 utilisateurs
export const options = {
    stages: [
        { duration: '30s', target: 5 },   // montée à 5 users
        { duration: '1m', target: 10 },   // maintien à 10 users
        { duration: '30s', target: 0 },   // descente
    ],
    thresholds: {
        http_req_duration: ['p(95)<45000'],
        http_req_failed: ['rate<0.2'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

const QUESTIONS = [
    'Quels sont vos délais de livraison ?',
    'Comment faire un retour ?',
    'Quels sont vos produits disponibles ?',
    'Livraison gratuite à partir de quel montant ?',
    'Quel est le délai de remboursement ?',
];

export default function () {
    const question = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];

    const payload = JSON.stringify({
        message: question,
        session_id: `load-${__VU}-${__ITER}`,
    });

    const params = {
        headers: { 'Content-Type': 'application/json' },
        timeout: '60s',
    };

    const res = http.post(`${BASE_URL}/chat`, payload, params);

    check(res, {
        'status is 200': (r) => r.status === 200,
        'response not empty': (r) => r.body.length > 0,
    });

    sleep(3);
}
