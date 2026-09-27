from __future__ import annotations

import errno
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
RUNNER_PATH = HERE / "hermetic_candidate_runner.py"
SPEC = importlib.util.spec_from_file_location("hermetic_candidate_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


FAKE_DOCKER = r'''#!/usr/bin/env python3
import contextlib
import fcntl
import json
import os
import shutil
import sys
import time
from pathlib import Path

# THE FAKE WORLD MUST HOLD THE IMAGE THE RUNNER DEMANDS (vibe-ic#2100).
#
# THE DIGEST USED TO BE A LITERAL HERE, and the reasoning was sound while there
# WAS a pin: this file is the pin's own test, so reading the pin to build the
# world the runner is judged against would have been circular, and a drift net
# (`test_the_fake_world_holds_exactly_the_pinned_digest`) made a pin move fail
# once, by name.
#
# THERE IS NO PIN TO GUARD ANY MORE. The identity is resolved from whatever EDA
# image the host holds, so "the digest changed" is the NORMAL state, not the
# event that net existed to announce — it fired on every host that had pulled
# anything, and took fifteen unrelated ids with it. The fake is now told the
# identity the runner resolved, which is not circular for the same reason the
# repository half never was: the runner is not being judged on WHICH image it
# picked, it is being judged on whether it binds, records and refuses around
# the one it did.
#
# THE REPOSITORY IS NOT PART OF THE IDENTITY either. MEASURED 2026-09-07 on
# 8HD-4: with `$VIBEIC_EDA_IMAGE_REPO` exported the runner asked for
# `<lan-registry>/vibeic-eda@…` while this fake answered about `ghcr.io/…`, and
# 15 ids went red describing the operator's registry rather than the runner.
# Taking the whole reference from the runner keeps both halves in step.
# Two ways in, because not every caller goes through `invoke()`: the explicit
# handoff, else the identity `_eda_pin` published into the environment when it
# resolved (so a whole process tree agrees) -- and this fake IS part of that
# tree. The export agrees on the DIGEST, not on the repository: it names the
# reference this host HOLDS the bytes under, while the runner asks for its
# CONFIGURED reference first (#2170). So a caller that starts the runner itself
# must make the explicit handoff, exactly as `invoke()` does.
IMAGE = (os.environ.get("FAKE_DOCKER_IMAGE")
         or os.environ.get("VIBEIC_EDA_IMAGE")
         or (((os.environ.get("VIBEIC_EDA_IMAGE_REPO") or "").strip()
              or "ghcr.io/vibeic/vibeic-eda") + "@sha256:" + "0" * 64))
IMAGE_ID = "sha256:" + "1" * 64
CID = "2" * 64
PREFIX = "VIBEIC_PROGRESS "
root = Path(os.environ["FAKE_DOCKER_STATE"])
root.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
with (root / "calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(args, sort_keys=True) + "\n")

# CONTAINMENT-ESCAPE SIMULATION. Every docker call happens strictly between the
# runner's initial and final input digests, so appending here is exactly "a
# parent-owned input changed while the candidate was running" -- the thing the
# post-attestation exists to catch. Fired once, so the digest changes once.
_tamper = os.environ.get("FAKE_DOCKER_TAMPER_PATH")
if _tamper and not (root / "tampered").exists():
    (root / "tampered").write_text("1", encoding="utf-8")
    with open(_tamper, "ab") as _fh:
        _fh.write(b"ESCAPED\n")

def fail(message, rc=1):
    print(message, file=sys.stderr)
    raise SystemExit(rc)

def container_path(name):
    if "-export-" in name:
        return root / "exporter.json"
    if "-provision-" in name:
        return root / "provisioner.json"
    return root / "container.json"

def load_container(name):
    path = container_path(name)
    if not path.exists():
        fail("Error: No such container: " + name)
    return json.loads(path.read_text(encoding="utf-8"))

@contextlib.contextmanager
def state_lock():
    """Serialise the state mutations real Docker serialises in its daemon.

    The runner drives `container kill` and `container rm --force` CONCURRENTLY
    during teardown, which is fine against a real daemon and was not fine here:
    `kill` does load -> mutate -> save with nothing holding the two ends
    together, so `rm` could unlink between them and `save` would then RESURRECT
    the file. Measured 2026-08-22 -- a zero-byte `container.json` left behind,
    because the stub was torn down between creating the name and writing to it.

    The lock is NOT taken around a whole command. `container start --attach`
    blocks until the container exits and is itself what `kill` ends, so holding
    a lock across it would deadlock the pair it is meant to protect.
    """
    fd = os.open(str(root / ".state.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

def save_container(doc, create=False):
    path = container_path(doc["Name"].lstrip("/"))
    with state_lock():
        if not create and not path.exists():
            # REMOVED WHILE THIS COMMAND WAS MID-FLIGHT. Writing now would
            # resurrect a container the runner has already torn down, and the
            # test that checks cleanup is owned would see leftover state that
            # the runner did not leave. Real Docker errors here; this returns
            # quietly on purpose, because a non-zero exit from a late `kill`
            # would change the runner's control flow rather than just its
            # bookkeeping, and the fake exists to be deterministic about the
            # latter.
            return
        # ATOMIC, so a stub killed mid-write can never leave a zero-byte file
        # where a valid document or no document are the only two honest states.
        tmp = path.parent / (path.name + "." + str(os.getpid()) + ".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(str(tmp), str(path))

if args[:2] == ["image", "inspect"]:
    print(json.dumps([{"Architecture": "amd64", "Id": IMAGE_ID,
                       "Os": "linux", "RepoDigests": [IMAGE]}]))
elif args[:2] == ["volume", "create"]:
    name = args[-1]
    label = args[args.index("--label") + 1].split("=", 1)[1]
    opts = {}
    for index, arg in enumerate(args):
        if arg == "--opt":
            key, value = args[index + 1].split("=", 1)
            opts[key] = value
    (root / "volume.json").write_text(json.dumps({
        "Driver": "local", "Labels": {"ai.vibeic.hermetic-run": label},
        "Mountpoint": "/var/lib/docker/volumes/" + name,
        "Name": name, "Options": opts, "Scope": "local",
    }), encoding="utf-8")
    print(name)
elif args[:2] == ["volume", "inspect"]:
    path = root / "volume.json"
    if not path.exists():
        fail("Error: No such volume: " + args[-1])
    print("[" + path.read_text(encoding="utf-8") + "]")
elif args[:2] == ["volume", "rm"]:
    path = root / "volume.json"
    if not path.exists():
        fail("Error: No such volume: " + args[-1])
    name = json.loads(path.read_text(encoding="utf-8"))["Name"]
    path.unlink()
    print(name)
elif args[:2] == ["container", "create"]:
    value_flags = {
        "--name", "--label", "--user", "--network", "--cap-drop",
        "--security-opt", "--restart", "--tmpfs", "--workdir", "--env",
        "--mount", "--entrypoint", "--platform",
    }
    boolean_flags = {"--pull=never", "--read-only"}
    values = {}
    repeated = {"--env": [], "--mount": [], "--tmpfs": []}
    i = 2
    while i < len(args) and args[i] != IMAGE:
        flag = args[i]
        if flag in boolean_flags:
            values[flag] = True
            i += 1
        elif flag in value_flags:
            value = args[i + 1]
            if flag in repeated:
                repeated[flag].append(value)
            else:
                values[flag] = value
            i += 2
        else:
            fail("unknown create option " + flag)
    if i >= len(args):
        fail("image missing")
    command = args[i + 1:]
    env = ["PATH=/usr/bin", *repeated["--env"]]
    mounts = []
    for raw in repeated["--mount"]:
        fields = {}
        flags = set()
        for part in raw.split(","):
            if "=" in part:
                key, value = part.split("=", 1)
                fields[key] = value
            else:
                flags.add(part)
        kind = fields["type"]
        destination = fields["dst"]
        if kind == "bind":
            # A DAEMON THAT REPORTS A WRITABLE PARENT-OWNED BIND. The runner
            # asks docker what it ACTUALLY mounted rather than trusting the
            # flags it passed, so this is how that question is made to matter.
            _rw = "readonly" not in flags
            if destination == os.environ.get("FAKE_DOCKER_WRITABLE_DEST"):
                _rw = True
            mounts.append({
                "Destination": destination,
                "Mode": "rw" if _rw else "ro",
                "Propagation": "rprivate", "RW": _rw,
                "Source": fields["src"], "Type": "bind",
            })
        else:
            mounts.append({
                "Destination": destination, "Driver": "local", "Mode": "z",
                "Name": fields["src"], "Propagation": "", "RW": True,
                "Source": "/var/lib/docker/volumes/" + fields["src"], "Type": "volume",
            })
            mounts[-1]["RW"] = "readonly" not in flags
    behavior = os.environ.get("FAKE_DOCKER_BEHAVIOR", "good")
    if behavior == "rw_bind":
        # A bind at the RIGHT destination that is READ-WRITE. `extra_mount`
        # adds a NEW destination and is caught by the unowned-bind branch;
        # this is the other shape, and it is the one that would let a candidate
        # arm write back into a host path the parent later trusts. Applied only
        # to the subject bind, so the provisioner passes and the refusal has to
        # come from the candidate profile itself.
        for item in mounts:
            if item["Destination"] == values["--workdir"]:
                item["Mode"] = "rw"
                item["RW"] = True
    if behavior == "extra_mount":
        mounts.append({
            "Destination": "/host", "Mode": "ro", "Propagation": "rprivate",
            "RW": False, "Source": "/", "Type": "bind",
        })
    user = values["--user"] if behavior != "wrong_user" else "0:0"
    tmpfs = dict(raw.split(":", 1) for raw in repeated["--tmpfs"])
    candidate = "-provision-" not in values["--name"] and "-export-" not in values["--name"]
    if candidate:
        if behavior == "missing_home":
            tmpfs.pop("/nonexistent", None)
        elif behavior == "missing_scratch":
            tmpfs.pop("/var/tmp", None)
        elif behavior == "exec_tmp":
            tmpfs["/tmp"] = tmpfs["/tmp"].replace("noexec", "exec")
        elif behavior == "home_unowned":
            tmpfs["/nonexistent"] = tmpfs["/nonexistent"].replace("uid=65534", "uid=0")
        elif behavior == "host_home_mount":
            mounts.append({"Destination": "/nonexistent", "Type": "bind",
                           "Source": "/home", "RW": True, "Propagation": "rprivate"})
    doc = {
        "Config": {
            "AttachStdin": False, "Cmd": command, "Entrypoint": [values["--entrypoint"]],
            "Env": env, "Image": IMAGE,
            "Labels": {"ai.vibeic.hermetic-run": values["--label"].split("=", 1)[1]},
            "OpenStdin": False, "Tty": False, "User": user,
            "WorkingDir": values["--workdir"],
        },
        "HostConfig": {
            "AutoRemove": False, "Binds": None, "CapAdd": None,
            "CapDrop": [values["--cap-drop"]], "Devices": [],
            "NetworkMode": values["--network"], "Privileged": False,
            "PublishAllPorts": False, "ReadonlyRootfs": True,
            "RestartPolicy": {"MaximumRetryCount": 0, "Name": "no"},
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": tmpfs,
        },
        "Id": CID, "Image": IMAGE_ID, "Mounts": mounts,
        "Name": "/" + values["--name"],
        "State": {
            "Dead": False, "Error": "", "ExitCode": 0, "OOMKilled": False,
            "Paused": False, "Pid": 0, "Restarting": False,
            "Running": False, "Status": "created",
        },
    }
    save_container(doc, create=True)
    (root / "evidence").mkdir(exist_ok=True)
    print(CID)
elif args[:2] == ["container", "inspect"]:
    print(json.dumps([load_container(args[-1])]))
elif args[:2] == ["container", "start"]:
    name = args[-1]
    doc = load_container(name)
    doc["State"].update({"Pid": 4242, "Running": True, "Status": "running"})
    save_container(doc)
    if "-export-" in name:
        source = root / "evidence"
        export_mount = next(item for item in doc["Mounts"]
                            if item["Destination"] == "/export")
        destination = Path(export_mount["Source"])
        for item in source.iterdir():
            target = destination / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copyfile(item, target)
        doc = load_container(name)
        doc["State"].update({
            "Dead": False, "Error": "", "ExitCode": 0, "OOMKilled": False,
            "Paused": False, "Pid": 0, "Restarting": False,
            "Running": False, "Status": "exited",
        })
        save_container(doc)
        raise SystemExit(0)
    if "-provision-" in name:
        (root / "evidence" / ".vibeic-volume-ready").touch()
        doc = load_container(name)
        doc["State"].update({
            "Dead": False, "Error": "", "ExitCode": 0, "OOMKilled": False,
            "Paused": False, "Pid": 0, "Restarting": False,
            "Running": False, "Status": "exited",
        })
        save_container(doc)
        raise SystemExit(0)
    cmd = doc["Config"]["Cmd"]
    command_index = next(index for index, item in enumerate(cmd[2:], 2)
                         if "=" not in item)
    env = dict(item.split("=", 1) for item in cmd[2:command_index])
    plan_mount = next(item for item in doc["Mounts"]
                      if item["Destination"] == "/input/progress-plan.json")
    plan = json.loads(Path(plan_mount["Source"]).read_text(encoding="utf-8"))
    common = {
        "nonce": env["VIBEIC_HERMETIC_PROGRESS_NONCE"], "schema": 1,
        "scope": plan["scope"], "total": len(plan["units"]),
    }
    behavior = os.environ.get("FAKE_DOCKER_BEHAVIOR", "good")
    def emit(row):
        print(PREFIX + json.dumps(row, sort_keys=True, separators=(",", ":")), flush=True)
    emit({**common, "seq": 0, "state": "start"})
    if behavior == "malformed":
        print(PREFIX + "{", flush=True)
        rc = 0
    elif behavior == "duplicate":
        print(PREFIX + '{"nonce":"x","nonce":"x"}', flush=True)
        rc = 0
    elif behavior == "nan":
        print(PREFIX + '{"completed":NaN}', flush=True)
        rc = 0
    elif behavior in {"stall", "stall_stop_unproven"}:
        (root / "evidence" / "raw-progress.jsonl").write_text(
            '{"event":"collection-started"}\n', encoding="utf-8")
        print("raw failure diagnostic", file=sys.stderr, flush=True)
        while True:
            current = load_container(name)
            if current.get("Killed"):
                raise SystemExit(137)
            time.sleep(0.02)
    else:
        if behavior == "collect_scan":
            emit({**common, "completed": 0, "seq": 1, "state": "collect_scan",
                  "unit": "pytest:collection-complete", "scanned": 1000})
        for index, unit in enumerate(plan["units"], 1):
            emit({**common, "completed": index, "seq": index,
                  "state": "checkpoint", "unit": unit})
        emit({**common, "completed": len(plan["units"]),
              "seq": len(plan["units"]) + 1, "state": "terminal"})
        (root / "evidence" / "result.txt").write_text("candidate evidence\n",
                                                       encoding="utf-8")
        rc = 7 if behavior == "exit7" else 0
    doc = load_container(name)
    doc["State"].update({
        "Dead": False, "Error": "", "ExitCode": rc,
        "OOMKilled": behavior == "oom", "Paused": False, "Pid": 0,
        "Restarting": behavior == "restarting", "Running": False,
        "Status": "exited",
    })
    save_container(doc)
    raise SystemExit(rc)
elif args[:2] == ["container", "cp"]:
    source = root / "evidence"
    destination = Path(args[-1])
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
elif args[:2] == ["container", "kill"]:
    doc = load_container(args[-1])
    doc["Killed"] = True
    doc["State"].update({
        "ExitCode": 137, "Pid": 0, "Restarting": False,
        "Running": False, "Status": "exited",
    })
    if os.environ.get("FAKE_DOCKER_BEHAVIOR") == "stall_stop_unproven":
        doc["State"].update({"Running": True, "Pid": 999, "Status": "running"})
    save_container(doc)
    print(doc["Name"].lstrip("/"))
elif args[:2] == ["container", "rm"]:
    name = args[-1]
    path = container_path(name)
    with state_lock():
        if not path.exists():
            fail("Error: No such container: candidate")
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc["State"]["Running"] and "--force" not in args:
            fail("container is running")
        path.unlink()
    print(doc["Name"].lstrip("/"))
else:
    fail("unsupported fake Docker command: " + repr(args))
    '''


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"))


@pytest.fixture
def case(tmp_path: Path):
    subject = tmp_path / "subject"
    runtime = tmp_path / "runtime"
    corpus = tmp_path / "corpus"
    subject.mkdir()
    runtime.mkdir()
    corpus.mkdir()
    (subject / "candidate.py").write_text("print('candidate')\n", encoding="utf-8")
    (runtime / "candidate.py").write_text("#!/usr/bin/python3\n", encoding="utf-8")
    (runtime / "candidate.py").chmod(0o755)
    (corpus / "one.def").write_text("VERSION 5.8 ;\n", encoding="utf-8")
    selection = tmp_path / "selection.json"
    selection.write_text('{"schema":1}\n', encoding="utf-8")
    plan = tmp_path / "progress.json"
    plan.write_text(canonical({
        "schema": 1,
        "scope": "candidate-arm",
        "stall_grace_seconds": 1,
        "units": ["load", "check"],
    }) + "\n", encoding="utf-8")
    docker = tmp_path / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    state = tmp_path / "docker-state"
    output = tmp_path / "output"
    receipt = tmp_path / "receipt.json"
    return {
        "subject": subject, "runtime": runtime, "corpus": corpus,
        "selection": selection,
        "plan": plan, "docker": docker, "state": state, "output": output,
        "receipt": receipt,
    }


def invoke(case, *, behavior="good", command=None, tamper=None,
           writable_dest=None):
    env = dict(os.environ)
    env["FAKE_DOCKER_STATE"] = str(case["state"])
    env["FAKE_DOCKER_BEHAVIOR"] = behavior
    # The world holds the image the runner resolved — see the note above IMAGE.
    env["FAKE_DOCKER_IMAGE"] = runner.IMAGE
    if tamper is not None:
        env["FAKE_DOCKER_TAMPER_PATH"] = str(tamper)
    if writable_dest is not None:
        env["FAKE_DOCKER_WRITABLE_DEST"] = writable_dest
    cmd = [
        sys.executable, str(RUNNER_PATH), "run",
        "--docker-bin", str(case["docker"]),
        "--subject", str(case["subject"]),
        "--runtime", str(case["runtime"]),
        "--overlay", "candidate.py",
        "--env", "GATEKEEPER_BENCHMARK_DATA_SHA=" + "b" * 40,
        "--env", "GATEKEEPER_VERIFY_ARM=A1",
        "--env", "VIBEIC_PYTEST_PROGRESS_FILE=/evidence/pytest-progress.jsonl",
        "--env", "VIBEIC_PYTEST_PROGRESS_NONCE=" + "d" * 64,
        "--corpus", str(case["corpus"]),
        "--selection", str(case["selection"]),
        "--progress-plan", str(case["plan"]),
        "--output-dir", str(case["output"]),
        "--receipt", str(case["receipt"]),
        "--", *(command or ["/subject/candidate.py"]),
    ]
    return subprocess.run(cmd, env=env, text=True, capture_output=True)


def calls(case):
    return [json.loads(line) for line in
            (case["state"] / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


def test_fake_docker_exact_profile_lifecycle_and_canonical_receipt(case):
    proc = invoke(case)
    assert proc.returncode == 0, proc.stderr
    assert (case["output"] / "result.txt").read_text() == "candidate evidence\n"
    assert (case["output"] / "runner-stdout.bin").is_file()
    assert (case["output"] / "runner-stderr.bin").is_file()
    receipt = runner.strict_load_receipt(case["receipt"])
    assert receipt["image"]["reference"] == runner.IMAGE
    assert receipt["image"]["platform"] == "linux/amd64"
    assert receipt["container"]["user"] == "65534:65534"
    assert receipt["container"]["network"] == "none"
    assert receipt["container"]["read_only_rootfs"] is True
    assert receipt["container"]["cap_drop"] == ["ALL"]
    assert receipt["container"]["no_new_privileges"] is True
    assert receipt["container"]["launcher"] == ["/usr/bin/env", "-i", "--"]
    process_env = dict(item.split("=", 1)
                       for item in receipt["container"]["process_environment"])
    assert process_env["GATEKEEPER_RUNTIME_ROOT"] == "/runtime"
    assert process_env["VIBEIC_REQUIRE_TRUSTED_PYTEST_ENTRY"] == "1"
    assert process_env["GATEKEEPER_VERIFY_ARM"] == "A1"
    assert process_env["GATEKEEPER_BENCHMARK_DATA_SHA"] == "b" * 40
    assert process_env["VIBEIC_PYTEST_SEMANTIC_STALL_GRACE"] == "600"
    assert process_env["VIBE_IC_BENCHMARK_DATA"] == "/corpus"
    assert process_env["VIBEIC_PYTEST_PROGRESS_FILE"] == \
        "/evidence/pytest-progress.jsonl"
    assert process_env["VIBEIC_PYTEST_PROGRESS_NONCE"] == "d" * 64
    assert "STARTUPDIR" not in process_env
    assert "PYTHONPATH" not in process_env
    assert "GATEKEEPER_HYGIENE_REPORT" not in process_env
    assert process_env["HOME"] == "/nonexistent"
    assert process_env["TMPDIR"] == "/var/tmp"
    assert receipt["container"]["tmpfs"] == {
        "/tmp": "rw,nosuid,nodev,noexec,size=536870912,mode=1777",
        "/nonexistent": "rw,nosuid,nodev,noexec,size=1048576,mode=0700,uid=65534,gid=65534",
        "/var/tmp": "rw,nosuid,nodev,exec,size=536870912,mode=0700,uid=65534,gid=65534",
    }
    assert receipt["result"] == {
        "attach_exit_code": 0, "dead": False, "exit_code": 0,
        "oom_killed": False, "pid": 0, "pid_dead": True,
        "restarting": False, "running": False, "status": "exited",
    }
    assert receipt["cleanup"] == {
        "container_absent": True, "exporter_absent": True,
        "provisioner_absent": True,
        "volume_absent": True,
    }
    assert receipt["progress"]["completed"] == 2
    assert receipt["inputs"]["overlays"] == [{
        "destination": "/subject/candidate.py",
        "path": "candidate.py",
        "source_file": receipt["inputs"]["overlays"][0]["source_file"],
    }]
    overlay_mount = next(
        row for row in receipt["container"]["mounts"]
        if row["role"] == "runtime_overlay:candidate.py")
    assert overlay_mount["destination"] == "/subject/candidate.py"
    assert overlay_mount["read_only"] is True
    artifacts = {row["path"]: row for row in receipt["artifacts"]["files"]}
    assert artifacts["result.txt"] == {
        "mode": "100644", "path": "result.txt",
        "sha256": "bb3f2f5d2e170589f3cf7241d41e4b8ba07ce70ad92dd1f3f5795e0e87262f1f",
        "size": 19,
    }
    assert artifacts["runner-stdout.bin"] == {
        "mode": "100644", "path": "runner-stdout.bin",
        **receipt["streams"]["stdout"],
    }
    assert artifacts["runner-stderr.bin"] == {
        "mode": "100644", "path": "runner-stderr.bin",
        **receipt["streams"]["stderr"],
    }
    raw = case["receipt"].read_bytes()
    assert raw == runner._canonical(receipt) + b"\n"

    history = calls(case)
    def candidate_call(row):
        return (any(value.startswith("vibeic-candidate-") for value in row)
                and not any("-export-" in value or "-provision-" in value
                            for value in row))

    create = next(row for row in history
                  if row[:2] == ["container", "create"] and candidate_call(row))
    for flag in ("--pull=never", "--read-only", "--cap-drop", "--security-opt",
                 "--network", "--user", "--tmpfs"):
        assert flag in create
    start_index = next(i for i, row in enumerate(history)
                       if row[:2] == ["container", "start"] and candidate_call(row))
    stopped_inspect_index = next(i for i, row in enumerate(history[start_index + 1:],
                                                        start_index + 1)
                                 if row[:2] == ["container", "inspect"])
    exporter_create_index = next(
        i for i, row in enumerate(history)
        if row[:2] == ["container", "create"] and
        any("vibeic-candidate-export-" in value for value in row))
    exporter_start_index = next(
        i for i, row in enumerate(history)
        if row[:2] == ["container", "start"] and
        any("vibeic-candidate-export-" in value for value in row))
    rm_index = next(i for i, row in enumerate(history)
                    if row[:2] == ["container", "rm"] and
                    any("vibeic-candidate-export-" in value for value in row))
    absent_index = next(i for i, row in enumerate(history[rm_index + 1:], rm_index + 1)
                        if row[:2] == ["container", "inspect"])
    assert (start_index < stopped_inspect_index < exporter_create_index
            < exporter_start_index < rm_index < absent_index)
    assert not (case["state"] / "container.json").exists()
    assert not (case["state"] / "exporter.json").exists()
    assert not (case["state"] / "provisioner.json").exists()
    assert not (case["state"] / "volume.json").exists()

    verify = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "verify-receipt", str(case["receipt"])],
        text=True, capture_output=True,
    )
    assert verify.returncode == 0, verify.stderr
    assert verify.stdout.strip() == receipt["receipt_sha256"]


def test_natural_candidate_failure_is_recorded_not_norecord(case):
    proc = invoke(case, behavior="exit7")
    assert proc.returncode == 1, proc.stderr
    receipt = runner.strict_load_receipt(case["receipt"])
    assert receipt["result"]["exit_code"] == 7
    assert receipt["result"]["attach_exit_code"] == 7
    assert receipt["cleanup"]["container_absent"] is True


@pytest.mark.parametrize("behavior", ["malformed", "duplicate", "nan"])
def test_malformed_progress_is_norecord_and_cleanup_is_owned(case, behavior):
    proc = invoke(case, behavior=behavior)
    assert proc.returncode == 2
    assert "[NORECORD]" in proc.stderr
    assert not case["receipt"].exists()
    assert not case["output"].exists()
    assert not (case["state"] / "container.json").exists()
    assert not (case["state"] / "volume.json").exists()
    rows = calls(case)
    # Failure-side export is permitted only after killing the owned candidate
    # and inspecting its stopped state. Success output/receipt remain absent.
    exported = [i for i, row in enumerate(rows)
                if row[:2] == ["container", "create"]
                and any("vibeic-candidate-export-" in value for value in row)]
    killed = [i for i, row in enumerate(rows) if row[:2] == ["container", "kill"]]
    assert all(killed and killed[0] < index for index in exported)


def test_semantic_stall_has_no_total_runtime_verdict_and_cleans(case):
    proc = invoke(case, behavior="stall")
    assert proc.returncode == 2
    assert "semantic progress stalled" in proc.stderr
    assert "no elapsed-runtime verdict" in proc.stderr
    assert "completed=0/2; last=<none>; next=load" in proc.stderr
    assert not case["receipt"].exists()
    assert not (case["state"] / "container.json").exists()
    assert not (case["state"] / "exporter.json").exists()
    assert not (case["state"] / "provisioner.json").exists()
    assert not (case["state"] / "volume.json").exists()


@pytest.mark.parametrize("behavior, exported, attach_signal, refusal", [
    pytest.param("stall", True, None, None, id="stall-True"),
    pytest.param("stall_stop_unproven", False, None, "dead-PID exit",
                 id="stall_stop_unproven-False"),
    pytest.param("stall", True, "exited", None,
                 id="exit-between-poll-and-signal"),
    pytest.param("stall_stop_unproven", False, "exited", "dead-PID exit",
                 id="exit-with-stop-unproven"),
    pytest.param("stall", False, "denied", "Operation not permitted",
                 id="signal-denied"),
])
def test_failed_run_retains_diagnostics_without_a_success_receipt(
    case, monkeypatch, behavior, exported, attach_signal, refusal,
):
    if attach_signal is not None:
        # Delay only the fake attach client's already-killed exit so the real
        # poll sees it alive. The wrapper lets it exit before the signal call;
        # production retention, stopped proof and export remain unmodified.
        before = 'if current.get("Killed"):\n                raise SystemExit(137)'
        after = ('if current.get("Killed"):\n                time.sleep(0.2)'
                 '\n                raise SystemExit(137)')
        fixture = case["docker"].read_text()
        assert fixture.count(before) == 1
        case["docker"].write_text(fixture.replace(before, after))
        wrapper = case["output"].parent / "attach_signal_wrapper.py"
        trace = wrapper.with_suffix(".json")
        wrapper.write_text(f'''
import errno
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, {str(HERE)!r})
import hermetic_candidate_runner as R
original = R._run_monitored
def monitored(docker, resources, *args, **kwargs):
    try:
        return original(docker, resources, *args, **kwargs)
    except R.Refusal:
        attach = resources.attach
        real_killpg = os.killpg
        def at_signal(pid, sig):
            if attach is not None and pid == attach.pid:
                os.killpg = real_killpg
                # Reaching this call proves the production poll returned None.
                rc = attach.wait(timeout=5)
                try:
                    if {attach_signal!r} == "denied":
                        raise PermissionError(errno.EPERM, os.strerror(errno.EPERM))
                    return real_killpg(pid, sig)
                except OSError as exc:
                    Path({str(trace)!r}).write_text(json.dumps(dict(
                        poll_return_before_exit=None, attach_exit_before_killpg=rc,
                        signal=sig, errno=exc.errno)))
                    raise
            return real_killpg(pid, sig)
        os.killpg = at_signal
        raise
R._run_monitored = monitored
raise SystemExit(R.main())
''')
        monkeypatch.setitem(globals(), "RUNNER_PATH", wrapper)
    proc = invoke(case, behavior=behavior)
    if attach_signal is not None:
        schedule = json.loads(trace.read_text())
        assert schedule["poll_return_before_exit"] is None
        assert schedule["attach_exit_before_killpg"] == 137
        assert schedule["signal"] == signal.SIGTERM
        assert schedule["errno"] == (
            errno.ESRCH if attach_signal == "exited" else errno.EPERM)
    assert proc.returncode == 2, proc.stderr
    paths = list(case["output"].parent.glob(case["output"].name + ".failure-diagnostics-*"))
    assert len(paths) == 1, proc.stderr
    diagnostic = paths[0]
    doc = json.loads((diagnostic / "FAILURE_DIAGNOSTICS.json").read_text())
    assert doc["status"] == "NORECORD"
    assert doc["completed"] == 0
    assert "semantic progress stalled" in doc["reason"]
    assert (diagnostic / "stdout.bin").read_bytes().startswith(b"VIBEIC_PROGRESS ")
    assert (diagnostic / "stderr.bin").read_text() == "raw failure diagnostic\n"
    plan = json.loads((diagnostic / "progress-plan.json").read_text())
    first = json.loads((diagnostic / "accepted-progress.jsonl").read_text())
    assert plan["nonce"] == first["nonce"]
    assert plan["units"] == ["load", "check"]
    assert not case["receipt"].exists()
    assert not case["output"].exists()
    if exported:
        assert doc["raw_volume"] == "EXPORTED_AFTER_STOP"
        assert (diagnostic / "raw-sidecars/raw-progress.jsonl").read_text() == (
            '{"event":"collection-started"}\n')
        rows = calls(case)
        killed = next(i for i, row in enumerate(rows)
                      if row[:2] == ["container", "kill"])
        created = next(i for i, row in enumerate(rows)
                       if row[:2] == ["container", "create"]
                       and any("vibeic-candidate-export-" in v for v in row))
        inspections = [i for i, row in enumerate(rows)
                       if row == ["container", "inspect", rows[killed][-1]]
                       and i > killed]
        assert inspections[0] < created < inspections[1]
    else:
        assert doc["raw_volume"] == "NOT_EXPORTED"
        assert refusal in doc["raw_volume_reason"]
        assert not (diagnostic / "raw-sidecars").exists()
        assert not any("vibeic-candidate-export-" in value
                       for row in calls(case) for value in row)
    assert not (case["state"] / "container.json").exists()
    assert not (case["state"] / "volume.json").exists()


def test_receipt_counts_scan_work_separately_from_completion(case):
    plan = json.loads(case["plan"].read_text())
    plan["units"] = ["pytest:collection-complete", "pytest:test_one.py",
                     "pytest:record-published"]
    case["plan"].write_text(canonical(plan) + "\n")
    proc = invoke(case, behavior="collect_scan")
    assert proc.returncode == 0, proc.stderr
    receipt = runner.strict_load_receipt(case["receipt"])
    assert receipt["progress"]["completed"] == 3
    assert receipt["progress"]["collect_scanned"] == 1000
    assert receipt["progress"]["records"] == 6
    for scanned, records in ((1000, 5), (0, 6), (1001, 6), (1001000, 1006)):
        changed = json.loads(case["receipt"].read_text())
        changed["progress"].update(collect_scanned=scanned, records=records)
        with pytest.raises(ValueError, match="progress|scan|collection"):
            runner.validate_receipt(changed)


@pytest.mark.parametrize("behavior", ["wrong_user", "extra_mount"])
def test_profile_drift_refuses_before_candidate_start(case, behavior):
    proc = invoke(case, behavior=behavior)
    assert proc.returncode == 2
    assert not any(row[:2] == ["container", "start"] for row in calls(case))
    assert not (case["state"] / "container.json").exists()
    assert not (case["state"] / "provisioner.json").exists()
    assert not (case["state"] / "volume.json").exists()


@pytest.mark.parametrize("behavior", [
    "missing_home", "missing_scratch", "exec_tmp", "home_unowned", "host_home_mount",
])
def test_private_account_and_scratch_drift_refuses_before_candidate_start(case, behavior):
    proc = invoke(case, behavior=behavior)
    assert proc.returncode == 2, proc.stderr
    assert "private tmpfs" in proc.stderr
    candidate_starts = [row for row in calls(case)
        if row[:2] == ["container", "start"]
        and not any("-provision-" in item or "-export-" in item for item in row)]
    assert candidate_starts == []
    assert not case["receipt"].exists()
    assert not (case["state"] / "container.json").exists()
    assert not (case["state"] / "volume.json").exists()


@pytest.mark.parametrize("destination", ["/tmp", "/nonexistent", "/var/tmp"])
def test_private_tmpfs_receipt_cannot_lose_isolation(case, destination):
    proc = invoke(case)
    assert proc.returncode == 0, proc.stderr
    receipt = runner.strict_load_receipt(case["receipt"])
    receipt["container"]["tmpfs"].pop(destination)
    # Rebind the digest: this must be a profile refusal, not merely bad hashing.
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    receipt["receipt_sha256"] = runner._sha256(runner._canonical(body))
    with pytest.raises(ValueError, match="container profile"):
        runner.validate_receipt(receipt)


def test_a_read_write_subject_bind_refuses_before_the_candidate_starts(case):
    """The arm's subject bind must be READ-ONLY, and that must be enforced.

    Nothing exercised this branch before. It is the property that makes a
    candidate arm structurally unable to pre-write the base wave's artifacts:
    the parent's run directory is not mounted into the arm at all, and every
    bind it does get is read-only. `wrong_user`/`extra_mount` above cannot
    reach it -- they perturb every container, so the evidence-volume
    provisioner refuses first and the candidate profile is never inspected.
    """
    proc = invoke(case, behavior="rw_bind")
    assert proc.returncode == 2
    # Refusing for the wrong reason is not a pass: name the owning branch.
    assert "subject bind is not exact/read-only" in proc.stderr, proc.stderr
    started = [row[-1] for row in calls(case)
               if row[:2] == ["container", "start"]]
    assert started, "the provisioner never ran, so this is not the branch above"
    assert not any("-provision-" not in name and "-export-" not in name
                   for name in started), started
    assert not case["receipt"].exists()


@pytest.mark.parametrize("behavior", ["oom", "restarting"])
def test_oom_or_restart_cannot_be_misreported_as_natural_exit(case, behavior):
    proc = invoke(case, behavior=behavior)
    assert proc.returncode == 2
    assert "dead-PID exit" in proc.stderr
    assert not case["receipt"].exists()


def test_receipt_duplicate_nan_noncanonical_and_digest_tamper_refuse(case, tmp_path):
    proc = invoke(case)
    assert proc.returncode == 0, proc.stderr
    raw = case["receipt"].read_bytes()
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(raw.replace(b'"schema":1', b'"schema":1,"schema":1', 1))
    with pytest.raises(ValueError, match="duplicate"):
        runner.strict_load_receipt(duplicate)
    nan = tmp_path / "nan.json"
    nan.write_bytes(raw.replace(b'"schema":1', b'"schema":NaN', 1))
    with pytest.raises(ValueError, match="non-finite"):
        runner.strict_load_receipt(nan)
    pretty = tmp_path / "pretty.json"
    pretty.write_text(json.dumps(json.loads(raw), indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="not canonical"):
        runner.strict_load_receipt(pretty)
    tampered = tmp_path / "tampered.json"
    doc = json.loads(raw)
    doc["result"]["exit_code"] = 1
    tampered.write_bytes(runner._canonical(doc) + b"\n")
    with pytest.raises(ValueError):
        runner.strict_load_receipt(tampered)


def test_signal_cleanup_removes_owned_container_and_volume(case):
    env = dict(os.environ)
    env["FAKE_DOCKER_STATE"] = str(case["state"])
    env["FAKE_DOCKER_BEHAVIOR"] = "stall"
    # The same explicit handoff `invoke()` makes: the fake world holds the image
    # the runner demands (#2100), which is `runner.IMAGE`, not the pin's export.
    env["FAKE_DOCKER_IMAGE"] = runner.IMAGE
    cmd = [
        sys.executable, str(RUNNER_PATH), "run",
        "--docker-bin", str(case["docker"]), "--subject", str(case["subject"]),
        "--runtime", str(case["runtime"]), "--overlay", "candidate.py",
        "--env", "GATEKEEPER_BENCHMARK_DATA_SHA=" + "b" * 40,
        "--env", "GATEKEEPER_VERIFY_ARM=A1",
        "--env", "VIBEIC_PYTEST_PROGRESS_FILE=/evidence/pytest-progress.jsonl",
        "--env", "VIBEIC_PYTEST_PROGRESS_NONCE=" + "d" * 64,
        "--corpus", str(case["corpus"]), "--selection", str(case["selection"]),
        "--progress-plan", str(case["plan"]), "--output-dir", str(case["output"]),
        "--receipt", str(case["receipt"]), "--", "/subject/candidate.py",
    ]
    proc = subprocess.Popen(cmd, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        path = case["state"] / "container.json"
        if path.exists():
            try:
                if json.loads(path.read_text())["State"]["Running"]:
                    break
            except (json.JSONDecodeError, KeyError):
                pass
        time.sleep(0.02)
    else:
        proc.kill()
        pytest.fail("fake candidate did not reach running state")
    proc.send_signal(signal.SIGTERM)
    stdout, stderr = proc.communicate()
    assert proc.returncode == 128 + signal.SIGTERM, (stdout, stderr)
    assert not (case["state"] / "container.json").exists()
    assert not (case["state"] / "volume.json").exists()
    assert not case["receipt"].exists()


def test_host_home_and_symlink_inputs_are_never_mounted(case, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(runner, "_home_path", lambda: fake_home.resolve())
    inside = fake_home / "subject"
    inside.mkdir()
    args = type("Args", (), {
        "command": ["--", "/subject/candidate.py"], "output_dir": case["output"],
        "receipt": case["receipt"], "subject": inside, "corpus": case["corpus"],
        "runtime": case["runtime"], "overlay": ["candidate.py"],
        "env": ["GATEKEEPER_BENCHMARK_DATA_SHA=" + "b" * 40,
                "GATEKEEPER_VERIFY_ARM=A1"],
        "selection": case["selection"], "progress_plan": case["plan"],
        "docker_bin": str(case["docker"]),
    })()
    with pytest.raises(runner.Refusal, match="host HOME"):
        runner.run(args)
    link = tmp_path / "selection-link"
    link.symlink_to(case["selection"])
    # resolve() intentionally points to the safe target, so tree inputs instead
    # carry the special-path refusal exercised below.
    (case["subject"] / "escape").symlink_to("/etc/passwd")
    with pytest.raises(runner.Refusal, match="non-regular|symlink/special"):
        runner._tree_digest(case["subject"], "subject")


def test_overlay_manifest_is_sorted_regular_and_present_in_both_trees(case):
    (case["runtime"] / "z.py").write_text("#!/usr/bin/python3\n")
    (case["runtime"] / "z.py").chmod(0o755)
    (case["subject"] / "z.py").write_text("candidate bytes\n")
    with pytest.raises(runner.Refusal, match="sorted unique"):
        runner._overlay_paths(
            ["z.py", "candidate.py"], case["runtime"], case["subject"])
    with pytest.raises(runner.Refusal, match="missing"):
        runner._overlay_paths(["missing.py"], case["runtime"], case["subject"])
    (case["runtime"] / "linked.py").symlink_to("candidate.py")
    (case["subject"] / "linked.py").write_text("candidate bytes\n")
    with pytest.raises(runner.Refusal, match="not regular"):
        runner._overlay_paths(["linked.py"], case["runtime"], case["subject"])


def test_reviewed_environment_is_arm_conditional_and_path_bound():
    test = [
        "GATEKEEPER_BENCHMARK_DATA_SHA=" + "b" * 40,
        "GATEKEEPER_VERIFY_ARM=A1",
        "VIBEIC_PYTEST_PROGRESS_FILE=/evidence/pytest-progress.jsonl",
        "VIBEIC_PYTEST_PROGRESS_NONCE=" + "d" * 64,
    ]
    assert runner._reviewed_process_env(test) == {
        "GATEKEEPER_BENCHMARK_DATA_SHA": "b" * 40,
        "GATEKEEPER_VERIFY_ARM": "A1",
        "VIBEIC_PYTEST_PROGRESS_FILE": "/evidence/pytest-progress.jsonl",
        "VIBEIC_PYTEST_PROGRESS_NONCE": "d" * 64,
    }
    with pytest.raises(runner.Refusal, match="missing"):
        runner._reviewed_process_env(["GATEKEEPER_VERIFY_ARM=A1"])
    test_fixed = runner._fixed_process_env("A1")
    assert test_fixed["VIBEIC_PYTEST_SEMANTIC_STALL_GRACE"] == "600"
    with pytest.raises(runner.Refusal, match="not in the reviewed allowlist"):
        runner._reviewed_process_env(sorted(test + [
            "VIBEIC_PYTEST_SEMANTIC_STALL_GRACE=601",
        ]))
    with pytest.raises(runner.Refusal, match="excess"):
        runner._reviewed_process_env(sorted(test + [
            "GATEKEEPER_HYGIENE_REPORT=/evidence/hygiene.json",
        ]))
    land = [
        "GATEKEEPER_BASE=" + "a" * 40,
        "GATEKEEPER_BENCHMARK_DATA_SHA=" + "b" * 64,
        "GATEKEEPER_HYGIENE_PROGRESS=/evidence/hygiene-progress.jsonl",
        "GATEKEEPER_HYGIENE_REPORT=/evidence/hygiene.json",
        "GATEKEEPER_VERIFY_ARM=B2",
        "GATEKEEPER_VERSION_BY_GATEKEEPER=1",
        "VIBEIC_LANDING_PROGRESS_NONCE=" + "c" * 64,
    ]
    assert set(runner._reviewed_process_env(land)) == runner._LAND_REVIEWED_ENV_NAMES
    land_fixed = runner._fixed_process_env("B2")
    assert land_fixed["GATEKEEPER_NO_STAMP"] == "1"
    assert land_fixed["GATEKEEPER_SKIP_TARGETED_TESTS"] == "1"
    assert land_fixed["VIBE_IC_BENCHMARK_DATA"] == "/corpus"
    assert land_fixed["VIBEIC_LANDING_COMPLETION"] == \
        "/evidence/landing-completion.json"
    assert land_fixed["VIBEIC_LANDING_PROGRESS"] == \
        "/evidence/landing-progress.jsonl"
    assert land_fixed["GIT_CONFIG_COUNT"] == "2"
    assert land_fixed["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert land_fixed["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert land_fixed["GIT_CONFIG_NOSYSTEM"] == "1"
    assert land_fixed["GIT_CONFIG_VALUE_0"] == "/subject"
    assert land_fixed["GIT_CONFIG_KEY_1"] == "safe.directory"
    assert land_fixed["GIT_CONFIG_VALUE_1"] == "/corpus"
    assert land_fixed["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert "PYTHONPATH" not in land_fixed
    bad_version = list(land)
    bad_version[5] = "GATEKEEPER_VERSION_BY_GATEKEEPER=2"
    with pytest.raises(runner.Refusal, match="must be 0 or 1"):
        runner._reviewed_process_env(bad_version)
    active_version = list(land)
    active_version[5] = "GATEKEEPER_VERSION_BY_GATEKEEPER=0"
    assert runner._reviewed_process_env(active_version)[
        "GATEKEEPER_VERSION_BY_GATEKEEPER"] == "0"
    bad_base = list(land)
    bad_base[0] = "GATEKEEPER_BASE=refs/heads/main"
    with pytest.raises(runner.Refusal, match="full lowercase object digest"):
        runner._reviewed_process_env(bad_base)
    with pytest.raises(runner.Refusal, match="not in the reviewed allowlist"):
        runner._reviewed_process_env(sorted(land + [
            "GATEKEEPER_BENCHMARK_MEASUREMENT_RECORD=/input/measurement.json",
        ]))
    bad = list(land)
    bad[2] = "GATEKEEPER_HYGIENE_PROGRESS=/tmp/leak"
    with pytest.raises(runner.Refusal, match="under evidence"):
        runner._reviewed_process_env(bad)


def test_live_exact_image_capability_and_profile(tmp_path):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI unavailable")
    available = subprocess.run(
        [docker, "image", "inspect", runner.IMAGE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if available.returncode != 0:
        pytest.skip("exact digest-pinned image unavailable")
    subject = tmp_path / "subject"
    runtime = tmp_path / "runtime"
    corpus = tmp_path / "corpus"
    subject.mkdir()
    runtime.mkdir()
    corpus.mkdir()
    (subject / "input.txt").write_text("subject\n")
    (corpus / "input.def").write_text("VERSION 5.8 ;\n")
    selection = tmp_path / "selection.json"
    selection.write_text("{}\n")
    plan = tmp_path / "progress.json"
    plan.write_text(canonical({
        "schema": 1, "scope": "live", "stall_grace_seconds": 30,
        "units": ["write"],
    }) + "\n")
    output = tmp_path / "output"
    receipt = tmp_path / "receipt.json"
    script = r'''#!/bin/sh
set -eu
test "$GATEKEEPER_RUNTIME_ROOT" = /runtime
test "$VIBEIC_REQUIRE_TRUSTED_PYTEST_ENTRY" = 1
test "$GATEKEEPER_VERIFY_ARM" = A1
test "$VIBE_IC_BENCHMARK_DATA" = /corpus
test "$GATEKEEPER_BENCHMARK_DATA_SHA" = bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
test "$VIBEIC_PYTEST_SEMANTIC_STALL_GRACE" = 600
test -z "${STARTUPDIR+x}"
printf 'VIBEIC_PROGRESS {"nonce":"%s","schema":1,"scope":"live","seq":0,"state":"start","total":1}\n' "$VIBEIC_HERMETIC_PROGRESS_NONCE"
printf 'candidate evidence\n' > "$VIBEIC_HERMETIC_EVIDENCE_PATH/result.txt"
printf 'VIBEIC_PROGRESS {"completed":1,"nonce":"%s","schema":1,"scope":"live","seq":1,"state":"checkpoint","total":1,"unit":"write"}\n' "$VIBEIC_HERMETIC_PROGRESS_NONCE"
printf 'VIBEIC_PROGRESS {"completed":1,"nonce":"%s","schema":1,"scope":"live","seq":2,"state":"terminal","total":1}\n' "$VIBEIC_HERMETIC_PROGRESS_NONCE"
'''
    (subject / "run.sh").write_text("#!/bin/sh\nexit 99\n")
    (runtime / "run.sh").write_text(script)
    (runtime / "run.sh").chmod(0o755)
    proc = subprocess.run([
        sys.executable, str(RUNNER_PATH), "run", "--docker-bin", docker,
        "--subject", str(subject), "--runtime", str(runtime),
        "--overlay", "run.sh", "--corpus", str(corpus),
        "--env", "GATEKEEPER_BENCHMARK_DATA_SHA=" + "b" * 40,
        "--env", "GATEKEEPER_VERIFY_ARM=A1",
        "--env", "VIBEIC_PYTEST_PROGRESS_FILE=/evidence/pytest-progress.jsonl",
        "--env", "VIBEIC_PYTEST_PROGRESS_NONCE=" + "d" * 64,
        "--selection", str(selection), "--progress-plan", str(plan),
        "--output-dir", str(output), "--receipt", str(receipt),
        "--", "/subject/run.sh",
    ], text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert (output / "result.txt").read_text() == "candidate evidence\n"
    assert (output / "runner-stdout.bin").is_file()
    loaded = runner.strict_load_receipt(receipt)
    assert loaded["image"]["reference"] == runner.IMAGE
    assert loaded["cleanup"] == {
        "container_absent": True, "exporter_absent": True,
        "provisioner_absent": True,
        "volume_absent": True,
    }


#: Enough concurrent pairs that the race cannot hide. The unfixed stub leaked on
#: 41 of 200 trials (~20%), so P(a broken stub shows zero leaks here) is about
#: 0.8**60 -- roughly one in seven hundred thousand. Fewer trials would make
#: this guard itself flaky, which is the failure it exists to remove.
_RACE_TRIALS = 60


def test_the_fake_docker_serialises_kill_against_rm(case):
    """A container the runner REMOVED must not come back.

    `test_malformed_progress_is_norecord_and_cleanup_is_owned` was
    intermittently red -- green three times in a row one hour and red in every
    interleaved round the next -- and the only failing assertion was that
    `container.json` is gone. The leftover file was ZERO BYTES, which named it:
    the runner drives `kill` and `rm --force` concurrently during teardown, and
    the stub did load -> mutate -> save with nothing holding the ends together,
    so `rm` could unlink between them and `save` resurrected the file.

    This drives that pair directly instead of waiting for host load to expose
    it. Measured over the stub at each commit: 41 leaks in 200 trials before the
    fix, 0 after.
    """
    state = case["state"]
    state.mkdir(parents=True, exist_ok=True)
    name = "vibeic-candidate-race-probe"
    doc = {
        "Name": "/" + name, "Id": "2" * 64,
        "State": {"Running": True, "Status": "running", "ExitCode": 0,
                  "Pid": 1234, "Restarting": False},
        "Mounts": [], "Config": {}, "HostConfig": {},
    }
    env = dict(os.environ)
    env["FAKE_DOCKER_STATE"] = str(state)

    def drive(argv):
        subprocess.run([sys.executable, str(case["docker"])] + argv,
                       env=env, capture_output=True, text=True)

    survivors = []
    for _ in range(_RACE_TRIALS):
        (state / "container.json").write_text(json.dumps(doc), encoding="utf-8")
        killer = threading.Thread(target=drive,
                                  args=(["container", "kill", name],))
        remover = threading.Thread(target=drive,
                                   args=(["container", "rm", "--force", name],))
        killer.start(); remover.start()
        killer.join(); remover.join()
        leftover = state / "container.json"
        if leftover.exists():
            survivors.append(leftover.stat().st_size)
            leftover.unlink()

    assert not survivors, (
        f"{len(survivors)} of {_RACE_TRIALS} kill/rm races left a container "
        f"the runner had removed, sizes {sorted(set(survivors))}. A `save` that "
        "lands after an `unlink` resurrects torn-down state, and a stub killed "
        "mid-write leaves a zero-byte document where a valid one or none are "
        "the only honest states.")




# ===========================================================================
# THE PARENT-OWNED INPUTS ARE POST-ATTESTED
# ===========================================================================
@pytest.mark.parametrize("owned", ["corpus", "subject", "selection"])
def test_a_parent_owned_input_changed_during_the_arm_is_refused(case, owned):
    """The properties `test_landing_merge_verdict` names, asserted against the
    interface THIS repository actually has.

    WHY THIS TEST EXISTS. Six landing properties -- a green test cannot move B1
    to another commit; index flags cannot hide changed B1 bytes; replace-refs
    cannot redefine the verified tree; the caller's checkout is never touched;
    a relinked parent selection is NORECORD; a B2 corpus mutation is
    post-attested and NORECORD -- are all ONE mechanism here:

        final_inputs[k] != initial_inputs[k]
            -> Refusal("candidate input changed between pre-arm and stopped copy")

    MEASURED 2026-08-22 on a4caccefe: that sentence occurs EXACTLY ONCE in the
    whole repository, in the implementation, and in no test. The six tests that
    would have covered these properties asserted them through a DIFFERENT
    design's interface -- shell messages such as "changed or could not
    re-attest" that this implementation never emits -- so they are red, and
    they guard nothing. Deleting any clause of the comparison above would
    therefore have been caught by nothing at all.

    The tamper is driven through the fake docker, which fires strictly between
    the initial and the final digest, so what is exercised is the real
    comparison and not a stub of it. `subject` and `selection` are included
    because the same clause is what makes the B1 properties hold; parametrising
    proves the guard is per-input and not a single lucky branch.
    """
    target = {
        "corpus": case["corpus"] / "one.def",
        "subject": case["subject"] / "candidate.py",
        "selection": case["selection"],
    }[owned]
    before = target.read_bytes()
    proc = invoke(case, tamper=target)
    assert target.read_bytes() != before, (
        "the escape hook did not fire, so this arm proves nothing")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "candidate input changed between pre-arm and stopped copy" in (
        proc.stdout + proc.stderr), proc.stdout + proc.stderr
    assert not case["receipt"].exists(), (
        "a run whose inputs moved under it must leave NO receipt -- that is "
        "what makes it NORECORD rather than a recorded pass")


def test_the_same_run_without_the_tamper_is_recorded(case):
    """The paired control. Without it the test above could pass because the
    harness refuses everything, which is the failure mode a one-sided
    containment test always has."""
    proc = invoke(case)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "candidate input changed between pre-arm and stopped copy" not in (
        proc.stdout + proc.stderr)
    assert case["receipt"].exists(), "the clean run must leave a receipt"


@pytest.mark.parametrize("destination", ["/corpus", "/subject", "/runtime"])
def test_a_writable_parent_owned_bind_is_refused_before_the_candidate_runs(
        case, destination):
    """The PREVENTION half of the same six properties, and the layer that makes
    the post-attestation above belt-and-braces rather than the only defence.

    The candidate cannot move B1, hide changed B1 bytes, redefine the verified
    tree or mutate the corpus because it cannot WRITE any of them: every
    parent-owned bind is mounted read-only, and the runner re-reads the mount
    table the daemon reports and refuses if what actually got mounted is
    writable --

        if item.get("RW") is not False:
            raise Refusal(f"candidate {role} bind is not exact/read-only")

    MEASURED 2026-08-22 on a4caccefe: that sentence, like the post-attestation
    one, appears ONCE in the repository and in no test. The only read-only
    assertion anywhere covered the runtime OVERLAY -- not corpus, not subject,
    not runtime, which are the three that carry the properties.

    Asked through a daemon that REPORTS a writable bind, because trusting the
    flags the runner itself passed would test nothing: the whole point of
    re-reading the mount table is that the daemon is not assumed to have obeyed.
    """
    proc = invoke(case, writable_dest=destination)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "is not exact/read-only" in (proc.stdout + proc.stderr), (
        proc.stdout + proc.stderr)
    assert not case["receipt"].exists(), (
        "a run that could not vouch for its own mounts must leave NO receipt")


# ---------------------------------------------------------------------------
# The pin: the DIGEST is the identity, the REPOSITORY is deployment config.
#
# `_image_profile` is the gate that binds the runtime. It must accept exactly one
# thing -- an image whose RepoDigests contains `<configured repo>@<pinned digest>`
# -- and refuse everything else. The two cases worth naming are the ones that are
# "nearly right", because those are the ones a careless change makes pass:
# the right bytes offered under a repository nobody configured, and the
# configured repository offering the wrong bytes.
# ---------------------------------------------------------------------------

PINNED_DIGEST = runner.IMAGE_DIGEST
OTHER_DIGEST = "sha256:" + "b" * 64


class _StubDocker:
    """Answers `image inspect` with a document under test; records nothing else."""

    def __init__(self, doc, returncode=0):
        self._doc = doc
        self._rc = returncode

    def call(self, args):
        payload = json.dumps([self._doc]).encode() if self._doc is not None else b""
        return subprocess.CompletedProcess(list(args), self._rc, payload, b"")


def _inspect_doc(repo_digests, image_id="sha256:" + "1" * 64):
    return {"Architecture": "amd64", "Id": image_id, "Os": "linux",
            "RepoDigests": list(repo_digests)}


def _fresh_runner(monkeypatch, repo=None):
    """Re-import the runner so the module-level pin resolves under a given env."""
    if repo is None:
        monkeypatch.delenv("VIBEIC_EDA_IMAGE_REPO", raising=False)
    else:
        monkeypatch.setenv("VIBEIC_EDA_IMAGE_REPO", repo)
    spec = importlib.util.spec_from_file_location(
        "_repinned_hermetic_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _unresolvable_runner(monkeypatch):
    """A fresh runner whose image identity cannot be resolved -- the no-docker
    host or the inside of the image -- raised the way `_eda_pin` raises it."""
    module = _fresh_runner(monkeypatch)
    sys.path.insert(0, str(RUNNER_PATH.parents[2] / "vibe-ic-marketplace"
                           / "plugins" / "vibe-ic" / "programs"))
    import _eda_pin                                             # noqa: PLC0415

    def _refuse():
        raise _eda_pin.ImageNotResolvable(["this test: no docker"])
    monkeypatch.setattr(module, "_resolve_digest", _refuse)
    return module


def test_an_absent_engine_is_named_before_an_unresolvable_image(monkeypatch):
    """LAZY RESOLUTION MUST NOT CHANGE WHAT THE RUN REPORTS (lane rfimg2). The
    image used to resolve at import; now it resolves on first use, inside the
    run. With no engine the run must still say "cannot execute Docker CLI" --
    the runner's own words, which the verifier classifies as NOT_MEASURED --
    and never escape as an `ImageNotResolvable` traceback."""
    module = _unresolvable_runner(monkeypatch)
    with pytest.raises(module.Refusal, match="cannot execute Docker CLI"):
        module._image_profile(module.Docker("/nonexistent/docker-cli"))


def test_an_unresolvable_image_on_a_running_engine_is_refused_by_name(
        monkeypatch):
    module = _unresolvable_runner(monkeypatch)
    with pytest.raises(module.Refusal,
                       match="runtime image identity not resolvable"):
        module._image_profile(_StubDocker(None))


def test_image_profile_accepts_the_configured_repo_at_the_pinned_digest():
    profile = runner._image_profile(_StubDocker(_inspect_doc([runner.IMAGE])))
    assert profile["repo_digest"] == runner.IMAGE
    assert profile["reference"] == runner.IMAGE


def test_image_profile_accepts_the_right_digest_under_any_repository():
    """Right bytes, repository nobody configured. THE DIGEST IS THE IDENTITY.

    THIS TEST ASSERTED THE OPPOSITE UNTIL vibe-ic#2170, on the reasoning that
    "an unconfigured registry is an unreviewed distribution path". That
    reasoning does not survive what was measured: the SAME image, published
    once and mirrored to a fleet registry, carries BOTH names in its
    RepoDigests on a host that has seen both and only ONE on a host that pulled
    from the mirror. 8HD-8 holds it under both; 8HD-9 under the mirror alone.
    So the old rule made the verdict depend on which of two equivalent names a
    host happened to pull under -- 23 engine-driving cases in
    `test_landing_merge_verdict` recorded `[NORECORD] ... No such image` about
    an image that was on the machine.

    An unreviewed distribution path is a real hazard, and it is not this check
    that guards it: a mirror serving DIFFERENT bytes cannot produce this
    digest, and that is what the two tests below still refuse. What was given
    up here is a claim about the NAME, which review never rested on.

    Owner ruling, 2026-09-07 (#2170): the digest is the identity, the
    repository is configuration and is never part of the identity comparison.
    """
    elsewhere = f"registry.example.invalid/vibeic-eda@{PINNED_DIGEST}"
    assert elsewhere != runner.IMAGE
    profile = runner._image_profile(_StubDocker(_inspect_doc([elsewhere])))
    assert runner.carries_pinned_digest(profile["repo_digest"])


def test_image_profile_refuses_the_right_repository_at_the_wrong_digest():
    """Configured repository, wrong bytes. This is the one that matters most:
    it is what a re-tagged or re-pushed image looks like."""
    wrong = f"{runner.image_repo()}@{OTHER_DIGEST}"
    assert wrong != runner.IMAGE
    with pytest.raises(runner.Refusal, match="does not bind the requested digest"):
        runner._image_profile(_StubDocker(_inspect_doc([wrong])))


def test_image_profile_refuses_an_image_that_carries_no_repo_digest_at_all():
    """A `docker load`ed image has RepoDigests == []. Measured 2026-09-07 on
    three of five fleet hosts. It must not be accepted just because its Id is
    right -- an Id is not a name anyone can re-obtain the bytes by."""
    with pytest.raises(runner.Refusal, match="does not bind the requested digest"):
        runner._image_profile(_StubDocker(_inspect_doc([])))


def test_image_profile_still_refuses_a_foreign_platform():
    doc = _inspect_doc([runner.IMAGE])
    doc["Architecture"] = "arm64"
    with pytest.raises(runner.Refusal, match="platform"):
        runner._image_profile(_StubDocker(doc))


def test_image_profile_still_refuses_an_image_with_no_content_id():
    doc = _inspect_doc([runner.IMAGE], image_id="not-a-digest")
    with pytest.raises(runner.Refusal, match="exact content ID"):
        runner._image_profile(_StubDocker(doc))


def test_the_configured_repository_names_where_but_never_which(monkeypatch):
    """The one config point does exactly one thing: it changes WHERE the bytes
    are fetched from. It cannot change WHICH bytes, and it is not asked to
    decide whether a set of bytes IS the pinned runtime -- the digest is
    (#2170).

    The second limb of this test used to require that the PUBLISHED repository
    be REFUSED once a mirror was configured. That is the same image, and
    refusing it made the answer depend on the name rather than on the bytes;
    it is asserted the other way now, and the digest limb -- the one that
    actually protects the runtime -- is unchanged above it.
    """
    lan = _fresh_runner(monkeypatch, repo="registry.example.invalid:5000/vibeic-eda")
    assert lan.IMAGE_DIGEST == PINNED_DIGEST, "the env must not move the digest"
    assert lan.IMAGE == f"registry.example.invalid:5000/vibeic-eda@{PINNED_DIGEST}"

    # accepted under the configured repository ...
    assert lan._image_profile(_StubDocker(_inspect_doc([lan.IMAGE])))
    # ... and equally under the published one: same digest, same runtime.
    published = f"{lan.IMAGE_REPO_DEFAULT}@{PINNED_DIGEST}"
    assert lan._image_profile(_StubDocker(_inspect_doc([published])))
    # ... while the configured repository at ANOTHER digest is still refused,
    # which is the assertion this file exists for.
    with pytest.raises(lan.Refusal, match="does not bind the requested digest"):
        lan._image_profile(_StubDocker(_inspect_doc([f"{lan.IMAGE.split('@')[0]}@{OTHER_DIGEST}"])))


def test_with_no_env_the_pin_is_the_published_repository(monkeypatch):
    default = _fresh_runner(monkeypatch, repo=None)
    assert default.IMAGE == f"{default.IMAGE_REPO_DEFAULT}@{PINNED_DIGEST}"
    assert default.IMAGE_REPO_DEFAULT == "ghcr.io/vibeic/vibeic-eda"


def test_an_empty_repo_env_is_not_a_repository(monkeypatch):
    """Empty must fall back to the published default, not compose `@sha256:...`
    onto nothing."""
    blank = _fresh_runner(monkeypatch, repo="")
    assert blank.IMAGE == f"{blank.IMAGE_REPO_DEFAULT}@{PINNED_DIGEST}"


def test_the_receipt_discloses_the_configured_reference_and_the_verifier_requires_it(
        case):
    """THE DISCLOSURE, DRIVEN END TO END (#2170).

    The digest is the identity, so the same bytes under another repository are
    accepted -- and the price of that is that `VIBEIC_EDA_IMAGE_REPO` could
    become decorative: an operator sets a mirror, the mirror is not on this
    host, the published copy is used instead and nobody is ever told. The
    receipt therefore records BOTH names, and this asserts the field on a
    receipt the runner actually produced.

    Both directions, because a field nobody enforces is a field a later
    producer can silently stop emitting: dropping it is refused, and so is a
    `configured_reference` carrying a digest that is not the pin.
    """
    import copy

    proc = invoke(case)
    assert proc.returncode == 0, proc.stderr
    receipt = runner.strict_load_receipt(case["receipt"])
    assert receipt["image"]["configured_reference"] == runner.IMAGE
    assert receipt["image"]["reference"] == runner.IMAGE, (
        "nothing was substituted on this arm, so the two names must agree; if "
        "they do not, this fixture is no longer the control it claims to be")

    doc = json.loads(Path(case["receipt"]).read_text(encoding="utf-8"))
    assert runner.validate_receipt(copy.deepcopy(doc)), (
        "the unmutated receipt must validate, or the two refusals below prove "
        "nothing about the mutation")

    dropped = copy.deepcopy(doc)
    del dropped["image"]["configured_reference"]
    with pytest.raises(ValueError):
        runner.validate_receipt(dropped)

    foreign = copy.deepcopy(doc)
    foreign["image"]["configured_reference"] = f"other.example/eda@{OTHER_DIGEST}"
    with pytest.raises(ValueError):
        runner.validate_receipt(foreign)
