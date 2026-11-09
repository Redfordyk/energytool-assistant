"""Create the submission archive without private inputs or generated files."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

with ZipFile('submission.zip', 'w', ZIP_DEFLATED) as archive:
    for path in (Path('solution.py'), Path('requirements.txt'), Path('core/__init__.py'), Path('core/rag.py'), Path('core/hybrid.py')):
        archive.write(path, path.as_posix())
