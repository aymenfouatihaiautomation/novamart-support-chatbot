import http from 'k6/http';
import { check, sleep } from 'k6';

// Stress test — pousse jusqu'aux limites
export const options = {
    stages: [
        { duration: '30s', target: 10 },
        { duration: '1m', target: 20 },
        { duration: '1m', target: 30 },
        { duration: '30s', target: 0 },
    ],
    thresholds: {
        http_req_duration: ['p(95)<60000'],
        http_req_failed: ['rate<0.5'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
    const payload = JSON.stringify({
        message: 'Délais de livraison France ?',
        session_id: `stress-${__VU}-${__ITER}`,
    });

    const params = {
        headers: { 'Content-Type': 'application/json' },
        timeout: '90s',
    };

    const res = http.post(`${BASE_URL}/chat`, payload, params);

    check(res, {
        'status is 200': (r) => r.status === 200,
    });

    sleep(1);
}
