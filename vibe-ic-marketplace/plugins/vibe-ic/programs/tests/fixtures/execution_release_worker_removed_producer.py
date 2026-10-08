"""Source-bound negative fixture with the release producer call removed."""
from __future__ import annotations

from pathlib import Path
import sys

PROGRAMS = Path(__file__).resolve().parents[2]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import execution_release_worker as _worker


def _removed_producer(*_args, **_kwargs):
    return []


_worker.produce = _removed_producer
main = _worker.main


if __name__ == "__main__":
    raise SystemExit(main())
