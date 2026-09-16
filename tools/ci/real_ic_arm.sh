#!/usr/bin/env bash
# tools/ci/real_ic_arm.sh — THE REAL-IC LANDING ARM (OWNER RULING R-0915-86 (1)).
#
# WHY THIS ARM EXISTS
# -------------------
# MEASURED on this repo 2026-09-16: 3,713 test files, 173 of which invoke a real
# EDA tool, and NOT ONE runs an IC end-to-end. The flow-matrix suite checks that
# steps have gates WIRED — 45 of 61 cells in one of its dimensions are stand-ins
# — so the whole suite can be green while the flow it describes is broken on a
# real design. Every rule bug found that day surfaced only when a REAL run
# reached a later step: the r26 `stage_on_pass_review` cascade, run16's
# INCONCLUSIVE equivalence record booked as a waived pass, the DRV census taken
# with no parasitics in STA. None of the three is reachable from a fixture.
#
# So the landing chain gets a third arm beside the test arms, and it asks the
# only question a test cannot: DOES A REAL IC STILL COME OUT THE SAME?
#
#   1. `real_ic_gate`   SPM through the plugin's own front door on the CANDIDATE
#                       tree, in the pinned image, diffed against the published
#                       cell's per-step verdict table. ~8-16 min.
#   2. `audit_replay`   every frozen snapshot under $FROZEN_ROOT re-judged with
#                       the candidate's programs and diffed against its recorded
#                       baseline. Seconds each, no containers. This is how a
#                       JUDGEMENT change (R-0915-85's 23-words-to-5 reform, any
#                       audit/compliance/review change) is measured on r26 and
#                       run16 without re-running tools that no longer exist.
#
# NO SECOND HARNESS (R-0915-85, "不要設計的疊床架屋"). This script runs two shipped
# plugin programs and aggregates their exit codes. It computes no verdict of its
# own, stages nothing, and knows nothing about the flow. tools/ at the repo root
# is the LANDING side and is not shipped; the two programs above ARE shipped, so
# anyone can reproduce an arm result with one command and no landing machinery.
#
# USAGE
#   tools/ci/real_ic_arm.sh <candidate-tree> <benchmark-data-clone> [outdir]
#
#     <candidate-tree>        the checkout being landed — its programs/ run the IC
#     <benchmark-data-clone>  a clone of the benchmark-data corpus: the design
#                             INPUT to stage (§4.05: input ONLY) and the
#                             published cell that is the reference table
#     [outdir]                where real_ic_arm.json and the per-subject reports
#                             land. Default: $PWD/real_ic_arm_out
#
# ENV (all optional; each one is RECORDED into real_ic_arm.json)
#   REAL_IC_NAME        the IC to run end-to-end          (default: spm)
#   REAL_IC_PDK         its PDK                           (default: gf180mcuD)
#   FROZEN_ROOT         where the snapshots live          (default: $HOME/_frozen)
#   EDA_CONTAINER       the container the run uses        (default: real-ic-arm-eda,
#                       created from the plugin's own pin if absent)
#   REAL_IC_REFERENCE   the table the IC run is judged against. Omitted: the
#                       highest published cell for this ic+pdk — which, since
#                       R-0915-88, this arm will normally REFUSE: a published
#                       cell is produced by an AGENT-DRIVEN lane run and this arm
#                       runs the front door with no agent, so the steps whose
#                       second pass is an agent's disagree at every tree and the
#                       two tables are not the same experiment. MEASURED on SPM:
#                       D1 rc 0 vs rc 4 and step 5 PASS vs FAIL at six trees,
#                       none of it caused by a landing. Point this at a PRIOR
#                       real_ic_gate.json from a headless run and the diff
#                       becomes the measurement the arm is actually for.
#   REAL_IC_REPLAY_REF_DIR   directory of per-snapshot baseline reports to diff
#                       against, named <snapshot>.json. Absent: each snapshot's
#                       OWN recorded table is used, which REFUSES (rc 2) when the
#                       run's last compliance pass was stage-scoped — see
#                       `audit_replay`'s docstring. A refusal is a refusal, never
#                       a pass, and this arm counts it as a non-zero subject.
#
# EXIT
#   0  every subject came back NO_REGRESSION.
#   1  at least one subject REGRESSED — a real IC got worse on this tree.
#   2  at least one subject REFUSED and none regressed: nothing is claimed about
#      it, which is not a pass either. A landing arm that let a refusal through
#      as green would be the zero-denominator green this repo already has a rule
#      against.
#
# TIME IS RECORDED, NEVER ENFORCED (owner's standing rule; R-0915-5). There is no
# `timeout`, no kill and no deadline in this file or in either program.
set -uo pipefail

