"""Native Verilator Step 4 candidate; fixed simulator, shared canonical consumers."""
# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os
import sys as _sys

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

from execution_step4 import main

if __name__ == '__main__':
    main('verilator')
