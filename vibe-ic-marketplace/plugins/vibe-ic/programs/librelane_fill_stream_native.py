"""Run reviewed LibreLane stream producer with only its fill-clear bug patched.

The installed source is never edited. Its complete license/header is retained
in the compiled source; the exact original digest and this patch are bound by
librelane_fill_stream.operation_sources. Unknown native versions refuse.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from librelane_fill_stream import native_script, patched_native_source

if __name__ == '__main__':
    original = native_script()
    source = patched_native_source(original.read_text())
    sys.argv[0] = str(original)
    exec(compile(source, str(original), 'exec'), {'__name__': '__main__', '__file__': str(original)})
