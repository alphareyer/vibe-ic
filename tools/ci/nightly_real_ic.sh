#!/usr/bin/env bash
# tools/ci/nightly_real_ic.sh — the SUBSERVIENT half of R-0915-86 (1).
#
# WHY A SECOND, SLOWER IC
# -----------------------
# The landing arm runs SPM because SPM is the cheapest full-flow
# PASS_WITH_WAIVERS in the corpus (~8-16 min): fast enough to sit in front of
# every landing. Subservient takes ~55 min, which is not, and subservient is
# where a whole class of defect actually lives — it is the IC whose r26 run
# exposed the `stage_on_pass_review` cascade (R-0915-80) and whose r12 run
# exposed the child->parent DEF return leg (R-0915-18). An IC that only ever
# runs when a lane happens to pick it up is an IC whose regressions are found
# by accident.
#
# So: SPM on every landing, subservient once a night on origin/main, its
# per-step verdict table APPENDED to a dated directory under the frozen root.
# The dated tables are the series — "when did step 15 stop passing?" is answered
# by reading them in order, which is a question nothing in this repo could
# answer before.
#
# NO SECOND HARNESS (R-0915-85). This runs `real_ic_gate`, the same shipped
# program the landing arm runs, with a different IC name. It is the arm's
# schedule that differs, not its mechanism.
#
# USAGE
#   tools/ci/nightly_real_ic.sh [<workroot>]
#
#     <workroot>  where the nightly clone and run tree live.
#                 Default: $HOME/_nightly_real_ic
#
# ENV (optional, each RECORDED into the emitted report)
#   NIGHTLY_IC        the IC to run          (default: subservient)
#   NIGHTLY_PDK       its PDK                (default: gf180mcuD)
#   FROZEN_ROOT       where nightly/<date>/ is written (default: $HOME/_frozen)
#   VIBE_IC_REMOTE    the repo to clone      (default: https://github.com/vibeic/vibe-ic.git)
#   BDATA_REMOTE      the corpus to clone    (default: https://github.com/vibeic/benchmark-data.git)
#   EDA_CONTAINER     the container to run in (default: nightly-real-ic-eda)
#
# THE CRON LINE (Asia/Taipei; state it, the dispatcher installs it):
#
#   CRON_TZ=Asia/Taipei
#   30 2 * * * /home/reyerchu/_lane_icspmgate/clone/tools/ci/nightly_real_ic.sh \
#              >> /home/reyerchu/_frozen/nightly/cron.log 2>&1
#
# 02:30 deliberately: it is after the landing day ends and before the 05:30
# fork-gatekeeper loop, so the ~55 min run has the cores to itself. There is no
# `timeout` and no kill in this file — if a night's run overruns, the next
# night's simply finds the lock held and RECORDS that, which is a true statement
# about the flow and better than a killed run that says nothing.
set -uo pipefail

IC=${NIGHTLY_IC:-subservient}
PDK=${NIGHTLY_PDK:-gf180mcuD}
FROZEN=${FROZEN_ROOT:-$HOME/_frozen}
WORKROOT=${1:-$HOME/_nightly_real_ic}
DATE=$(date +%Y-%m-%d)
OUT="$FROZEN/nightly/$DATE"
mkdir -p "$OUT" "$WORKROOT"

LOCK="$WORKROOT/.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "[nightly_real_ic] a previous night's run still holds $LOCK — RECORDING that and exiting" \
    | tee "$OUT/${IC}_SKIPPED_LOCK_HELD.txt"
  exit 2
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

# A FRESH full clone every night. Never `--shared`, never `--reference`, never a
# worktree: a clone of a local repo inherits that repo's own stale `main`, which
# is how a nightly silently measures a tree from last week.
CLONE="$WORKROOT/clone"
rm -rf "$CLONE"
git clone "${VIBE_IC_REMOTE:-https://github.com/vibeic/vibe-ic.git}" "$CLONE" \
  > "$OUT/${IC}_clone.log" 2>&1 || {
    echo "[nightly_real_ic] REFUSED: could not clone the repo" | tee -a "$OUT/${IC}_clone.log"
    exit 2
  }
CORPUS="$WORKROOT/benchmark-data"
rm -rf "$CORPUS"
git clone "${BDATA_REMOTE:-https://github.com/vibeic/benchmark-data.git}" "$CORPUS" \
  > "$OUT/${IC}_bdata_clone.log" 2>&1 || {
    echo "[nightly_real_ic] REFUSED: could not clone benchmark-data" | tee -a "$OUT/${IC}_bdata_clone.log"
    exit 2
  }

HEAD=$(git -C "$CLONE" rev-parse HEAD)
BHEAD=$(git -C "$CORPUS" rev-parse HEAD)
echo "[nightly_real_ic] $DATE  $IC/$PDK  main=$HEAD  benchmark-data=$BHEAD"

ARM="$CLONE/tools/ci/real_ic_arm.sh"
[ -x "$ARM" ] || chmod +x "$ARM" 2>/dev/null || true

# The arm IS the mechanism; the nightly only changes which IC and turns off the
# replay half (the frozen snapshots are the landing arm's subject, and replaying
# them nightly would be the same measurement twice).
REAL_IC_NAME="$IC" REAL_IC_PDK="$PDK" \
FROZEN_ROOT="$WORKROOT/no_snapshots" \
EDA_CONTAINER="${EDA_CONTAINER:-nightly-real-ic-eda}" \
  bash "$ARM" "$CLONE" "$CORPUS" "$OUT/arm" > "$OUT/${IC}_arm.log" 2>&1
rc=$?

# APPEND the per-step table to the dated directory. The table is the series; a
# verdict word alone cannot answer "when did step 15 stop passing?".
python3 - "$OUT/arm/real_ic_gate_${IC}.json" "$OUT/${IC}_table.json" \
         "$HEAD" "$BHEAD" "$DATE" "$rc" <<'PY'
import json, sys
src, dst, head, bhead, date, rc = sys.argv[1:7]
try:
    d = json.load(open(src))
except Exception as exc:
    json.dump({"date": date, "tree_head": head, "benchmark_data_head": bhead,
               "arm_rc": int(rc), "verdict": "REFUSED",
               "refusal": "the nightly wrote no readable gate report: %s" % exc},
              open(dst, "w"), indent=2)
    print("NIGHTLY REFUSED: no readable gate report at %s (%s)" % (src, exc))
    raise SystemExit(0)
out = {
    "date": date, "tree_head": head, "benchmark_data_head": bhead,
    "arm_rc": int(rc),
    "verdict": d.get("verdict"), "refusal": d.get("refusal"),
    "orchestrator": d.get("orchestrator"),
    "run": {k: (d.get("run") or {}).get(k) for k in ("rc", "elapsed_s")},
    "reference_selection": d.get("reference_selection"),
    "table": d.get("table"),
    "diff": d.get("diff"),
}
json.dump(out, open(dst, "w"), indent=2)
t = d.get("table") or {}
print("NIGHTLY %s %s: %s over %s step(s); wrote %s"
      % (date, head[:9], d.get("verdict"), t.get("step_count"), dst))
PY

echo "[nightly_real_ic] arm rc=$rc; tables under $OUT"
exit $rc
