"""One offline acceptance checklist shared by local verification, CI and packaging."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
VALIDATORS = (
    'verify_worker_recovery.py',
    'verify_agent_desk.py',
    'verify_session_setup.py',
    'verify_local_ai.py',
    'verify_strategy_selection.py',
    'verify_broker_connection.py',
    'verify_cli_workflows.py',
    'verify_research_inputs.py',
)


def main() -> int:
    paths = [TOOLS / name for name in VALIDATORS]
    for path in paths:
        if not path.is_file():
            print(f'Missing acceptance validator: {path.name}', file=sys.stderr)
            return 2
    # Preserve the build environment's isolated interpreter without disabling
    # a source checkout's existing user installation during ordinary verify.
    flags = ['-I'] if sys.flags.isolated else []
    for path in paths:
        print(f'Checking {path.name}', flush=True)
        result = subprocess.run([sys.executable, *flags, str(path)], check=False)
        if result.returncode:
            return result.returncode
    print('All offline acceptance checks passed.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
