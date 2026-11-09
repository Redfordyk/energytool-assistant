import json
import zipfile

import pytest

from evaluation.chunk_eval import evaluate


def test_chunk_evaluation_measures_exact_evidence_retrieval(tmp_path):
    questions = tmp_path / 'questions.csv'
    questions.write_text('id,question\n1,Какова максимальная мощность?\n', encoding='utf-8')
    knowledge = tmp_path / 'knowledge.zip'
    with zipfile.ZipFile(knowledge, 'w') as archive:
        archive.writestr(
            'limits.md', '# Предел мощности\nМаксимальная мощность объекта составляет 750 кВт.'
        )
        archive.writestr('other.md', '# Другое\nДокумент описывает цену оборудования.')
    gold = tmp_path / 'gold.json'
    gold.write_text(
        json.dumps(
            {
                '1': [
                    {
                        'source': 'limits.md',
                        'contains': 'Максимальная мощность объекта составляет 750 кВт',
                    }
                ],
            }
        ),
        encoding='utf-8',
    )

    report = evaluate(questions, knowledge, gold)

    assert report['metrics']['query_hit@1'] == 1
    assert report['metrics']['evidence_recall@10'] == 1
    assert report['queries'][0]['first_relevant_rank'] == 1


def test_chunk_evaluation_rejects_stale_or_mistyped_evidence(tmp_path):
    questions = tmp_path / 'questions.csv'
    questions.write_text('id,question\n1,вопрос\n', encoding='utf-8')
    knowledge = tmp_path / 'knowledge.zip'
    with zipfile.ZipFile(knowledge, 'w') as archive:
        archive.writestr('limits.md', '# Предел мощности\n750 кВт.')
    gold = tmp_path / 'gold.json'
    gold.write_text(
        json.dumps(
            {
                '1': [{'source': 'limits.md', 'contains': 'Несуществующее подтверждение'}],
            }
        ),
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='evidence anchor not found'):
        evaluate(questions, knowledge, gold)
