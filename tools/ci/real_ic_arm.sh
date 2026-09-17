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
#   tools/ci/real_ic_arm.sh --print-mounts <path>...
#                             print the `-v` flags a container would need to see
#                             those paths, and exit. A diagnostic for the
#                             "container cannot see ..." refusal below; it
#                             creates nothing and runs nothing.
#
# ENV (all optional; each one is RECORDED into real_ic_arm.json)
#   REAL_IC_NAME        the IC to run end-to-end          (default: spm)
#   REAL_IC_PDK         its PDK                           (default: gf180mcuD)
#   FROZEN_ROOT         where the snapshots live          (default: $HOME/_frozen)
#   EDA_CONTAINER       the container the run uses        (default: real-ic-arm-eda,
#                       created from the plugin's own pin if absent)
#   REAL_IC_LOCK        the host's one-real-IC-run-at-a-time lock file
#                       (default: $TMPDIR/real_ic_arm.host.lock). The arm WAITS
#                       on it — it never refuses and never kills. Give two
#                       concurrent arms different paths only if they are on
#                       different machines' filesystems.
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

# WHICH HOST PATHS THE CONTAINER MUST BE ABLE TO SEE.
#
# $HOME and /tmp are mounted because most runs live there; each of the three
# paths this run actually touches is mounted too, UNLESS one of those already
# covers it (a duplicate nested mount is an error, and a redundant one is noise).
# See the measured defect at the creation site below.
mounts_for() {
  local out="-v $HOME:$HOME -v /tmp:/tmp" d m covered
  for d in "$@"; do
    covered=""
    for m in "$HOME" /tmp; do
      case "$d" in "$m"|"$m"/*) covered=1 ;; esac
    done
    [ -n "$covered" ] && continue
    case " $out " in *" -v $d:$d "*) continue ;; esac
    out="$out -v $d:$d"
  done
  printf '%s' "$out"
}

# A DIAGNOSTIC, not a test hook: "why can't the container see my workdir?" is
# the first question after the refusal below, and it must be answerable without
# starting a 17-minute run or creating a container.
if [ "${1:-}" = "--print-mounts" ]; then
  shift
  mounts_for "$@"
  echo
  exit 0
fi

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

# THE PIN IS THE PLUGIN'S, AND SO IS THE RESOLUTION.
#
# `_eda_pin.IMAGE_DIGEST` is the IDENTITY a verdict can be replayed against, and
# it is what `--require-image` compares. It is NOT a runnable reference: the
# module says so itself (`is_bare_image_id` -> True, `IMAGE_ID_NOT_A_REFERENCE`),
# and a `docker run sha256:89a8...` gets "No such image" from the daemon even on
# a host that demonstrably holds those bytes. MEASURED: the third arm's first
# landing run REFUSED for exactly that, on 2026-09-16.
#
# `_eda_pin.pinned_image_present()` is the resolver — it answers "does THIS host
# hold an image whose registry digest is the pin, under ANY repository name" and
# returns the runnable `<repo>@<digest>`. Calling it, rather than re-deriving a
# reference here, is the whole point: this script must own no image logic, and
# the one place that logic lives already handles the mirror-vs-ghcr name, the
# dangling pulled-by-digest image (`docker image ls -a`), and the difference
# between "absent" and "could not be asked".
# NO `|| true` HERE. "could not resolve" is a reachable state now, and
# swallowing it left PIN empty, which downstream reads as "no requirement"
# and FAILS OPEN. An arm that cannot name its runtime must refuse.
PIN=$(cd "$PROGRAMS" && python3 -c 'import _eda_pin; print(_eda_pin.IMAGE_DIGEST)' 2>/dev/null)
if [ -z "${PIN:-}" ]; then
  echo "[REFUSE] real_ic_arm: the EDA image identity could not be resolved on this host; nothing was measured." >&2
  exit 2
fi
resolve_runnable_image() {
  (cd "$PROGRAMS" && python3 -c '
import sys
import _eda_pin
ref, why = _eda_pin.pinned_image_present()
if ref is None:
    sys.stderr.write(why + "\n")
    raise SystemExit(2)
print(ref)
')
}
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
  # A host that does not hold the pinned bytes REFUSES BY NAME and creates
  # nothing. "Could not resolve the pin" is not "run it anyway with whatever is
  # here": the resolver has no fallback to :latest or to the newest local tag,
  # deliberately, and neither does this.
  if ! RUNNABLE=$(resolve_runnable_image 2>"$OUTDIR/.pin_refusal"); then
    echo "[real_ic_arm] REFUSED: the pinned image is not runnable on this host —" \
         "$(cat "$OUTDIR/.pin_refusal" 2>/dev/null)" >&2
    exit 2
  fi
  # The memory ceiling is the PLUGIN's own computation, spliced after `run` —
  # never a number chosen here.
  MEMFLAGS=$(cd "$PROGRAMS" && python3 _docker_memory.py --flags | tr '\n' ' ')
  # EVERY PATH THIS RUN TOUCHES IS MOUNTED, not just $HOME.
  #
  # MEASURED 2026-09-16, the arm's first run on a host whose workdir is NOT under
  # $HOME: with `-v $HOME:$HOME -v /tmp:/tmp` alone a workdir on another
  # filesystem is INVISIBLE inside the container, every in-container step dies in
  # seconds ("cd: .../sim_professional/<top>: No such file or directory"; LEC
  # "FAIL rc=1 elapsed=3s"), and the gate then reports dozens of PHANTOM
  # regressions with a straight face. A landing arm that manufactures its own
  # findings is worse than no arm.
  MOUNTS=$(mounts_for "$OUTDIR" "$TREE" "$CORPUS")
  echo "[real_ic_arm] creating $CONTAINER from $RUNNABLE (pin $PIN); mounts:$MOUNTS"
  # shellcheck disable=SC2086
  docker run -d --name "$CONTAINER" $MEMFLAGS $MOUNTS -e USER=designer \
    "$RUNNABLE" --skip sleep infinity >/dev/null || {
      echo "[real_ic_arm] REFUSED: could not create $CONTAINER from $RUNNABLE" >&2
      exit 2
    }
fi

# THE CONTAINER MUST BE ABLE TO SEE WHAT THE RUN WILL WRITE — checked BEFORE the
# runner starts, on whatever container is in use, including one the caller
# named and this script did not create. Asked of the container itself rather
# than inferred from the `-v` flags: a mount that exists in the argv and not in
# the namespace answers the wrong question. Skipped when the container does not
# exist at all — `real_ic_gate` refuses on that by name, and two lanes refusing
# the same thing is the duplication this arm avoids everywhere else.
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  UNSEEN=""
  for d in "$OUTDIR" "$TREE"; do
    docker exec "$CONTAINER" test -d "$d" >/dev/null 2>&1 || UNSEEN="$UNSEEN $d"
  done
  if [ -n "$UNSEEN" ]; then
    echo "[real_ic_arm] REFUSED: container $CONTAINER cannot see:$UNSEEN —" \
         "the run's in-container steps would fail in seconds and the gate would" \
         "report phantom regressions. Mount those paths, or name a container" \
         "that already does." >&2
    exit 2
  fi
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
#
# ONE REAL-IC RUN AT A TIME ON A HOST. A full-flow run wants ~8 cores and a 31 GB
# EDA container; two at once do not halve the wall-clock, they make both runs
# measure the machine's contention instead of the candidate — and this gate's
# whole claim is that a diff it prints is about the TREE. `flock` is the right
# primitive and a `while [ -e lockfile ]` loop is not: the kernel releases it
# when the holder dies, so a killed landing cannot leave a lock that blocks the
# host forever, and there is no stale-lock timeout to get wrong.
#
# IT WAITS; IT DOES NOT REFUSE AND IT DOES NOT KILL. A second landing arriving
# during a run is NORMAL — the queue is serialised, not rejected — and the
# owner's standing rule forbids a deadline that terminates a run. The wait is
# announced with the lock path so a reader who wonders why nothing is happening
# is not left guessing.
REAL_IC_LOCK=${REAL_IC_LOCK:-${TMPDIR:-/tmp}/real_ic_arm.host.lock}
GATE_JSON="$OUTDIR/real_ic_gate_${IC}.json"
if command -v flock >/dev/null 2>&1 && exec 9>"$REAL_IC_LOCK" 2>/dev/null; then
  if ! flock -n 9; then
    echo "[real_ic_arm] another real-IC run holds $REAL_IC_LOCK — WAITING (no timeout, no kill)"
    _lock_t0=$(date +%s)
    flock 9
    echo "[real_ic_arm] lock acquired after $(( $(date +%s) - _lock_t0 ))s"
  fi
  echo "[real_ic_arm] holding the host real-IC lock $REAL_IC_LOCK"
else
  # RECORDED, not passed over: without the lock two runs can overlap and each
  # one's numbers are about the machine, not the tree.
  echo "[real_ic_arm] NOTE: no host real-IC lock (flock unavailable or $REAL_IC_LOCK not writable) —" \
       "a concurrent run on this host would make both measurements about contention" >&2
fi
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
# Released as soon as the IC run is done: the replays below need no container
# and no cores worth serialising, and holding it through them would queue other
# landings behind work that costs seconds.
exec 9>&- 2>/dev/null || true

# ── subject 2..n: every frozen snapshot, re-judged ───────────────────────────
if [ -d "$FROZEN" ]; then
  for snap in "$FROZEN"/*/; do
    [ -d "${snap}reports" ] || continue          # not a run snapshot
    name=$(basename "$snap")
    ref=""
    mkbase=""
    if [ -n "${REAL_IC_REPLAY_REF_DIR:-}" ]; then
      if [ -f "$REAL_IC_REPLAY_REF_DIR/$name.json" ]; then
        ref="$REAL_IC_REPLAY_REF_DIR/$name.json"
      else
        # FIRST ENCOUNTER. A snapshot's OWN recorded audit is written by whichever
        # compliance pass ran LAST in that run, which is routinely a stage-scoped
        # one (measured: 9 steps vs a full pass's 69) — so using it refuses
        # forever, and a gate that always refuses is a gate nobody reads. Record
        # the full-scope table once, at THIS tree, stamped with its sha; every
        # later run diffs against it. Reported as BASELINE_RECORDED, never as a
        # pass: this snapshot was not measured on this run.
        mkbase="$REAL_IC_REPLAY_REF_DIR/$name.json"
      fi
    fi
    echo "[real_ic_arm] replay $name${ref:+ against $ref}${mkbase:+ — FIRST ENCOUNTER, recording a baseline at $mkbase}"
    python3 "$PROGRAMS/audit_replay.py" "${snap%/}" \
      --tree "$TREE" --workdir "$OUTDIR/replay" \
      ${ref:+--reference "$ref"} \
      ${mkbase:+--baseline "$mkbase"} \
      --json "$OUTDIR/audit_replay_$name.json" \
      > "$OUTDIR/audit_replay_$name.log" 2>&1
    rc_subject=$?
    if [ -n "$mkbase" ]; then kind=audit_replay_baseline; else kind=audit_replay; fi
    record "$name" "$kind" "$rc_subject" "$OUTDIR/audit_replay_$name.json"
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
# A subject seen for the FIRST time records a baseline instead of diffing. It is
# not a pass — nothing about the candidate was measured on it — so it is counted
# apart, and an arm whose subjects were ALL baselines measured nothing at all.
baselined = [r for r in rows if r["kind"] == "audit_replay_baseline"
             and r["rc"] == 0]
measured = [r for r in rows if r not in baselined]
rc = 1 if regressed else (2 if refused else (0 if measured else 2))
verdict = ("REGRESSION" if regressed else
           "REFUSED" if refused else
           "NO_REGRESSION" if measured else "NOTHING_MEASURED")
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
    "baselined_this_run": [r["subject"] for r in baselined],
    "measured_subject_count": len(measured),
    "subjects": rows,
}
open(out, "w").write(json.dumps(report, indent=2))
print("")
print("REAL-IC ARM %s — %d subject(s): %d measured, %d regressed, %d refused, "
      "%d baselined (first encounter, NOT a pass)"
      % (verdict, len(rows), len(measured), len(regressed), len(refused),
         len(baselined)))
for r in rows:
    print("  %-26s %-14s rc=%d %s%s"
          % (r["subject"], r["kind"], r["rc"], r.get("verdict"),
             (" — " + (r.get("refusal") or "")[:100]) if r.get("refusal") else ""))
print("wrote %s" % out)
sys.exit(rc)
PY
exit $?
