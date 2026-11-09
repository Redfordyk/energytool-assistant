import httpx

from core.hybrid import bounded_timeout, fuse, rerank, semantic_rank
from core.rag import Chunk, answer, verify_claims


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def post(self, url, headers, json, **kwargs):
        self.calls.append((url, json))
        payload = self.handler(url, json)
        return httpx.Response(200, request=httpx.Request('POST', url), json=payload)


def test_semantic_search_finds_nonlexical_match_and_reuses_index():
    chunks = (Chunk('a', 'один'), Chunk('b', 'два'))

    def handler(url, body):
        vectors = {'a\nодин': [1, 0], 'b\nдва': [0, 1], 'вопрос': [0, 1]}
        return {'data': [{'index': i, 'embedding': vectors[text]} for i, text in enumerate(body['input'])]}

    client = FakeClient(handler)
    assert semantic_rank(client, 'https://test-hybrid', 'key', 'model', 'вопрос', chunks, 2)[0] == chunks[1]
    semantic_rank(client, 'https://test-hybrid', 'key', 'model', 'вопрос', chunks, 2)
    assert len(client.calls) == 3  # One corpus batch, two query embeddings.


def test_fusion_and_reranker_reject_invalid_indices():
    a, b = Chunk('a', 'a'), Chunk('b', 'b')
    assert fuse([[a, b], [b]], 2)[0] == b
    client = FakeClient(lambda url, body: {'results': [{'index': 8}]})
    import pytest
    with pytest.raises(ValueError):
        rerank(client, 'https://test', 'key', 'model', 'question', [a, b], 2)


def test_verifier_requires_explicit_boolean_for_every_claim():
    claims = [{'text': 'Факт', 'quote': 'Подтверждающая цитата'}]
    def client_for(verdict):
        return FakeClient(lambda url, body: {'choices': [{'message': {'content': verdict}}]})
    assert verify_claims(client_for('{"supported":[true]}'), 'https://test', 'key', 'model', claims)
    assert not verify_claims(client_for('{"supported":[false]}'), 'https://test', 'key', 'model', claims)
    assert not verify_claims(client_for('{"supported":[1]}'), 'https://test', 'key', 'model', claims)
    assert not verify_claims(client_for('{"supported":[]}'), 'https://test', 'key', 'model', claims)


def test_exhausted_embedding_budget_does_not_call_remote_service(monkeypatch):
    import pytest
    from core import hybrid

    monkeypatch.setattr(hybrid.time, 'monotonic', lambda: 10.0)
    client = FakeClient(lambda url, body: {})
    with pytest.raises(TimeoutError):
        semantic_rank(client, 'https://budget-test', 'key', 'model', 'вопрос', [Chunk('a', 'текст')], 1, deadline=9.0)
    assert not client.calls
    assert bounded_timeout(12.0, 6) == 2.0


def test_answer_falls_back_to_lexical_search_but_requires_verification(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://test-answer')
    monkeypatch.setenv('LLM_API_KEY', 'fake')
    class Client:
        def __init__(self, **kwargs):
            self.chat_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, headers, json, **kwargs):
            request = httpx.Request('POST', url)
            if not url.endswith('/chat/completions'):
                return httpx.Response(503, request=request)
            self.chat_calls += 1
            content = ('{"claims":[{"text":"Срок составляет 10 дней.","source":1,"quote":"Срок составляет 10 дней при согласовании."}]}'
                       if self.chat_calls == 1 else '{"supported":[true]}')
            return httpx.Response(200, request=request, json={'choices': [{'message': {'content': content}}]})

    monkeypatch.setattr(httpx, 'Client', Client)
    assert answer('срок', [Chunk('test', 'Срок составляет 10 дней при согласовании.')]) == 'Срок составляет 10 дней.'
