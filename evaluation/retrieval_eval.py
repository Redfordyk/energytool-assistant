"""Reproducible source-level recall and reciprocal-rank evaluation."""
import argparse
import csv
import json
import os
from pathlib import Path

from core.rag import RETRIEVAL_CONFIG, expand_retrieval_query, load_knowledge, retrieve, retrieval_query


def evaluate(questions_path, knowledge_path, gold_path, cutoffs=(1, 5, 10)):
    gold = json.loads(Path(gold_path).read_text(encoding='utf-8'))
    with Path(questions_path).open(encoding='utf-8-sig', newline='') as source:
        questions = list(csv.DictReader(source))
    chunks = load_knowledge(knowledge_path)
    rows = []
    for row in questions:
        expected = set(gold.get(str(row['id']), []))
        if not expected:
            continue
        query = expand_retrieval_query(retrieval_query(row['question']))
        ranked_chunks = retrieve(query, chunks, limit=100)
        ranked_sources = list(dict.fromkeys(chunk.source for chunk in ranked_chunks))
        first_rank = min((rank for rank, source in enumerate(ranked_sources, 1) if source in expected), default=None)
        item = {'id': str(row['id']), 'expected': sorted(expected), 'ranked': ranked_sources,
                'first_relevant_rank': first_rank}
        rows.append(item)

    if not rows:
        raise ValueError('No questions matched the gold source manifest')
    metrics = {'questions': len(rows), 'chunks': len(chunks), 'documents': len({c.source for c in chunks}),
               'retrieval_config': RETRIEVAL_CONFIG}
    for cutoff in cutoffs:
        metrics[f'recall@{cutoff}'] = sum(
            bool(set(row['expected']) & set(row['ranked'][:cutoff])) for row in rows
        ) / len(rows)
    metrics['mrr@10'] = sum(
        1 / row['first_relevant_rank'] if row['first_relevant_rank'] and row['first_relevant_rank'] <= 10 else 0
        for row in rows
    ) / len(rows)
    metrics['misses@10'] = [row['id'] for row in rows if row['first_relevant_rank'] is None or row['first_relevant_rank'] > 10]
    metrics['source_level_only'] = True
    return {'metrics': metrics, 'queries': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--questions', default=str(Path(os.getenv('DATA_DIR', 'data')) / 'questions.csv'))
    parser.add_argument('--knowledge', default=str(Path(os.getenv('DATA_DIR', 'data')) / 'knowledge-base.zip'))
    parser.add_argument('--gold', default=str(Path(__file__).with_name('gold_sources.json')))
    parser.add_argument('--output')
    args = parser.parse_args()
    report = evaluate(args.questions, args.knowledge, args.gold)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(encoded + '\n', encoding='utf-8')
    print(json.dumps(report['metrics'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
