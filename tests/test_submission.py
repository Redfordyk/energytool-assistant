import csv
import os
import subprocess
import sys
import zipfile

from package_submission import FILES, build_archive
from solution import configure_submission_retrieval


def test_submission_inherits_shared_platform_base_for_retrieval(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://platform.example/v1/')
    monkeypatch.delenv('BASE_URL', raising=False)
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    monkeypatch.delenv('EMBEDDER_BASE_URL', raising=False)
    monkeypatch.delenv('RERANKER_BASE_URL', raising=False)

    configure_submission_retrieval()

    assert os.environ['EMBEDDER_BASE_URL'] == 'https://platform.example/v1'
    assert os.environ['RERANKER_BASE_URL'] == 'https://platform.example/v1'


def test_submission_preserves_explicit_retrieval_configuration(monkeypatch):
    monkeypatch.setenv('BASE_URL', 'https://platform.example/v1')
    monkeypatch.delenv('LLM_BASE_URL', raising=False)
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    monkeypatch.setenv('EMBEDDER_BASE_URL', '')
    monkeypatch.setenv('RERANKER_BASE_URL', 'https://reranker.example/v1')

    configure_submission_retrieval()

    assert os.environ['EMBEDDER_BASE_URL'] == ''
    assert os.environ['RERANKER_BASE_URL'] == 'https://reranker.example/v1'


def test_submission_archive_runs_in_clean_directory(tmp_path):
    archive_path = build_archive(tmp_path / 'submission.zip')
    with zipfile.ZipFile(archive_path) as archive:
        expected = {path.as_posix() for path in FILES} | {'requirements.txt'}
        assert set(archive.namelist()) == expected
        assert archive.read('requirements.txt').decode().strip() == 'httpx==0.28.1'
        assert not any(name.startswith(('data/', '.env')) for name in archive.namelist())
        archive.extractall(tmp_path / 'package')

    data_dir = tmp_path / 'task-data'
    data_dir.mkdir()
    with zipfile.ZipFile(data_dir / 'knowledge-base.zip', 'w') as knowledge:
        knowledge.writestr('facts.md', '# Энергия\nДокумент о тарифах.')
    with (data_dir / 'questions.csv').open('w', encoding='utf-8-sig', newline='') as source:
        writer = csv.writer(source)
        writer.writerow(('id', 'question'))
        writer.writerow(('case,1', 'Вопрос с запятой, да?'))
        writer.writerow(('case-2', 'Вопрос\nс переносом'))

    env = os.environ.copy()
    for name in ('LLM_API_KEY', 'OPENAI_API_KEY', 'LLM_BASE_URL', 'BASE_URL', 'OPENAI_BASE_URL'):
        env.pop(name, None)
    env['DATA_DIR'] = str(data_dir)
    subprocess.run(
        [sys.executable, 'solution.py'],
        cwd=tmp_path / 'package',
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )

    with (tmp_path / 'package' / 'submission.csv').open(encoding='utf-8', newline='') as result:
        rows = list(csv.DictReader(result))
    assert [row['id'] for row in rows] == ['case,1', 'case-2']
    assert all(row['answer'].startswith('Не могу достоверно ответить') for row in rows)
