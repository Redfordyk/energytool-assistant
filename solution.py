"""Platform entry point: read CSV and supplied knowledge ZIP, write submission.csv."""

import csv
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.rag import answer, load_knowledge


def find(names):
    directory = Path(os.getenv('DATA_DIR', 'data'))
    for root in (directory, Path(__file__).resolve().parent / directory, Path.cwd()):
        for name in names:
            path = root / name
            if path.is_file():
                return path
    raise FileNotFoundError(', '.join(names))


def main():
    chunks = load_knowledge(find(('knowledge-base.zip', 'knowledge_base.zip', 'kb.zip')))
    with find(('questions.csv', 'train.csv')).open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file))
    try:
        workers = min(8, max(1, int(os.getenv('MAX_WORKERS', '3'))))
    except ValueError:
        workers = 3
    with ThreadPoolExecutor(max_workers=workers) as pool:
        answers = list(pool.map(lambda row: answer(row['question'], chunks), rows))
    with open('submission.csv', 'w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, quoting=csv.QUOTE_ALL, lineterminator='\n')
        writer.writerow(('id', 'answer'))
        writer.writerows((row['id'], result) for row, result in zip(rows, answers))


if __name__ == '__main__':
    main()
