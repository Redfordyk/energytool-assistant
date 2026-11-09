"""Optional semantic retrieval and reranking over the supplied knowledge only."""

import hashlib
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from typing import Protocol

import httpx

_CACHE: OrderedDict[tuple[str, str, bytes, bytes], tuple[tuple[float, ...], ...]] = OrderedDict()
_LOCK = threading.Lock()
_MAX_CACHED_INDEXES = 2
_BATCH_SIZE = 32


class RankedChunk(Protocol):
    source: str
    text: str

    def __hash__(self) -> int: ...


def bounded_timeout(deadline, cap):
    if deadline is None:
        return cap
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Request time budget exhausted')
    return min(cap, remaining)


def _vectors(client, base, key, model, texts, deadline=None):
    result = client.post(
        base + '/embeddings',
        headers={'Authorization': 'Bearer ' + key},
        json={'model': model, 'input': texts},
        timeout=bounded_timeout(deadline, 6),
    )
    result.raise_for_status()
    data = result.json()['data']
    if not isinstance(data, list) or len(data) != len(texts):
        raise ValueError('Incomplete embedding response')
    vectors: list[tuple[float, ...] | None] = [None] * len(texts)
    for item in data:
        index, values = item['index'], item['embedding']
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(texts)
            or vectors[index] is not None
        ):
            raise ValueError('Invalid embedding index')
        if not isinstance(values, list) or not values:
            raise ValueError('Invalid embedding vector')
        vector = tuple(float(value) for value in values)
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(norm) or norm == 0:
            raise ValueError('Invalid embedding norm')
        vectors[index] = tuple(value / norm for value in vector)
    complete_vectors = [vector for vector in vectors if vector is not None]
    if (
        len(complete_vectors) != len(vectors)
        or len({len(vector) for vector in complete_vectors}) != 1
    ):
        raise ValueError('Inconsistent embedding vectors')
    return tuple(complete_vectors)


def _index(client, base, key, model, chunks, deadline=None):
    digest = hashlib.sha256()
    for chunk in chunks:
        for part in (chunk.source, chunk.text):
            encoded = part.encode('utf-8')
            digest.update(len(encoded).to_bytes(8, 'big'))
            digest.update(encoded)
    cache_key = (base, model, hashlib.sha256(key.encode()).digest(), digest.digest())
    # Serialize index builds so concurrent questions do not re-embed the corpus.
    acquired = _LOCK.acquire(timeout=bounded_timeout(deadline, 6))
    if not acquired:
        raise TimeoutError('Index build is busy')
    try:
        if cache_key in _CACHE:
            _CACHE.move_to_end(cache_key)
            return _CACHE[cache_key]
        vectors = []
        for start in range(0, len(chunks), _BATCH_SIZE):
            texts = [
                chunk.source + '\n' + chunk.text for chunk in chunks[start : start + _BATCH_SIZE]
            ]
            vectors.extend(_vectors(client, base, key, model, texts, deadline))
        if vectors and len({len(vector) for vector in vectors}) != 1:
            raise ValueError('Inconsistent index dimensions')
        _CACHE[cache_key] = tuple(vectors)
        while len(_CACHE) > _MAX_CACHED_INDEXES:
            _CACHE.popitem(last=False)
        return _CACHE[cache_key]
    finally:
        _LOCK.release()


def semantic_rank(client, base, key, model, question, chunks, limit, deadline=None):
    vectors = _index(client, base, key, model, chunks, deadline)
    query = _vectors(client, base, key, model, [question], deadline)[0]
    if any(len(vector) != len(query) for vector in vectors):
        raise ValueError('Query dimension differs from index')
    scores = [(sum(a * b for a, b in zip(query, vector)), i) for i, vector in enumerate(vectors)]
    scores.sort(key=lambda pair: (-pair[0], pair[1]))
    return [chunks[index] for _, index in scores[:limit]]


def fuse(
    rankings: Sequence[Sequence[RankedChunk]], limit: int, rank_constant: int = 60
) -> list[RankedChunk]:
    scores: dict[RankedChunk, float] = {}
    for ranking in rankings:
        seen = set()
        for rank, chunk in enumerate(ranking, start=1):
            if chunk not in seen:
                scores[chunk] = scores.get(chunk, 0) + 1 / (rank_constant + rank)
                seen.add(chunk)
    return sorted(scores, key=lambda chunk: (-scores[chunk], chunk.source, chunk.text))[:limit]


def rerank(client, base, key, model, question, chunks, limit, deadline=None):
    response = client.post(
        base + '/rerank',
        headers={'Authorization': 'Bearer ' + key},
        json={
            'model': model,
            'query': question,
            'documents': [chunk.source + '\n' + chunk.text for chunk in chunks],
            'top_n': min(limit, len(chunks)),
        },
        timeout=bounded_timeout(deadline, 3),
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get('results', payload.get('data'))
    if not isinstance(items, list) or not items:
        raise ValueError('Empty reranker response')
    indices = [item['index'] for item in items]
    if any(
        isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(chunks) for i in indices
    ) or len(set(indices)) != len(indices):
        raise ValueError('Invalid reranker indices')
    return [chunks[i] for i in indices[:limit]]


RETRIEVAL_ERRORS = (httpx.HTTPError, KeyError, TypeError, ValueError, OverflowError, TimeoutError)
