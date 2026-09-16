import http from 'k6/http';
import { check, sleep } from 'k6';

// Smoke test — 1 utilisateur, 1 minute
export const options = {
    vus: 1,
    duration: '1m',
    thresholds: {
        http_req_duration: ['p(95)<30000'], // 95% des requêtes < 30s
        http_req_failed: ['rate<0.1'],       // moins de 10% d'erreurs
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
    const payload = JSON.stringify({
        message: 'Quels sont vos délais de livraison ?',
        session_id: `smoke-${__VU}-${__ITER}`,
    });

    const params = {
        headers: { 'Content-Type': 'application/json' },
        timeout: '60s',
    };

    const res = http.post(`${BASE_URL}/chat`, payload, params);

    check(res, {
        'status is 200': (r) => r.status === 200,
        'response has content': (r) => r.body.length > 0,
        'no error in response': (r) => !r.json('response').includes('erreur'),
    });

    sleep(2);
}
