#!/usr/bin/env python3
"""Step37.3 launcher adapter for the existing LibreLane contract.

Preserves docker inspect/remove calls; native runs use the image's standard
wrapper and retain actual command, exit status, wall time and tool RSS.
"""


# `programs/` is a flat directory whose modules import each other by bare name
# (vibe-ic#2104): restore the condition a by-path load does not provide.
import os as _os
import sys as _sys

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import json
import os
import subprocess
import sys
import time

import _docker_memory as _dmem


def main():
    args = sys.argv[1:]
    if not args or args[0] != "run":
        os.execvp("docker", ["docker", *args])
    image = os.environ["VIBEIC_NATIVE373_IMAGE"]
    at = args.index(image)
    flags, command = args[1:at], args[at + 1:]
    entrypoint = None
    for flag in ("--entrypoint", "--cpus", "--memory", "--memory-swap", "--network"):
        while flag in flags:
            pos = flags.index(flag)
            if flag == "--entrypoint":
                entrypoint = flags[pos + 1]
            del flags[pos:pos + 2]
    if entrypoint:
        command = [entrypoint, *command]
    elif command and command[0] == "--skip":
        command = command[1:]
    else:
        raise ValueError("NATIVE373_WRAPPER_COMMAND_MISSING")
    argv = ["docker", "run", *flags, *_dmem.docker_memory_flags(), "--cpus", "2",
            "--network", "none", image, "--skip",
            "/usr/bin/time", "-v", *command]
    started = time.monotonic()
    cp = subprocess.run(argv)
    with open(os.environ["VIBEIC_NATIVE373_LAUNCH_LOG"], "a") as stream:
        stream.write(json.dumps({"argv": argv, "rc": cp.returncode,
                                 "wall_seconds": time.monotonic() - started}) + "\n")
    return cp.returncode


if __name__ == "__main__":
    sys.exit(main())
