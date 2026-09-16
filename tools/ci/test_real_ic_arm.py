"""R-0915-86 (1) — the real-IC landing arm aggregates honestly.

`real_ic_arm.sh` is the landing side of the real-IC gate: it runs the two SHIPPED
programs (`real_ic_gate`, `audit_replay`) over the candidate tree and the frozen
snapshots, and turns their exit codes into ONE arm verdict. It computes no
verdict of its own, which is the point — but the aggregation itself is a place a
gate can go quiet, so it is tested in both directions rather than trusted.

The three properties that matter, and why each is a real escape:

  * A REGRESSED subject makes the arm rc 1. Obvious, and pinned so a later edit
    to the aggregation cannot drop it.
  * A REFUSED subject makes the arm rc 2 — NOT 0. "Nothing is claimed about
    this subject" read as "this subject is fine" is the zero-denominator green;
    the landing chain must see a refusal as a state it has to resolve.
  * NO subjects at all is rc 2, not rc 0. An arm that measured nothing and
    reported success is how a gate stops working without anyone noticing — this
    repo has the rule (`gate_zero_denominator_refuses_check`, #564) and the arm
    obeys it.

The subjects here are STUB programs in a fake candidate tree. That is
deliberate: this file measures the ARM, and a test that needs a 31 GB EDA image
and an IC is not a test. The real thing — SPM through the front door, r26 and
run16 replayed — is a MEASUREMENT recorded by the lane that built this.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ARM = Path(__file__).resolve().parent / "real_ic_arm.sh"

_STUB = '''\
import json, sys
from pathlib import Path
spec = json.loads(Path(__file__).with_name("_stub_%s.json").read_text())
out = None
argv = sys.argv[1:]
for i, a in enumerate(argv):
    if a == "--json":
        out = argv[i + 1]
if out:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(spec["report"]))
raise SystemExit(spec["rc"])
'''


def _tmp() -> Path:
    """mkdtemp, not pytest's tmp_path — the EDA image's tmp_path carries a
    newline in this repo's containers and it has broken invocations before."""
    return Path(tempfile.mkdtemp(prefix="realicarm_"))


def _report(verdict, regressions=0, refusal=None):
    return {"verdict": verdict, "refusal": refusal,
            "diff": {"comparable": refusal is None,
                     "regressions": [{"id": str(i)} for i in range(regressions)],
                     "improvements": [], "laterals": []}}


def _tree(root: Path, gate_spec, replay_spec) -> Path:
    progs = root / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
    progs.mkdir(parents=True, exist_ok=True)
    (progs / "real_ic_gate.py").write_text(_STUB % "gate", encoding="utf-8")
    (progs / "audit_replay.py").write_text(_STUB % "replay", encoding="utf-8")
    (progs / "_stub_gate.json").write_text(json.dumps(gate_spec), encoding="utf-8")
    (progs / "_stub_replay.json").write_text(json.dumps(replay_spec),
                                             encoding="utf-8")
    return root


def _frozen(root: Path, names) -> Path:
    for n in names:
        (root / n / "reports").mkdir(parents=True, exist_ok=True)
    return root


def _run(tree, corpus, outdir, frozen, env_extra=None):
    env = dict(os.environ)
    env.update({
        "REAL_IC_NAME": "anic", "REAL_IC_PDK": "anpdk",
        "FROZEN_ROOT": str(frozen),
        # NAMED by the caller, so the arm never creates or touches a container.
        "EDA_CONTAINER": "real-ic-arm-test-container-that-does-not-exist",
    })
    env.update(env_extra or {})
    return subprocess.run(["bash", str(_ARM), str(tree), str(corpus),
                           str(outdir)],
                          capture_output=True, text=True, env=env)


@pytest.fixture()
def bed():
    base = _tmp()
    (base / "corpus").mkdir()
    yield base
    shutil.rmtree(base, ignore_errors=True)


def test_every_subject_clean_exits_0(bed):
    tree = _tree(bed / "tree", {"rc": 0, "report": _report("NO_REGRESSION")},
                 {"rc": 0, "report": _report("NO_REGRESSION")})
    frozen = _frozen(bed / "frozen", ["snapA", "snapB"])
    out = bed / "out"
    r = _run(tree, bed / "corpus", out, frozen)
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads((out / "real_ic_arm.json").read_text())
    assert rep["verdict"] == "NO_REGRESSION"
    assert rep["subject_count"] == 3          # the IC + two snapshots
    assert rep["regressed"] == [] and rep["refused"] == []


def test_a_regressed_subject_exits_1_and_is_named(bed):
    tree = _tree(bed / "tree",
                 {"rc": 1, "report": _report("REGRESSION", regressions=2)},
                 {"rc": 0, "report": _report("NO_REGRESSION")})
    frozen = _frozen(bed / "frozen", ["snapA"])
    out = bed / "out"
    r = _run(tree, bed / "corpus", out, frozen)
    assert r.returncode == 1, r.stdout + r.stderr
    rep = json.loads((out / "real_ic_arm.json").read_text())
    assert rep["verdict"] == "REGRESSION"
    assert rep["regressed"] == ["anic"]
    assert [s for s in rep["subjects"] if s["subject"] == "anic"][0]["regressions"] == 2


def test_a_REFUSED_subject_exits_2_and_never_0(bed):
    """'Nothing is claimed about this subject' is not 'this subject is fine'."""
    tree = _tree(bed / "tree", {"rc": 0, "report": _report("NO_REGRESSION")},
                 {"rc": 2, "report": _report("REFUSED",
                                             refusal="NOT_COMPARABLE: ...")})
    frozen = _frozen(bed / "frozen", ["snapA"])
    out = bed / "out"
    r = _run(tree, bed / "corpus", out, frozen)
    assert r.returncode == 2, r.stdout + r.stderr
    rep = json.loads((out / "real_ic_arm.json").read_text())
    assert rep["verdict"] == "REFUSED"
    assert rep["refused"] == ["snapA"]


