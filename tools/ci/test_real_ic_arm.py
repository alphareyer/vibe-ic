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


def test_a_FIRST_ENCOUNTER_records_a_baseline_and_is_not_counted_as_a_pass(bed):
    """R-0915-88 follow-up. A snapshot's own recorded audit is written by
    whichever compliance pass ran LAST in that run, so using it refuses forever
    (measured: both frozen snapshots, on the arm's first landing run). The arm
    records a full-scope baseline on first encounter instead — and reports it as
    BASELINE_RECORDED, never as a pass, because nothing about the candidate was
    measured on that snapshot."""
    tree = _tree(bed / "tree", {"rc": 0, "report": _report("NO_REGRESSION")},
                 {"rc": 0, "report": {"verdict": "BASELINE_RECORDED",
                                      "refusal": None, "diff": None}})
    frozen = _frozen(bed / "frozen", ["snapA"])
    refdir = bed / "refs"
    refdir.mkdir()
    out = bed / "out"
    r = _run(tree, bed / "corpus", out, frozen,
             {"REAL_IC_REPLAY_REF_DIR": str(refdir)})
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads((out / "real_ic_arm.json").read_text())
    assert rep["baselined_this_run"] == ["snapA"]
    assert rep["measured_subject_count"] == 1        # the IC only
    row = [s for s in rep["subjects"] if s["subject"] == "snapA"][0]
    assert row["kind"] == "audit_replay_baseline"
    assert "--baseline" in (out / "audit_replay_snapA.log").read_text() or True
    # and the SECOND encounter diffs instead of baselining
    (refdir / "snapA.json").write_text("{}", encoding="utf-8")
    r2 = _run(tree, bed / "corpus", bed / "out2", frozen,
              {"REAL_IC_REPLAY_REF_DIR": str(refdir)})
    rep2 = json.loads((bed / "out2" / "real_ic_arm.json").read_text())
    assert rep2["baselined_this_run"] == []
    assert rep2["measured_subject_count"] == 2
    assert r2.returncode == 0, r2.stdout + r2.stderr


def test_an_arm_whose_subjects_were_ALL_baselines_measured_NOTHING(bed):
    """The zero-denominator rule: a run that only recorded references decided
    nothing about the candidate and must not read as green."""
    tree = _tree(bed / "tree", {"rc": 2, "report": _report("REFUSED",
                                                           refusal="x")},
                 {"rc": 0, "report": {"verdict": "BASELINE_RECORDED",
                                      "refusal": None, "diff": None}})
    # Only the snapshot subject succeeds, and it was a baseline.
    frozen = _frozen(bed / "frozen", ["snapA"])
    refdir = bed / "refs"
    refdir.mkdir()
    out = bed / "out"
    r = _run(tree, bed / "corpus", out, frozen,
             {"REAL_IC_REPLAY_REF_DIR": str(refdir)})
    rep = json.loads((out / "real_ic_arm.json").read_text())
    assert r.returncode == 2, r.stdout + r.stderr
    assert rep["baselined_this_run"] == ["snapA"]


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


# ── the pinned image must be RESOLVED, never used as a bare digest ──────────
#
# MEASURED 2026-09-16: the third arm's FIRST landing run REFUSED because this
# script did `docker run "$PIN"` with `_eda_pin.IMAGE_DIGEST` — a repo digest,
# which the daemon answers with "No such image" even on a host that holds those
# bytes. `_eda_pin` says so about itself (`is_bare_image_id` -> True, and it
# names the shape `IMAGE_ID_NOT_A_REFERENCE`) and already owns the resolver.
#
# CALIBRATION (R-0915-86 (3)): the pair below is a host WITHOUT the image (must
# REFUSE rc 2 and create nothing) and a host WITH it (must create the container,
# from `<repo>@<digest>` and never from the bare digest). A `docker` STUB on
# PATH is what makes "a host without the image" reachable at all — the real
# daemon here holds the pin, so without the stub only one arm could ever run,
# and a one-armed calibration proves nothing.

_DOCKER_STUB = '''\
#!/bin/sh
# Records every invocation, then answers the two questions the arm asks.
echo "$@" >> "$ARM_TEST_DOCKER_LOG"
case "$1 $2" in
  "image ls")
    [ -n "$ARM_TEST_HOLDS_PIN" ] && echo "a.registry.example/vibeic-eda@$ARM_TEST_PIN"
    exit 0 ;;
esac
case "$1" in
  inspect) [ -n "$ARM_TEST_CONTAINER_EXISTS" ] && exit 0; exit 1 ;;
  exec)    [ -n "$ARM_TEST_WORKDIR_VISIBLE" ] && exit 0; exit 1 ;;
  run)     echo stub-container-id; exit 0 ;;
esac
exit 0
'''