usage() {
  sed -n '/^# USAGE/,/^# EXIT/p' "${BASH_SOURCE[0]}" >&2
  exit 2
}

[ "$#" -ge 2 ] || usage
TREE=$(cd "$1" && pwd) || usage
CORPUS=$(cd "$2" && pwd) || usage
OUTDIR=${3:-$PWD/real_ic_arm_out}
mkdir -p "$OUTDIR"
OUTDIR=$(cd "$OUTDIR" && pwd)

IC=${REAL_IC_NAME:-spm}
PDK=${REAL_IC_PDK:-gf180mcuD}
FROZEN=${FROZEN_ROOT:-$HOME/_frozen}
PROGRAMS="$TREE/vibe-ic-marketplace/plugins/vibe-ic/programs"

[ -d "$PROGRAMS" ] || {
  echo "[real_ic_arm] REFUSED: $TREE carries no plugin programs/ — it is not a candidate tree" >&2
  exit 2
}

# The image pin is the PLUGIN's, read from the candidate tree itself. A pin kept
# in this script would be a second copy of a number that moves.
PIN=$(cd "$PROGRAMS" && python3 -c 'import _eda_pin; print(_eda_pin.IMAGE_DIGEST)' 2>/dev/null || true)
CONTAINER=${EDA_CONTAINER:-real-ic-arm-eda}

# ONLY the default container is auto-created. An EDA_CONTAINER the caller NAMED
# is the caller's: "never attach to a container you did not create" cuts both
# ways, and an arm that silently manufactured a container under a name someone
# else owns would be the worse half of that rule.
if [ -z "${EDA_CONTAINER:-}" ] && ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  if [ -z "$PIN" ]; then
    echo "[real_ic_arm] REFUSED: no container $CONTAINER and the tree states no image pin" >&2
    exit 2
  fi
  # The memory ceiling is the PLUGIN's own computation, spliced after `run` —
  # never a number chosen here.
  MEMFLAGS=$(cd "$PROGRAMS" && python3 _docker_memory.py --flags | tr '\n' ' ')
  echo "[real_ic_arm] creating $CONTAINER from $PIN"
  # shellcheck disable=SC2086
  docker run -d --name "$CONTAINER" $MEMFLAGS \
    -v "$HOME:$HOME" -v /tmp:/tmp -e USER=designer \
    "$PIN" --skip sleep infinity >/dev/null || {
      echo "[real_ic_arm] REFUSED: could not create $CONTAINER from the pinned image" >&2
      exit 2
    }
fi

STARTED=$(date -Is)
SUBJECTS_JSON="$OUTDIR/.subjects.jsonl"
: > "$SUBJECTS_JSON"

echo "[real_ic_arm] IC reference: ${REAL_IC_REFERENCE:-<auto: highest published cell — expect a RUN SHAPE refusal, see the header>}"

record() {  # name kind rc report
  python3 - "$1" "$2" "$3" "$4" "$SUBJECTS_JSON" <<'PY'
import json, sys
name, kind, rc, report, out = sys.argv[1:6]
row = {"subject": name, "kind": kind, "rc": int(rc), "report": report}
try:
    d = json.load(open(report))
    row["verdict"] = d.get("verdict")
    row["refusal"] = d.get("refusal")
    diff = d.get("diff") or {}
    row["regressions"] = len(diff.get("regressions") or [])
    row["improvements"] = len(diff.get("improvements") or [])
    row["laterals"] = len(diff.get("laterals") or [])
    row["comparable"] = diff.get("comparable")
except Exception as exc:
    # THE EXIT CODE IS NOT THE MEASUREMENT. A subject that exits 0 and writes no
    # report has claimed nothing, and reading its rc alone would book that as a
    # pass — the quietest way for this arm to stop working. The row is FORCED to
    # the refusal code so the aggregate below cannot miss it.
    row["verdict"] = "REFUSED"
    row["report_readable"] = False
    row["exit_code_as_run"] = row["rc"]
    row["rc"] = 2
    row["refusal"] = "the subject wrote no readable report: %s" % exc
open(out, "a").write(json.dumps(row) + "\n")
PY
}

