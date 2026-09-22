"""Cross-platform one-command verification for Update 30 Phase 1."""
from __future__ import annotations

import compileall
import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print(f"\n> {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    required = ('flask', 'flask_sqlalchemy', 'flask_migrate', 'PIL', 'sqlalchemy', 'boto3')
    missing = []
    for name in required:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        print(f"Missing runtime dependencies: {', '.join(missing)}")
        print('Activate the application virtual environment and install requirements.txt first.')
        return 2

    print('Compiling Python source…', flush=True)
    if not compileall.compile_dir(ROOT, quiet=1):
        return 3
    run([sys.executable, '-m', 'pip', 'check'])
    run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
    print('\nUPDATE 30 PHASE 1 VERIFICATION: PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
