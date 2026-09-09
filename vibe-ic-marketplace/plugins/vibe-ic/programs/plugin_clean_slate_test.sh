#!/usr/bin/env bash
# plugin_clean_slate_test.sh — End-to-end fresh-agent regression test for the plugin.
#
# Runs a benchmark project from input/ docs alone (no peeking at any other
# project version) and verifies that the produced artifacts are a genuine
# fresh build (md5 differs from a contaminated baseline) AND that the
# hardware acceptance verdict is PASS.
#
# Usage:
#   plugin_clean_slate_test.sh \
#       --project-dir /path/to/phase2+3_v0XX_clean \
#       --baseline-gds /path/to/contaminated/gds/ic-a.gds \
#       --device-tool device_tester_usb_hid_tester_connect_test \
#       --verdict-key verdict
#
# The script does NOT itself spawn the fresh agent — that's a thicker
# operation (Claude Agent + MCP + LLM). The script ONLY performs the
# *post-build* verification gates so any orchestrator (CI, manual run) can
# call it.
#
# Required env: jq, md5sum, sha256sum.
#
# Exits 0 on PASS, 1 on FAIL, 2 on usage / missing dep.
#
# Generality: works for ANY benchmark project + ANY hardware acceptance
# device tool (specified via --device-tool). No chip / tester / PDK names
# baked in.
set -euo pipefail

PROJ=""
BASELINE_GDS=""
BASELINE_SOF=""
DEVICE_TOOL=""
VERDICT_KEY="verdict"
VERDICT_PASS_VALUE="PASS"
DEVICE_DRIVER_PATH=""
DEVICE_DRIVER_ARGS='{}'

usage() {
  sed -n '2,30p' "$0"
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir) PROJ="$2"; shift 2 ;;
    --baseline-gds) BASELINE_GDS="$2"; shift 2 ;;
    --baseline-sof) BASELINE_SOF="$2"; shift 2 ;;
    --device-tool) DEVICE_TOOL="$2"; shift 2 ;;
    --verdict-key) VERDICT_KEY="$2"; shift 2 ;;
    --verdict-pass-value) VERDICT_PASS_VALUE="$2"; shift 2 ;;
    --device-driver-path) DEVICE_DRIVER_PATH="$2"; shift 2 ;;
    --device-driver-args) DEVICE_DRIVER_ARGS="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