def test_a_regression_outranks_a_refusal(bed):
    """Both present: the arm reports the one that names a defect."""
    tree = _tree(bed / "tree", {"rc": 1, "report": _report("REGRESSION", 1)},
                 {"rc": 2, "report": _report("REFUSED", refusal="x")})
    frozen = _frozen(bed / "frozen", ["snapA"])
    out = bed / "out"
    r = _run(tree, bed / "corpus", out, frozen)
    assert r.returncode == 1
    rep = json.loads((out / "real_ic_arm.json").read_text())
    assert rep["verdict"] == "REGRESSION" and rep["refused"] == ["snapA"]


def test_a_directory_that_is_not_a_snapshot_is_not_a_subject(bed):
    """The replay half walks $FROZEN_ROOT/*/ and takes only directories that
    hold a reports/ tree. A stray directory must not become a subject that
    refuses, or the arm cries wolf on its own scratch space."""
    tree = _tree(bed / "tree", {"rc": 0, "report": _report("NO_REGRESSION")},
                 {"rc": 0, "report": _report("NO_REGRESSION")})
    frozen = _frozen(bed / "frozen", ["snapA"])
    (frozen / "nightly" / "2026-09-16").mkdir(parents=True)
    out = bed / "out"
    r = _run(tree, bed / "corpus", out, frozen)
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads((out / "real_ic_arm.json").read_text())
    assert [s["subject"] for s in rep["subjects"]] == ["anic", "snapA"]


def test_a_subject_that_writes_no_report_is_REFUSED_not_ignored(bed):
    """A stub that exits 0 and writes nothing. Reading the exit code alone would
    call that a pass; the arm reads the REPORT and refuses when there is none."""
    tree = _tree(bed / "tree", {"rc": 0, "report": _report("NO_REGRESSION")},
                 {"rc": 0, "report": _report("NO_REGRESSION")})
    progs = tree / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
    (progs / "real_ic_gate.py").write_text("raise SystemExit(0)\n",
                                           encoding="utf-8")
    frozen = _frozen(bed / "frozen", ["snapA"])
    out = bed / "out"
    r = _run(tree, bed / "corpus", out, frozen)
    assert r.returncode == 2, r.stdout + r.stderr
    rep = json.loads((out / "real_ic_arm.json").read_text())
    assert rep["refused"] == ["anic"]


def test_REAL_IC_REFERENCE_is_forwarded_to_the_gate(bed):
    """R-0915-88: since a published cell is produced by an AGENT-DRIVEN lane run
    and this arm runs the front door with no agent, the arm must be able to name
    a headless baseline instead. A flag the arm silently dropped would leave the
    gate judging against the wrong reference forever, and the refusal it printed
    would look like the arm's own."""
    tree = _tree(bed / "tree", {"rc": 0, "report": _report("NO_REGRESSION")},
                 {"rc": 0, "report": _report("NO_REGRESSION")})
    progs = tree / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
    # A stub that RECORDS its own argv, so the assertion is about what the arm
    # actually passed, not about what the script text says.
    (progs / "real_ic_gate.py").write_text(
        "import json,sys,pathlib\n"
        "argv=sys.argv[1:]\n"
        "out=argv[argv.index('--json')+1]\n"
        "pathlib.Path(out).parent.mkdir(parents=True,exist_ok=True)\n"
        "pathlib.Path(out).write_text(json.dumps("
        "{'verdict':'NO_REGRESSION','argv':argv,'diff':{'comparable':True,"
        "'regressions':[],'improvements':[],'laterals':[]}}))\n",
        encoding="utf-8")
    frozen = _frozen(bed / "frozen", [])
    out = bed / "out"
    baseline = bed / "headless_baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    r = _run(tree, bed / "corpus", out, frozen,
             {"REAL_IC_REFERENCE": str(baseline)})
    assert r.returncode == 0, r.stdout + r.stderr
    gate = json.loads((out / "real_ic_gate_anic.json").read_text())
    assert "--reference" in gate["argv"]
    assert gate["argv"][gate["argv"].index("--reference") + 1] == str(baseline)


def test_a_tree_with_no_plugin_programs_REFUSES(bed):
    out = bed / "out"
    empty = bed / "empty"
    empty.mkdir()
    r = _run(empty, bed / "corpus", out, _frozen(bed / "frozen", []))
    assert r.returncode == 2
    assert "no plugin programs" in (r.stdout + r.stderr)


def test_no_timeout_or_kill_appears_in_either_landing_script():
    """The owner's standing rule, pinned in the files rather than promised."""
    for name in ("real_ic_arm.sh", "nightly_real_ic.sh"):
        src = (_ARM.parent / name).read_text(encoding="utf-8")
        body = "\n".join(l for l in src.splitlines() if not l.startswith("#"))
        for banned in ("timeout ", "kill ", "pkill", "SIGKILL", "--deadline"):
            assert banned not in body, "%r must not appear in %s" % (banned, name)


def test_the_nightly_states_its_cron_line():
    """The dispatcher installs the cron line; a nightly whose schedule lives
    only in somebody's head is a nightly that stops running silently."""
    src = (_ARM.parent / "nightly_real_ic.sh").read_text(encoding="utf-8")
    assert "CRON_TZ=Asia/Taipei" in src
    assert "nightly_real_ic.sh" in src.split("THE CRON LINE", 1)[1][:600]
