"""The native antenna deck is the one INSIDE the image the run's PDK root is bound to.

MEASURED on the spm IC-die run (vibeic-eda 0.3.85, image id sha256:7d372e6d...):
Step 26's GDS antenna gate published DISCLOSED_SKIP "container PDK deck bytes
differ from the image-bound bridge tree" although the container's
`gf180mcu.drc` / `rule_decks/antenna.rb` and the host cache copy are
byte-identical (992ed4d4... / f82e2aed... on both sides). The probe ran
`sha256sum` through the image's LOGIN shell, whose profile prints two
`[INFO] Final ... variable:` lines on stdout, and read the digests by
position -- so it compared the word "[INFO]" with a SHA-256 and never
measured the design.

The repair is not a better positional parse. The tree the run used is named by
the receipt as (image id, path inside that image); the host cache copy is an
extraction nothing re-verifies. So the deck is resolved, hashed and executed
inside a container running exactly that image, the host copy is never read,
and the count is accepted only when the transcript names the hashed rule, the
RDB names the hashed parent, and the deck's own tally equals the RDB count.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import gds_antenna_deck_check as gate  # noqa: E402
import _klayout_launch as launch  # noqa: E402
import _watchdog as watchdog  # noqa: E402

IMAGE_ID = "sha256:" + "7d" * 32
OTHER_IMAGE_ID = "sha256:" + "2c" * 32
PDK = "fixture_pdk"
GUEST = f"/foss/pdks/{PDK}"
DRC_REL = "libs.tech/klayout/tech/drc"
PARENT = "main.drc"
PARENT_SRC = b"decks: $decks\nload rule_decks\n"
RULE_SRC = b"# the image's own antenna rule\n"
# What vibeic-eda's login profile prints on stdout before every `bash -lc`.
LOGIN_BANNER = ("[INFO] Final PATH variable: /headless/.local/bin:/foss/tools/bin\n"
                "[INFO] Final PYTHONPATH variable: /headless/.local/lib/python3.12\n")


def _rdb(violations: int, script: str) -> str:
    items = "".join("<item><category>'ANT.1'</category></item>"
                    for _ in range(violations))
    return ("<?xml version='1.0'?><report-database><description/>"
            f"<generator>drc: script='{script}'</generator>"
            "<categories><category><name>ANT.1</name><description/></category>"
            "</categories><items>" + items + "</items></report-database>")


def _transcript(rule: str, violations: int) -> str:
    tally = ("DRC RESULT: SUCCESS (0 violations)" if violations == 0
             else f"DRC RESULT: FAILURE ({violations} violation(s))")
    return (f"x: Executing deck antenna_poly2 from {rule}\n"
            "x: Executing rule ANT.1\n"
            f"x: Executing deck antenna_metal1 from {rule}\n"
            f"x: {tally}\n")


def _write_deck(drc: Path, *, rule: bytes | None = RULE_SRC) -> None:
    (drc / "rule_decks").mkdir(parents=True)
    (drc / PARENT).write_bytes(PARENT_SRC)
    (drc / "helper.rb").write_bytes(b"# not a parent\n")
    if rule is not None:
        (drc / "rule_decks" / "antenna.rb").write_bytes(rule)


class ImageContainer:
    """A container running one image: its filesystem is `fs`.

    `run_argv` behaves like `bash -lc` in vibeic-eda, banner included, which
    is the path the old digest probe took. `read_bytes`/`list_files` are the
    direct reads the container runner offers.
    """
    kind = "container"

    def __init__(self, fs: Path, image_id: str = IMAGE_ID, violations: int = 0,
                 banner: bool = True):
        self.fs = fs
        self.detail = "fixture-eda:klayout"
        self._image_id = image_id
        self.violations = violations
        self.banner = banner
        self.argv = None
        self.transcript = None      # override: (rule, script) -> stdout
        self.rdb = None             # override: (script) -> RDB text
        self.during_run = None      # hook executed while "KLayout" runs

    def _in_image(self, path) -> Path:
        return self.fs / str(path).lstrip("/")

    def covers(self, path):
        return True

    def cpath(self, path):
        return str(path)

    def klayout_bin(self):
        return "klayout"

    def image_id(self):
        return self._image_id, ""

    def read_bytes(self, path):
        try:
            return self._in_image(path).read_bytes()
        except OSError:
            return None

    def list_files(self, directory, suffix):
        d = self._in_image(directory)
        if not d.is_dir():
            return None
        return sorted("/" + str(p.relative_to(self.fs)) for p in d.iterdir()
                      if p.name.endswith(suffix) and p.is_file())

    def run_argv(self, argv, env, *, timeout):
        out = LOGIN_BANNER if self.banner else ""
        if argv[0] != "sha256sum":
            raise AssertionError(f"unexpected command {argv}")
        for arg in argv[1:]:
            f = self._in_image(arg)
            if not f.is_file():
                return 1, out, f"sha256sum: {arg}: No such file or directory"
            out += f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {arg}\n"
        return 0, out, ""

    def run_argv_supervised(self, argv, env, *, stall_grace_s,
                            memory_limit_mb, progress_paths):
        self.argv = argv
        script = argv[argv.index("-r") + 1]
        assert self._in_image(script).is_file(), f"{script} is not in the image"
        rule = posixpath.join(posixpath.dirname(script), "rule_decks", "antenna.rb")
        report = Path(next(a.split("=", 1)[1] for a in argv
                           if a.startswith("report=")))
        report.write_text(self.rdb(script) if self.rdb
                          else _rdb(self.violations, script))
        if self.during_run:
            self.during_run()
        out = (LOGIN_BANNER if self.banner else "") + (
            self.transcript(rule, script) if self.transcript
            else _transcript(rule, self.violations))
        return watchdog.SupervisedResult(0, out, "", "natural", 0.1,
                                         supervision={"fixture": True})


class HostKLayout:
    kind = "host"
    detail = "klayout -b -r"

    def __init__(self):
        self.argv = None

    def covers(self, path):
        return True

    def cpath(self, path):
        return str(path)

    def klayout_bin(self):
        return "klayout"

    def run_argv_supervised(self, argv, env, **kw):
        self.argv = argv
        report = Path(next(a.split("=", 1)[1] for a in argv
                           if a.startswith("report=")))
        script = argv[argv.index("-r") + 1]
        rule = posixpath.join(posixpath.dirname(script), "rule_decks", "antenna.rb")
        report.write_text(_rdb(0, script))
        return watchdog.SupervisedResult(0, _transcript(rule, 0), "", "natural",
                                         0.1, supervision={})


def _project(tmp_path, *, host_copy: bytes | None = RULE_SRC,
             image_rule: bytes | None = RULE_SRC):
    """A resolved-PDK run: host cache copy + the tree inside the image."""
    project = tmp_path / "project"
    gds_dir = project / "phase3/stage4/gds"
    gds_dir.mkdir(parents=True)
    (gds_dir / "layout.gds").write_bytes(b"routed stream geometry")
    fs = tmp_path / "image_fs"
    _write_deck(fs / GUEST.lstrip("/") / DRC_REL, rule=image_rule)
    cache = tmp_path / "host_cache" / IMAGE_ID.split(":", 1)[1]
    if host_copy is not None:
        _write_deck(cache / PDK / DRC_REL, rule=host_copy)
    receipt = project / gate._PDK_ROOT_RECEIPT
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({
        "path": str(cache), "source": "resolved",
        "derivation": {"image": "ghcr.io/example/eda@sha256:" + "ab" * 32,
                       "image_id": IMAGE_ID, "pdk": PDK, "guest_path": GUEST,
                       "host_path": str(cache / PDK), "cache": "reused"}}))
    return project, fs


def _use(monkeypatch, runner, *, host=None):
    """Route both runner resolvers; `raising=False` keeps the base comparable."""
    monkeypatch.setattr(gate._kl, "find_runner",
                        lambda **kw: host if host is not None else runner)
    monkeypatch.setattr(gate._kl, "find_container_runner",
                        lambda **kw: runner, raising=False)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("violations, verdict", [(0, "PASS"), (1, "FAIL")])
def test_login_banner_no_longer_reads_as_a_deck_digest_mismatch(
        tmp_path, monkeypatch, violations, verdict):
    """The measured defect: identical decks, banner on stdout -> never measured."""
    project, fs = _project(tmp_path)
    runner = ImageContainer(fs, violations=violations)
    _use(monkeypatch, runner)
    result = gate.run(project, None, None, None, None)
    assert result["verdict"] == verdict, result
    assert result["violations"] == violations
    parent = f"{GUEST}/{DRC_REL}/{PARENT}"
    assert runner.argv[runner.argv.index("-r") + 1] == parent
    assert result["pdk_parent"] == parent
    assert result["pdk_rule"] == f"{GUEST}/{DRC_REL}/rule_decks/antenna.rb"
    assert result["pdk_rule_sha256"] == _sha(RULE_SRC)
    assert result["pdk_parent_sha256"] == _sha(PARENT_SRC)
    assert result["pdk_image_id"] == IMAGE_ID
    assert result["pdk_tree_side"] == "container:fixture-eda:klayout"
    assert result["transcript_tally"] == violations


def test_deck_is_measured_from_the_image_when_the_host_copy_is_absent(
        tmp_path, monkeypatch):
    project, fs = _project(tmp_path, host_copy=None)
    runner = ImageContainer(fs)
    _use(monkeypatch, runner)
    result = gate.run(project, None, None, None, None)
    assert result["verdict"] == "PASS", result
    assert result["pdk_tree"] == GUEST


def test_a_divergent_host_copy_is_never_the_recorded_deck(tmp_path, monkeypatch):
    project, fs = _project(tmp_path, host_copy=b"# an edited host copy\n")
    runner = ImageContainer(fs, banner=False)
    _use(monkeypatch, runner)
    result = gate.run(project, None, None, None, None)
    assert result["verdict"] == "PASS", result
    assert result["pdk_rule_sha256"] == _sha(RULE_SRC)
    assert str(tmp_path / "host_cache") not in json.dumps(
        {k: v for k, v in result.items() if k.startswith("pdk_")})


def test_container_running_another_image_is_not_measured(tmp_path, monkeypatch):
    project, fs = _project(tmp_path)
    runner = ImageContainer(fs, image_id=OTHER_IMAGE_ID, banner=False)
    _use(monkeypatch, runner)
    result = gate.run(project, None, None, None, None)
    assert result["verdict"] == "DISCLOSED_SKIP", result
    assert result["measurement"] == "NOT_MEASURED"
    assert IMAGE_ID in result["reason"] and OTHER_IMAGE_ID in result["reason"]
    assert runner.argv is None
    assert gate.main([str(project)]) == gate.SKIP


def test_host_klayout_never_substitutes_for_the_image_tree(tmp_path, monkeypatch):
    project, _ = _project(tmp_path)
    host = HostKLayout()
    _use(monkeypatch, None, host=host)
    result = gate.run(project, None, None, None, None)
    assert result["verdict"] == "DISCLOSED_SKIP", result
    assert result["measurement"] == "NOT_MEASURED"
    assert "inside the image" in result["reason"]
    assert host.argv is None


def test_deck_missing_inside_the_image_stays_not_measured(tmp_path, monkeypatch):
    project, fs = _project(tmp_path, image_rule=None)
    runner = ImageContainer(fs)
    _use(monkeypatch, runner)
    result = gate.run(project, None, None, None, None)
    assert result["verdict"] == "DISCLOSED_SKIP", result
    assert result["measurement"] == "NOT_MEASURED"
    assert "no native antenna rule deck" in result["reason"]
    assert GUEST in result["reason"]
    assert runner.argv is None


@pytest.mark.parametrize("tamper, needle", [
    # The RDB holds a violation the deck's own completion tally does not.
    ({"violations": 1, "transcript": lambda rule, script:
      _transcript(rule, 0)}, "own tally"),
    # The run never reached its completion tally.
    ({"transcript": lambda rule, script: _transcript(rule, 0).rsplit("x: DRC", 1)[0]},
     "completion tallies"),
    # The decks came from some other rule file than the one hashed.
    ({"transcript": lambda rule, script:
      _transcript("/elsewhere/rule_decks/antenna.rb", 0)}, "not the hashed rule"),
    # The RDB was written by another script.
    ({"rdb": lambda script: _rdb(0, "/elsewhere/other.drc")}, "not the hashed parent"),
])
def test_rdb_and_transcript_must_agree_before_a_count_is_read(
        tmp_path, monkeypatch, tamper, needle):
    project, fs = _project(tmp_path)
    runner = ImageContainer(fs, banner=False,
                            violations=tamper.get("violations", 0))
    runner.transcript = tamper.get("transcript")
    runner.rdb = tamper.get("rdb")
    _use(monkeypatch, runner)
    result = gate.run(project, None, None, None, None)
    assert result["verdict"] == "DISCLOSED_SKIP", result
    assert result["measurement"] == "NOT_MEASURED"
    assert needle in result["reason"], result
    assert "violations" not in result


def test_deck_changed_during_execution_is_not_measured(tmp_path, monkeypatch):
    project, fs = _project(tmp_path)
    runner = ImageContainer(fs, banner=False)
    rule = fs / GUEST.lstrip("/") / DRC_REL / "rule_decks" / "antenna.rb"
    runner.during_run = lambda: rule.write_bytes(b"# swapped mid-run\n")
    _use(monkeypatch, runner)
    result = gate.run(project, None, None, None, None)
    assert result["verdict"] == "DISCLOSED_SKIP", result
    assert "changed or vanished during execution" in result["reason"]


@pytest.mark.parametrize("derivation, needle", [
    ({"image_id": IMAGE_ID, "guest_path": "/foss/pdks/another_pdk"}, "is not the absolute tree"),
    ({"image_id": "7d372e6d", "guest_path": GUEST}, "names no image bytes"),
    ({"guest_path": GUEST}, "names no image bytes"),
])
def test_receipt_that_cannot_bind_a_tree_to_image_bytes_is_unusable(
        tmp_path, monkeypatch, derivation, needle):
    project, fs = _project(tmp_path)
    receipt = project / gate._PDK_ROOT_RECEIPT
    doc = json.loads(receipt.read_text())
    doc["derivation"] = {"pdk": PDK, **derivation}
    receipt.write_text(json.dumps(doc))
    runner = ImageContainer(fs, banner=False)
    _use(monkeypatch, runner)
    result = gate.run(project, None, None, None, None)
    assert result["verdict"] == "DISCLOSED_SKIP", result
    assert needle in result["reason"]
    assert runner.argv is None


def test_container_reader_runs_the_command_directly_not_in_a_login_shell(
        monkeypatch):
    seen = []
    monkeypatch.setattr(launch._ce, "docker_exec_argv",
                        lambda c, *rest, opts=(): ["docker", "exec", c, *rest])

    def fake_run(argv, **kw):
        seen.append(argv)
        body = b"deck bytes\n" if argv[3] == "cat" else b"/t/a.drc\0/t/b.drc\0"
        return subprocess.CompletedProcess(argv, 0, body, b"")

    monkeypatch.setattr(launch.subprocess, "run", fake_run)
    runner = launch.ContainerRunner("fixture-eda")
    assert runner.read_bytes("/t/rule_decks/antenna.rb") == b"deck bytes\n"
    assert runner.list_files("/t", ".drc") == ["/t/a.drc", "/t/b.drc"]
    assert seen[0] == ["docker", "exec", "fixture-eda", "cat", "--",
                       "/t/rule_decks/antenna.rb"]
    assert all("bash" not in argv and "-lc" not in argv for argv in seen)


def test_container_resolver_never_returns_a_host_klayout(monkeypatch):
    monkeypatch.delenv("VIBEIC_KLAYOUT_FORCE_ABSENT", raising=False)
    monkeypatch.setattr(launch.shutil, "which",
                        lambda name: "/usr/bin/klayout" if name == "klayout" else None)
    assert isinstance(launch.find_runner(), launch.HostRunner)
    assert launch.find_container_runner() is None


def test_transcript_and_rdb_grammar_is_the_real_tools_own():
    """The binding grammar, read from REAL KLayout output (calibration pair)."""
    cal = PROGRAMS / "calibration"
    rule = "/pdk/gf180mcuD/libs.tech/klayout/tech/drc/rule_decks/antenna.rb"
    parent = "/pdk/gf180mcuD/libs.tech/klayout/tech/drc/gf180mcu.drc"
    for stem, status, count in (("violation", "FAILURE", 1), ("clean", "SUCCESS", 0)):
        log = (cal / f"native_antenna_{stem}.log").read_text()
        rdb = (cal / f"native_antenna_{stem}.lyrdb").read_text()
        assert set(gate._DECK_FROM_RE.findall(log)) == {rule}
        assert gate._TALLY_RE.findall(log) == [(status, str(count))]
        assert gate._GENERATOR_RE.search(rdb).group(1) == parent
