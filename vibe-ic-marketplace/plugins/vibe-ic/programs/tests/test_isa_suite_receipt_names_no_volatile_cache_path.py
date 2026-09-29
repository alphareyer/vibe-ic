"""The ISA-suite receipt names WHAT was compiled, never where a cache kept it.

MEASURED on a DIE-route front-door run (vibeic-eda 0.3.85, 2026-09-29): the
Phase-2 strict audit FAILed `project_outputs_in_tree_check` with "2 live
external-storage artifact(s)". Both were the receipt's acquisition lines,
`<suite>@<commit>: cache /tmp/vibeic_isa_suites/<commit>.tar.gz; ...`. The
cache is a host-wide scratch copy that `acquire` re-verifies against the
lock's tarball sha256 before use; the evidence of what was compiled is that
digest and the pinned URL, not the cache file. Naming the cache path made a
correct run depend on a volatile file outside the project, and the gate is
right to refuse that.

THE RULE NOW: an acquisition line states the pinned URL and the verified
tarball sha256, on a cache hit and on a fetch alike, and never a filesystem
path outside the project. The gate itself is unchanged; the paired control
shows it still refuses a receipt that does name a live external path.

Real code path: `acquire` with a counting fake fetch (the network boundary
only), then the real `project_outputs_in_tree_check` over the receipt.
chip-AGNOSTIC: synthetic suite, no chip, vendor or PDK literal.
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import isa_suite_producer as I  # noqa: E402

ROOT = "suite-0123"
FILES = {"src/add-01.S": b"/* a program */\n", "env/h.h": b"/* a header */\n"}


def _tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, blob in FILES.items():
            ti = tarfile.TarInfo(f"{ROOT}/{rel}")
            ti.size = len(blob)
            tf.addfile(ti, io.BytesIO(blob))
    return buf.getvalue()


TARBALL = _tarball()
URL = "https://example.invalid/suite.tar.gz"
SUITE = {"commit": "e" * 40, "root": ROOT, "tarball_url": URL,
         "tarball_sha256": hashlib.sha256(TARBALL).hexdigest(),
         "include_dirs": ["env"],
         "files": {k: hashlib.sha256(v).hexdigest() for k, v in FILES.items()}}


def _acquire_twice(tmp_path, monkeypatch):
    cache = tmp_path / "host_cache"
    monkeypatch.setenv("VIBEIC_ISA_SUITE_CACHE", str(cache))
    calls = []

    def fetch(url):
        calls.append(url)
        return TARBALL

    first = I.acquire("s", SUITE, tmp_path / "w1", fetch=fetch)
    second = I.acquire("s", SUITE, tmp_path / "w2", fetch=fetch)
    return cache, calls, first, second


def test_a_cache_hit_names_the_url_and_digest_not_the_cache_file(tmp_path, monkeypatch):
    cache, calls, first, second = _acquire_twice(tmp_path, monkeypatch)
    assert calls == [URL], "the second acquisition must be a cache hit"
    assert (cache / f"{SUITE['commit']}.tar.gz").is_file()
    for root, why in (first, second):
        assert root is not None, why
        assert URL in why and SUITE["tarball_sha256"] in why, why
        assert str(cache) not in why, why


def test_the_receipt_passes_the_in_tree_gate(tmp_path, monkeypatch):
    _, _, first, second = _acquire_twice(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    rec = project / "reports" / "phase2" / "isa_suites"
    rec.mkdir(parents=True)
    (rec / "isa_suite_receipt.json").write_text(
        json.dumps({"acquisition": [first[1], second[1]]}))
    cp = subprocess.run([sys.executable,
                         str(PROGRAMS / "project_outputs_in_tree_check.py"),
                         str(project)], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr


def test_control_the_gate_still_refuses_a_live_external_path(tmp_path):
    """Paired control: the same gate over a receipt naming a live file outside
    the project still fails, so the green above is the producer's change."""
    outside = tmp_path / "host_cache" / "x.tar.gz"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(TARBALL)
    project = tmp_path / "proj"
    rec = project / "reports" / "phase2" / "isa_suites"
    rec.mkdir(parents=True)
    (rec / "isa_suite_receipt.json").write_text(
        json.dumps({"acquisition": [f"s@{SUITE['commit']}: cache {outside}"]}))
    cp = subprocess.run([sys.executable,
                         str(PROGRAMS / "project_outputs_in_tree_check.py"),
                         str(project)], capture_output=True, text=True)
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert str(outside) in cp.stdout
