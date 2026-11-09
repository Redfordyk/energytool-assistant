"""Grouped cross-validation for BM25 parameters on the labeled source set."""
import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

from core.rag import RETRIEVAL_CONFIG, STOP_WORDS, TOKEN, load_knowledge, retrieve, retrieval_query


WIDTHS = (3, 4, 5, 6)
K1_VALUES = (0.7, 1.2, 2.0)
B_VALUES = (0.25, 0.75, 1.0)


def _tokenize(text, width):
    return [word[:width] for word in TOKEN.findall(text.lower().replace('ё', 'е'))
            if len(word) >= 3 and word[:width] not in STOP_WORDS]


def _reciprocal_rank(rank):
    return 1 / rank if rank is not None and rank <= 10 else 0


def evaluate(questions_path, knowledge_path, gold_path):
    gold = json.loads(Path(gold_path).read_text(encoding='utf-8'))
    with Path(questions_path).open(encoding='utf-8-sig', newline='') as source:
        questions = list(csv.DictReader(source))
    chunks = load_knowledge(knowledge_path)
    source_frequency = Counter(source_name for expected in gold.values() for source_name in expected)
    rows = []
    for row in questions:
        expected = set(gold.get(str(row['id']), []))
        if not expected:
            continue
        primary = min(expected, key=lambda source_name: (source_frequency[source_name], source_name))
        rows.append({
            'id': str(row['id']),
            'query': retrieval_query(row['question']),
            'expected': expected,
            'group': primary,
        })
    if not rows:
        raise ValueError('No questions matched the gold source manifest')

    parameters = [(width, k1, b) for width in WIDTHS for k1 in K1_VALUES for b in B_VALUES]
    ranks = {}
    for width in WIDTHS:
        documents = [Counter(_tokenize(chunk.source + ' ' + chunk.text, width)) for chunk in chunks]
        document_frequency = Counter(token for document in documents for token in document)
        lengths = [sum(document.values()) for document in documents]
        average_length = sum(lengths) / len(lengths) or 1
        query_tokens = {row['id']: set(_tokenize(row['query'], width)) for row in rows}
        for k1 in K1_VALUES:
            for b in B_VALUES:
                for row in rows:
                    query = query_tokens[row['id']]
                    scored = []
                    for index, document in enumerate(documents):
                        score = sum(
                            math.log(1 + (len(documents) - document_frequency[token] + 0.5)
                                     / (document_frequency[token] + 0.5))
                            * document[token] * (k1 + 1)
                            / (document[token] + k1 * (1 - b + b * lengths[index] / average_length))
                            for token in query if token in document
                        )
                        if score:
                            scored.append((score, index))
                    scored.sort(key=lambda item: (-item[0], item[1]))
                    ranked_sources = list(dict.fromkeys(chunks[index].source for _, index in scored))
                    first_relevant = next(
                        (rank for rank, name in enumerate(ranked_sources, 1) if name in row['expected']), None)
                    ranks[(width, k1, b, row['id'])] = first_relevant

    def score(parameter, selected):
        return sum(_reciprocal_rank(ranks[(*parameter, row['id'])]) for row in selected) / len(selected)

    groups = sorted({row['group'] for row in rows})
    folds = []
    held_out = []
    for group in groups:
        train = [row for row in rows if row['group'] != group]
        test = [row for row in rows if row['group'] == group]
        selected = max(parameters, key=lambda item: (score(item, train), -item[0], -item[1], item[2]))
        fold_ranks = [{'id': row['id'], 'rank': ranks[(*selected, row['id'])]} for row in test]
        held_out.extend(fold_ranks)
        folds.append({
            'held_out_primary_source': group,
            'train_questions': len(train),
            'test_questions': len(test),
            'selected_parameters': {'token_prefix_length': selected[0], 'k1': selected[1], 'b': selected[2]},
            'mrr@10': sum(_reciprocal_rank(item['rank']) for item in fold_ranks) / len(fold_ranks),
            'queries': fold_ranks,
        })

    baseline_ranks = {}
    for row in rows:
        ranked = retrieve(row['query'], chunks, limit=len(chunks))
        sources = list(dict.fromkeys(chunk.source for chunk in ranked))
        baseline_ranks[row['id']] = next(
            (rank for rank, name in enumerate(sources, 1) if name in row['expected']), None)

    return {
        'questions': len(rows),
        'chunks': len(chunks),
        'primary_source_groups': len(groups),
        'parameter_grid': {'token_prefix_length': list(WIDTHS), 'k1': list(K1_VALUES), 'b': list(B_VALUES)},
        'current_config': RETRIEVAL_CONFIG,
        'current_config_mrr@10': sum(_reciprocal_rank(baseline_ranks[row['id']]) for row in rows) / len(rows),
        'grouped_cross_validated_mrr@10': sum(_reciprocal_rank(item['rank']) for item in held_out) / len(held_out),
        'folds': folds,
        'source_level_only': True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--questions', default='data/questions.csv')
    parser.add_argument('--knowledge', default='data/knowledge-base.zip')
    parser.add_argument('--gold', default=str(Path(__file__).with_name('gold_sources.json')))
    parser.add_argument('--output')
    args = parser.parse_args()
    report = evaluate(args.questions, args.knowledge, args.gold)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(encoded + '\n', encoding='utf-8')
    print(json.dumps({
        'questions': report['questions'],
        'primary_source_groups': report['primary_source_groups'],
        'current_config': report['current_config'],
        'current_config_mrr@10': report['current_config_mrr@10'],
        'grouped_cross_validated_mrr@10': report['grouped_cross_validated_mrr@10'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
