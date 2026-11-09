"""Create a reproducible submission archive without private task inputs."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
FILES = (
    Path('solution.py'),
    Path('core/__init__.py'),
    Path('core/rag.py'),
    Path('core/hybrid.py'),
)


def build_archive(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for relative_path in FILES:
            archive.write(ROOT / relative_path, relative_path.as_posix())
        # The platform expects the dependency manifest at the archive root.
        archive.write(ROOT / 'submission-requirements.txt', 'requirements.txt')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'submission.zip'))
    args = parser.parse_args()
    result = build_archive(args.output)
    print(f'Created {result} ({result.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
