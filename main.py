"""AI Animation Studio — Root Command-Line and Studio Entry Point."""

import os
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"


def main() -> None:
    # 1. Enforce execution under backend/.venv Python interpreter
    if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        cmd = [str(VENV_PYTHON), str(Path(__file__).resolve())] + sys.argv[1:]
        result = subprocess.run(cmd)
        sys.exit(result.returncode)

    # 2. Add repository root to sys.path
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    # 3. Delegate directly to the backend CLI implementation
    from backend.app.cli.main import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
