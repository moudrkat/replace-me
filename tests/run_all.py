"""Run every end-to-end suite, each in its own process.

    python3 tests/run_all.py

Own-process isolation matters: the modules cache the character and read
env at import, so suites must not share an interpreter. ~1 minute total,
no mic/TTS/model/network beyond 127.0.0.1.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = ["test_handoff.py", "test_minutes.py", "test_ui.py", "test_style.py", "test_career.py"]


def main() -> None:
    failed = []
    for suite in SUITES:
        print(f"\n=== {suite} ===", flush=True)
        result = subprocess.run([sys.executable, str(HERE / suite)])
        if result.returncode != 0:
            failed.append(suite)
    print("\n" + ("ALL SUITES PASS" if not failed else f"SUITES FAILED: {failed}"), flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
