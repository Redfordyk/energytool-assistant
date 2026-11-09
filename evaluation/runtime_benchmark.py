"""Measure local chat API latency with synthetic, non-personal test traffic.

This measures the configured deployment path. If the model gateway is absent,
the report explicitly describes the result as a fallback-only measurement.
"""
import argparse
import asyncio
import json
import math
import subprocess
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.rag import FALLBACK


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 2)


async def benchmark(base_url, request_count=20, concurrency=4, timeout=30):
    if not 1 <= request_count <= 25:
        raise ValueError('request_count must be between 1 and 25 (shared rate-limit budget)')
    if not 1 <= concurrency <= request_count:
        raise ValueError('concurrency must be between 1 and request_count')

    base_url = base_url.rstrip('/')
    try:
        code_revision = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[1],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        code_revision = None
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        try:
            readiness = await client.get(base_url + '/health/ready')
            api_ready = readiness.status_code == 200
        except httpx.HTTPError:
            api_ready = False

        sessions = []
        setup_statuses = Counter()
        for _ in range(request_count):
            try:
                response = await client.post(base_url + '/api/sessions')
                setup_statuses[str(response.status_code)] += 1
                if response.status_code == 200:
                    sessions.append(response.json())
            except (httpx.HTTPError, ValueError):
                setup_statuses['error'] += 1

        semaphore = asyncio.Semaphore(concurrency)
        samples = []

        async def send(session):
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.post(
                        base_url + f"/api/sessions/{session['id']}/messages",
                        headers={
                            'Authorization': 'Bearer ' + session['token'],
                            'Idempotency-Key': uuid.uuid4().hex,
                        },
                        json={
                            'text': 'Нужна консультация по учёту электроэнергии.',
                            'session_version': 0,
                        },
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    fallback = False
                    trace_id = None
                    if response.status_code == 200:
                        payload = response.json()
                        fallback = payload.get('answer') == FALLBACK
                        trace_id = payload.get('trace_id')
                    return {'status': str(response.status_code), 'latency_ms': elapsed_ms,
                            'fallback': fallback, 'trace_id_present': bool(trace_id)}
                except (httpx.HTTPError, ValueError, KeyError):
                    return {'status': 'error', 'latency_ms': (time.perf_counter() - started) * 1000,
                            'fallback': False, 'trace_id_present': False}

        results = await asyncio.gather(*(send(session) for session in sessions))
        latency = [item['latency_ms'] for item in results if item['status'] == '200']
        statuses = Counter(item['status'] for item in results)
        async def close(session):
            try:
                await client.post(base_url + f"/api/sessions/{session['id']}/close",
                                  headers={'Authorization': 'Bearer ' + session['token']})
            except httpx.HTTPError:
                pass
        await asyncio.gather(*(close(session) for session in sessions))
        report = {
            'measured_at_utc': datetime.now(timezone.utc).isoformat(),
            'target': base_url,
            'code_revision': code_revision,
            'mode': 'service_ready_path' if api_ready else 'service_unready_path',
            'service_readiness': api_ready,
            'requested': request_count,
            'sessions_created': len(sessions),
            'concurrency': concurrency,
            'setup_statuses': dict(setup_statuses),
            'chat_statuses': dict(statuses),
            'successful_responses': len(latency),
            'fallback_responses': sum(item['fallback'] for item in results),
            'trace_ids_returned': sum(item['trace_id_present'] for item in results),
            'latency_ms': {
                'p50': percentile(latency, .50),
                'p95': percentile(latency, .95),
                'p99': percentile(latency, .99),
            },
            'limitations': [
                'Synthetic questions only; no user data.',
                'Latency is measured at the HTTP chat endpoint.',
                'When service_readiness is false, this does not verify a complete configured response path.',
                'No first-token latency is available because responses are not streamed.',
            ],
        }
        return report


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--requests', type=int, default=20)
    parser.add_argument('--concurrency', type=int, default=4)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--output')
    args = parser.parse_args()
    report = await benchmark(args.url, args.requests, args.concurrency, args.timeout)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as target:
            target.write(rendered + '\n')
    print(rendered)


if __name__ == '__main__':
    asyncio.run(main())
