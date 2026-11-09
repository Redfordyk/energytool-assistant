import zipfile

from core.rag import FALLBACK, answer, load_knowledge, retrieve


def test_retrieval_covers_later_sections(tmp_path):
    archive = tmp_path / 'kb.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('product.md', '# Введение\n' + 'текст ' * 500 + '\n# Солнце\nФасадные панели применяются при определённых условиях.')
    chunks = load_knowledge(archive)
    assert any('Фасадные' in c.text for c in retrieve('фасадные панели', chunks))


def test_no_model_fails_closed(monkeypatch):
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    assert answer('цена', []) == FALLBACK


def test_archive_size_limit(tmp_path):
    archive = tmp_path / 'kb.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('big.md', 'x' * 2_000_001)
    import pytest
    with pytest.raises(ValueError):
        load_knowledge(archive)