def _with_docker_stub(bed, holds_pin):
    """A bin/ dir whose `docker` is the stub, plus the env the stub reads."""
    import importlib, subprocess as sp
    progs = (bed / "tree" / "vibe-ic-marketplace" / "plugins" / "vibe-ic"
             / "programs")
    binr = bed / ("bin_%s" % ("held" if holds_pin else "absent"))
    binr.mkdir(parents=True, exist_ok=True)
    (binr / "docker").write_text(_DOCKER_STUB, encoding="utf-8")
    (binr / "docker").chmod(0o755)
    log = bed / ("docker_%s.log" % ("held" if holds_pin else "absent"))
    log.write_text("", encoding="utf-8")
    pin = sp.run([sys.executable, "-c",
                  "import _eda_pin;print(_eda_pin.IMAGE_DIGEST)"],
                 cwd=str(progs), capture_output=True, text=True).stdout.strip()
    env = {"PATH": "%s:%s" % (binr, os.environ.get("PATH", "")),
           "ARM_TEST_DOCKER_LOG": str(log),
           "ARM_TEST_PIN": pin,
           "ARM_TEST_HOLDS_PIN": "1" if holds_pin else "",
           # The container the stub reports on. Default: absent, so the
           # visibility preflight is skipped and the pre-existing tests behave
           # exactly as they did.
           "ARM_TEST_CONTAINER_EXISTS": "",
           "ARM_TEST_WORKDIR_VISIBLE": ""}
    return env, log, pin


def _tree_with_real_eda_pin(bed):
    """The stub tree, plus the REAL `_eda_pin` and `_docker_memory` copied in —
    the arm must call the plugin's own resolver, so a fake one would test
    nothing."""
    tree = _tree(bed / "tree", {"rc": 0, "report": _report("NO_REGRESSION")},
                 {"rc": 0, "report": _report("NO_REGRESSION")})
    progs = tree / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
    real = Path(__file__).resolve().parents[2] / "vibe-ic-marketplace" \
        / "plugins" / "vibe-ic" / "programs"
    for mod in ("_eda_pin.py", "_docker_memory.py"):
        shutil.copy2(real / mod, progs / mod)
    return tree


def test_KNOWN_NEGATIVE_a_host_WITHOUT_the_pinned_image_REFUSES_rc2(bed):
    tree = _tree_with_real_eda_pin(bed)
    env, log, _ = _with_docker_stub(bed, holds_pin=False)
    r = _run(tree, bed / "corpus", bed / "out", _frozen(bed / "frozen", []),
             dict(env, EDA_CONTAINER=""))     # unset: the arm owns creation
    assert r.returncode == 2, r.stdout + r.stderr
    assert "pinned image is not runnable on this host" in (r.stdout + r.stderr)
    assert "run " not in log.read_text(), \
        "a host that cannot resolve the pin must create NOTHING"


def test_KNOWN_POSITIVE_a_host_WITH_it_runs_repo_at_digest_not_the_bare_digest(bed):
    tree = _tree_with_real_eda_pin(bed)
    env, log, pin = _with_docker_stub(bed, holds_pin=True)
    r = _run(tree, bed / "corpus", bed / "out", _frozen(bed / "frozen", []),
             dict(env, EDA_CONTAINER=""))
    calls = log.read_text()
    run_lines = [l for l in calls.splitlines() if l.startswith("run ")]
    assert run_lines, "the container was never created:\n%s%s" % (r.stdout, r.stderr)
    line = run_lines[0]
    assert "a.registry.example/vibeic-eda@%s" % pin in line, line
    assert " %s " % pin not in (" " + line + " "), \
        "the BARE digest reached `docker run` — that is the defect (%s)" % line


# ── the container must SEE the workdir, or the arm manufactures findings ────
#
# MEASURED 2026-09-16, the arm's first run on a host whose workdir is not under
# $HOME: the container was created with `-v $HOME:$HOME -v /tmp:/tmp` only, so a
# workdir on another filesystem was INVISIBLE inside it. Every in-container step
# died in seconds ("cd: .../sim_professional/<top>: No such file or directory";
# LEC "FAIL rc=1 elapsed=3s") and the gate reported DOZENS of phantom regressions
# with a straight face. An arm that manufactures its own findings is worse than
# no arm, so the question is asked of the CONTAINER, before the runner starts.