# ── subject 1: the real IC, through the front door ───────────────────────────
GATE_JSON="$OUTDIR/real_ic_gate_${IC}.json"
echo "[real_ic_arm] $IC end-to-end on $TREE (container $CONTAINER)"
python3 "$PROGRAMS/real_ic_gate.py" \
  --tree "$TREE" --ic "$IC" --pdk "$PDK" \
  --benchmark-data "$CORPUS" \
  --workdir "$OUTDIR/run" \
  --container "$CONTAINER" \
  ${PIN:+--require-image "$PIN"} \
  ${REAL_IC_REFERENCE:+--reference "$REAL_IC_REFERENCE"} \
  --json "$GATE_JSON" > "$OUTDIR/real_ic_gate_${IC}.log" 2>&1
record "$IC" real_ic_gate "$?" "$GATE_JSON"

# ── subject 2..n: every frozen snapshot, re-judged ───────────────────────────
if [ -d "$FROZEN" ]; then
  for snap in "$FROZEN"/*/; do
    [ -d "${snap}reports" ] || continue          # not a run snapshot
    name=$(basename "$snap")
    ref=""
    if [ -n "${REAL_IC_REPLAY_REF_DIR:-}" ] && [ -f "$REAL_IC_REPLAY_REF_DIR/$name.json" ]; then
      ref="$REAL_IC_REPLAY_REF_DIR/$name.json"
    fi
    echo "[real_ic_arm] replay $name${ref:+ against $ref}"
    python3 "$PROGRAMS/audit_replay.py" "${snap%/}" \
      --tree "$TREE" --workdir "$OUTDIR/replay" \
      ${ref:+--reference "$ref"} \
      --json "$OUTDIR/audit_replay_$name.json" \
      > "$OUTDIR/audit_replay_$name.log" 2>&1
    record "$name" audit_replay "$?" "$OUTDIR/audit_replay_$name.json"
  done
else
  echo "[real_ic_arm] NOTE: no frozen snapshots at $FROZEN — the replay half of" \
       "this arm measured nothing, and that is RECORDED, not passed over" >&2
fi

# ── aggregate ────────────────────────────────────────────────────────────────
python3 - "$SUBJECTS_JSON" "$OUTDIR/real_ic_arm.json" "$TREE" "$CORPUS" \
         "$CONTAINER" "$STARTED" "$FROZEN" <<'PY'
import json, subprocess, sys
subjects_path, out, tree, corpus, container, started, frozen = sys.argv[1:8]
rows = [json.loads(l) for l in open(subjects_path) if l.strip()]


def _sha(path):
    try:
        return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip() or None
    except Exception:
        return None


def _image(name):
    try:
        return subprocess.run(["docker", "inspect", "--format", "{{.Image}}", name],
                              capture_output=True, text=True).stdout.strip() or None
    except Exception:
        return None


regressed = [r for r in rows if r["rc"] == 1]
refused = [r for r in rows if r["rc"] == 2]
rc = 1 if regressed else (2 if refused else 0)
verdict = ("REGRESSION" if regressed else
           "REFUSED" if refused else
           "NO_REGRESSION" if rows else "NOTHING_MEASURED")
if not rows:
    rc = 2
report = {
    "schema_version": 1, "arm": "real_ic_arm", "started_at": started,
    "tree": tree, "tree_head": _sha(tree),
    "benchmark_data": corpus, "benchmark_data_head": _sha(corpus),
    "container": container, "container_image": _image(container),
    "frozen_root": frozen,
    "verdict": verdict,
    "subject_count": len(rows),
    "regressed": [r["subject"] for r in regressed],
    "refused": [r["subject"] for r in refused],
    "subjects": rows,
}
open(out, "w").write(json.dumps(report, indent=2))
print("")
print("REAL-IC ARM %s — %d subject(s): %d regressed, %d refused"
      % (verdict, len(rows), len(regressed), len(refused)))
for r in rows:
    print("  %-26s %-14s rc=%d %s%s"
          % (r["subject"], r["kind"], r["rc"], r.get("verdict"),
             (" — " + (r.get("refusal") or "")[:100]) if r.get("refusal") else ""))
print("wrote %s" % out)
sys.exit(rc)
PY
exit $?
