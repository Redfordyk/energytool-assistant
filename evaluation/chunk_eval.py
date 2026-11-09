"""Evaluate retrieval against manually anchored evidence chunks."""

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from core.rag import (
    RETRIEVAL_CONFIG,
    expand_retrieval_query,
    load_knowledge,
    retrieval_query,
    retrieve,
)


def _normalized(text):
    return re.sub(r'\s+', ' ', text).strip().casefold().replace('ё', 'е')


def _resolve_annotations(gold, chunks):
    resolved = {}
    errors = []
    for question_id, annotations in gold.items():
        if not isinstance(annotations, list) or not annotations:
            errors.append(f'{question_id}: evidence annotations must be a non-empty list')
            continue
        targets = []
        for annotation_index, annotation in enumerate(annotations):
            if not isinstance(annotation, dict):
                errors.append(f'{question_id}[{annotation_index}]: annotation must be an object')
                continue
            alternatives = annotation.get('alternatives', [annotation])
            if not isinstance(alternatives, list) or not alternatives:
                errors.append(
                    f'{question_id}[{annotation_index}]: alternatives must be a non-empty list'
                )
                continue
            matches = set()
            for alternative_index, alternative in enumerate(alternatives):
                location = f'{question_id}[{annotation_index}].alternatives[{alternative_index}]'
                if not isinstance(alternative, dict):
                    errors.append(f'{location}: anchor must be an object')
                    continue
                source = alternative.get('source', '')
                evidence = _normalized(alternative.get('contains', ''))
                if not source or not evidence:
                    errors.append(f'{location}: source and contains are required')
                    continue
                anchor_matches = {
                    index
                    for index, chunk in enumerate(chunks)
                    if chunk.source == source and evidence in _normalized(chunk.text)
                }
                if not anchor_matches:
                    errors.append(f'{location}: evidence anchor not found in {source}')
                matches.update(anchor_matches)
            targets.append(matches)
        if any(targets):
            resolved[str(question_id)] = targets
    if errors:
        raise ValueError('Invalid gold chunk annotations:\n' + '\n'.join(errors))
    return resolved


def evaluate(questions_path, knowledge_path, gold_path, cutoffs=(1, 5, 10)):
    questions_path = Path(questions_path)
    knowledge_path = Path(knowledge_path)
    gold_path = Path(gold_path)
    with questions_path.open(encoding='utf-8-sig', newline='') as source:
        questions = list(csv.DictReader(source))
    if not questions or any(
        not (row.get('id') or '').strip() or not (row.get('question') or '').strip()
        for row in questions
    ):
        raise ValueError('Questions must include non-empty id and question columns')
    question_ids = [row['id'].strip() for row in questions]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError('Question IDs must be unique')
    gold = json.loads(gold_path.read_text(encoding='utf-8'))
    missing = set(question_ids) - set(gold)
    extra = set(gold) - set(question_ids)
    if missing or extra:
        raise ValueError(
            f'Gold/query ID mismatch: missing={sorted(missing)}, extra={sorted(extra)}'
        )
    if not cutoffs or any(
        isinstance(cutoff, bool) or not isinstance(cutoff, int) or cutoff < 1 for cutoff in cutoffs
    ):
        raise ValueError('Cutoffs must be positive integers')
    chunks = load_knowledge(knowledge_path)
    annotations = _resolve_annotations(gold, chunks)
    chunk_indexes = {id(chunk): index for index, chunk in enumerate(chunks)}

    rows = []
    for question in questions:
        question_id = str(question['id'])
        expected_groups = annotations.get(question_id)
        if not expected_groups:
            continue
        search_query = expand_retrieval_query(retrieval_query(question['question']))
        ranked = retrieve(search_query, chunks, limit=len(chunks))
        ranked_indexes = [chunk_indexes[id(chunk)] for chunk in ranked]
        chunk_ranks = {index: rank for rank, index in enumerate(ranked_indexes, 1)}
        anchor_ranks = [
            min((chunk_ranks[index] for index in group if index in chunk_ranks), default=None)
            for group in expected_groups
        ]
        first_rank = min((rank for rank in anchor_ranks if rank is not None), default=None)
        evidence_hits = {
            cutoff: sum(rank is not None and rank <= cutoff for rank in anchor_ranks)
            for cutoff in cutoffs
        }
        rows.append(
            {
                'id': question_id,
                'relevant_evidence_count': len(expected_groups),
                'first_relevant_rank': first_rank,
                'evidence_hits': {str(cutoff): evidence_hits[cutoff] for cutoff in cutoffs},
                'evidence_recall': {
                    str(cutoff): evidence_hits[cutoff] / len(expected_groups) for cutoff in cutoffs
                },
            }
        )
    if not rows:
        raise ValueError('No questions matched the gold chunk manifest')

    metrics = {
        'questions': len(rows),
        'evidence_anchors': sum(row['relevant_evidence_count'] for row in rows),
        'chunks': len(chunks),
        'knowledge_sha256': hashlib.sha256(knowledge_path.read_bytes()).hexdigest(),
        'questions_sha256': hashlib.sha256(questions_path.read_bytes()).hexdigest(),
        'gold_chunks_sha256': hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        'retrieval_config': RETRIEVAL_CONFIG,
    }
    for cutoff in cutoffs:
        metrics[f'query_hit@{cutoff}'] = sum(
            bool(row['evidence_hits'][str(cutoff)]) for row in rows
        ) / len(rows)
        metrics[f'evidence_recall@{cutoff}'] = (
            sum(row['evidence_hits'][str(cutoff)] for row in rows) / metrics['evidence_anchors']
        )
    metrics['mrr@10'] = sum(
        1 / row['first_relevant_rank']
        if row['first_relevant_rank'] and row['first_relevant_rank'] <= 10
        else 0
        for row in rows
    ) / len(rows)
    metrics['unanswered_evidence@10'] = [
        row['id']
        for row in rows
        if not row['first_relevant_rank'] or row['first_relevant_rank'] > 10
    ]
    return {'metrics': metrics, 'queries': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--questions', default='data/questions.csv')
    parser.add_argument('--knowledge', default='data/knowledge-base.zip')
    parser.add_argument('--gold', default=str(Path(__file__).with_name('gold_chunks.json')))
    parser.add_argument('--output', default='docs/chunk-retrieval-baseline.json')
    args = parser.parse_args()
    report = evaluate(args.questions, args.knowledge, args.gold)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded, encoding='utf-8')
    print(json.dumps(report['metrics'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