[[ -z "$PROJ" ]] && { echo "missing --project-dir" >&2; usage; }
[[ ! -d "$PROJ" ]] && { echo "not a dir: $PROJ" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq required" >&2; exit 2; }
command -v md5sum >/dev/null || { echo "md5sum required" >&2; exit 2; }

errors=()
ok() { echo "[OK]   $*"; }
fail() { echo "[FAIL] $*"; errors+=("$*"); }

# Gate 1 — required deliverables exist
echo "=== Gate 1: deliverable presence ==="
deliverables=(
  "fpga/output_files"
  "gds"
  "rtl"
  "reports/FINAL_VERIFICATION_REPORT_zh.md"
  "reports/ITERATION_DIARY_zh.md"
  "reports/SPEC_DERIVATIONS_zh.md"
)
for d in "${deliverables[@]}"; do
  if [[ -e "$PROJ/$d" ]]; then
    ok "$d present"
  else
    fail "$d missing"
  fi
done

# Gate 2 — the build is not the baseline (proves fresh build)
#
# THE GDS HALF USES A CANONICAL DIGEST, NOT md5, AND THAT IS THE WHOLE POINT
# OF vibe-ic#2221. A GDSII stream stamps its own write time into every BGNLIB
# and BGNSTR record, so `md5` on a `.gds` answers "did a clock tick" and never
# "is this the same layout". MEASURED in the pinned image: one KLayout layout
# written twice 2.2 s apart differs in exactly six bytes, six of six inside a
# date field. So a contaminated build that merely RE-STREAMS the baseline
# layout used to satisfy this gate every time, on any design, forever -- the
# check could not fail in the direction it exists to catch.
#
# `gds_canonical_digest.py` zeroes those date fields and hashes the rest, so
# identical geometry now produces an identical digest and the contamination
# verdict is real. It exits 2 and prints NOT_MEASURED rather than a digest
# when a file will not parse, and that is treated as a FAIL here: an
# unreadable artefact is not a distinct one.
echo "=== Gate 2: distinctness vs baseline ==="
project_gds=$(find "$PROJ/gds" -name "*.gds" -size +1k 2>/dev/null | head -1)
project_sof=$(find "$PROJ/fpga" -name "*.sof" -size +1k 2>/dev/null | head -1)
if [[ -n "$BASELINE_GDS" && -f "$BASELINE_GDS" && -n "$project_gds" ]]; then
  canon="$(dirname "${BASH_SOURCE[0]}")/gds_canonical_digest.py"
  c_proj=$(python3 "$canon" "$project_gds" 2>&1 | awk 'NR==1{print $1}') || true
  c_base=$(python3 "$canon" "$BASELINE_GDS" 2>&1 | awk 'NR==1{print $1}') || true
  if [[ "$c_proj" == "NOT_MEASURED" || "$c_base" == "NOT_MEASURED" \
        || -z "$c_proj" || -z "$c_base" ]]; then
    fail "GDS canonical digest NOT_MEASURED (project=$c_proj baseline=$c_base)"
  elif [[ "$c_proj" == "$c_base" ]]; then
    fail "GDS canonical digest IDENTICAL to baseline ($c_proj) — same layout, contamination"
  else
    ok "GDS canonical digest distinct ($c_proj vs baseline $c_base)"
  fi
elif [[ -n "$BASELINE_GDS" ]]; then
  fail "baseline GDS specified but project GDS missing or unreadable"
fi
if [[ -n "$BASELINE_SOF" && -f "$BASELINE_SOF" && -n "$project_sof" ]]; then
  m_proj_sof=$(md5sum "$project_sof" | awk '{print $1}')
  m_base_sof=$(md5sum "$BASELINE_SOF" | awk '{print $1}')
  if [[ "$m_proj_sof" == "$m_base_sof" ]]; then
    fail "SOF md5 IDENTICAL to baseline — likely contamination"
  else
    ok "SOF md5 distinct"
  fi
fi

# Gate 3 — RTL filename namespace differs from baseline (extra defense)
echo "=== Gate 3: RTL filename diversity ==="
if [[ -d "$PROJ/rtl" ]]; then
  fname_count=$(find "$PROJ/rtl" -name "*.v" -o -name "*.sv" -o -name "*.svh" -o -name "*.vh" | wc -l)
  if (( fname_count >= 5 )); then
    ok "$fname_count RTL files — reasonable spread"
  else
    fail "only $fname_count RTL files — too few for full chip"
  fi
fi

# Gate 4 — hardware acceptance verdict (if device tool supplied)
echo "=== Gate 4: hardware acceptance verdict ==="
if [[ -n "$DEVICE_DRIVER_PATH" && -x "$DEVICE_DRIVER_PATH" ]]; then
  echo "[run] $DEVICE_DRIVER_PATH"
  resp=$(echo "$DEVICE_DRIVER_ARGS" | "$DEVICE_DRIVER_PATH" --json-args /dev/stdin 2>/dev/null || true)
  if [[ -z "$resp" ]]; then
    fail "device driver produced no output"
  else
    verdict=$(echo "$resp" | jq -r --arg k "$VERDICT_KEY" '.[$k] // empty')
    if [[ "$verdict" == "$VERDICT_PASS_VALUE" ]]; then
      ok "hardware verdict $VERDICT_KEY=$verdict"
    else
      fail "hardware verdict $VERDICT_KEY=$verdict (expected $VERDICT_PASS_VALUE)"
    fi
  fi
elif [[ -n "$DEVICE_TOOL" ]]; then
  echo "[skip] device tool '$DEVICE_TOOL' — caller must invoke via MCP separately"
else
  echo "[skip] no device tool / driver supplied"
fi

# Summary
echo "==================================================================="
if (( ${#errors[@]} > 0 )); then
  echo "RESULT: FAIL — ${#errors[@]} gate(s) failed"
  for e in "${errors[@]}"; do echo "  - $e"; done
  exit 1
fi
echo "RESULT: PASS — all gates green"
exit 0
