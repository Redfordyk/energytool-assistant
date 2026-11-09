import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which('sh') is None, reason='POSIX shell is not installed')
def test_backup_and_restore_scripts_have_valid_shell_syntax():
    root = Path(__file__).resolve().parents[1]
    for script in (root / 'ops' / 'backup.sh', root / 'ops' / 'restore-smoke.sh'):
        subprocess.run(['sh', '-n', str(script)], check=True)