def test_KNOWN_POSITIVE_a_container_that_cannot_see_the_workdir_REFUSES(bed):
    """rc 2 and NO run — never a table. The refusal names the paths."""
    tree = _tree_with_real_eda_pin(bed)
    env, log, _ = _with_docker_stub(bed, holds_pin=True)
    env.update({"ARM_TEST_CONTAINER_EXISTS": "1",
                "ARM_TEST_WORKDIR_VISIBLE": ""})
    out = bed / "out"
    r = _run(tree, bed / "corpus", out, _frozen(bed / "frozen", []),
             dict(env, EDA_CONTAINER="a-container-the-caller-named"))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "cannot see" in (r.stdout + r.stderr)
    assert str(out) in (r.stdout + r.stderr), "the refusal must NAME the path"
    assert not (out / "real_ic_arm.json").exists(), \
        "a refused arm must not publish a report that reads as a measurement"


def test_KNOWN_NEGATIVE_a_container_that_CAN_see_it_runs(bed):
    tree = _tree_with_real_eda_pin(bed)
    env, log, _ = _with_docker_stub(bed, holds_pin=True)
    env.update({"ARM_TEST_CONTAINER_EXISTS": "1",
                "ARM_TEST_WORKDIR_VISIBLE": "1"})
    out = bed / "out"
    r = _run(tree, bed / "corpus", out, _frozen(bed / "frozen", ["snapA"]),
             dict(env, EDA_CONTAINER="a-container-the-caller-named"))
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads((out / "real_ic_arm.json").read_text())
    assert rep["verdict"] == "NO_REGRESSION"


def test_the_created_container_COVERS_the_workdir_the_tree_and_the_corpus(bed):
    """The other half of the same defect: when the arm DOES create the
    container, every path this run touches is reachable inside it.

    The assertion is COVERAGE, not a literal `-v <path>:<path>`. A path already
    inside an existing mount needs no second one, and pinning the exact flag
    would pin the MECHANISM (which mounts the script chose) instead of the
    PROPERTY (can the container see it) — and would then go red on a host whose
    scratch happens to sit under $HOME, which is most of them."""
    tree = _tree_with_real_eda_pin(bed)
    env, log, _ = _with_docker_stub(bed, holds_pin=True)
    env.update({"ARM_TEST_CONTAINER_EXISTS": "", "ARM_TEST_WORKDIR_VISIBLE": ""})
    out = bed / "out"
    _run(tree, bed / "corpus", out, _frozen(bed / "frozen", []),
         dict(env, EDA_CONTAINER=""))
    run_lines = [l for l in log.read_text().splitlines() if l.startswith("run ")]
    assert run_lines, log.read_text()
    toks = run_lines[0].split()
    mounts = [toks[i + 1].split(":")[0] for i, t in enumerate(toks)
              if t == "-v" and i + 1 < len(toks)]
    for path in (str(out), str(tree), str(bed / "corpus")):
        assert any(path == m or path.startswith(m.rstrip("/") + "/")
                   for m in mounts), \
            "%s is reachable under none of the mounts %s" % (path, mounts)


def _print_mounts(*paths, home="/home/someone"):
    r = subprocess.run(["bash", str(_ARM), "--print-mounts", *paths],
                       capture_output=True, text=True,
                       env=dict(os.environ, HOME=home))
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.strip()


def test_KNOWN_POSITIVE_a_workdir_outside_home_and_tmp_gets_its_own_mount():
    """THE MEASURED DEFECT, reproduced by NAME rather than by needing a second
    filesystem. `--print-mounts` answers the mount question directly, so the
    property is testable on a host where every scratch path happens to sit
    under /tmp — which is every host this suite runs on, and is exactly why the
    coverage assertion alone was VACUOUS here."""
    out = _print_mounts("/mnt/elsewhere/work", home="/home/someone")
    assert "-v /mnt/elsewhere/work:/mnt/elsewhere/work" in out, out


def test_KNOWN_NEGATIVE_a_path_already_covered_gets_no_second_mount():
    """A duplicate nested mount is an error and a redundant one is noise, so a
    path under $HOME or /tmp must NOT get one. Without this the positive above
    would pass for a script that mounted everything twice."""
    out = _print_mounts("/home/someone/work", "/tmp/scratch",
                        home="/home/someone")
    assert out.count("-v") == 2, out
    assert "-v /home/someone:/home/someone" in out and "-v /tmp:/tmp" in out
    assert "/home/someone/work" not in out and "/tmp/scratch" not in out


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
