"""Read the installed PDK threshold source from the pinned EDA image.

The capture plan only names the PDK and library.  The config bytes and version
come from the image without a host bind mount, and the judge repeats this read.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _docker_memory  # noqa: E402


_IMAGE_PROBE = r'''
import hashlib, json, os, pathlib, sys
pdk, library = sys.argv[1:]
root = pathlib.Path(os.environ.get("PDK_ROOT", "/foss/pdks"))
suffix = (pdk, "libs.tech", "librelane", library, "config.tcl")
matches = [path for path in root.rglob("config.tcl")
           if path.is_file() and path.parts[-len(suffix):] == suffix]
if len(matches) != 1:
    raise SystemExit("expected exactly one installed PDK config, found %d" % len(matches))
path = matches[0]
parts = path.parts
version = parts[parts.index("versions") + 1] if "versions" in parts else None
print(json.dumps({"path": str(path),
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "pdk_commit": version}))
'''


def image_pdk_anchor(image: str, pdk: str, library: str) -> dict:
    """Return a fresh, image-owned source identity; fail closed on ambiguity."""
    if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", value)
               for value in (pdk, library)):
        raise ValueError("PDK/library identity invalid")
    # The same memory ceiling as every other container this plugin creates.
    process = subprocess.run(
        ["docker", "run", *_docker_memory.docker_memory_flags(), "--rm", image, "--skip", "python3", "-c",
         _IMAGE_PROBE, pdk, library],
        capture_output=True, text=True, check=False, timeout=90)
    if process.returncode:
        raise ValueError("installed PDK config probe failed: " + process.stderr[-300:])
    try:
        record = json.loads(process.stdout.strip().splitlines()[-1])
        if not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
            raise ValueError("image PDK digest malformed")
        return record
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("installed PDK config identity unreadable") from exc
