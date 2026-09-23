#!/usr/bin/env python3
"""
internal_vs_external_timing_check.py — L8 must separate host-side from DUT-side timing.

Problem statement (chip-AGNOSTIC):
  Half-duplex single-wire protocols measure timing at TWO distinct places:
    - HOST->DUT (external): how long the HOST drives low to send symbols
                           (e.g. logic-0, logic-1, break) to the DUT.
    - DUT->HOST (internal): how long the DUT should drive low when
                           responding back to the HOST.
  These two sets of values are NOT equal. Tolerance windows differ, the
  inter-byte time (IBT) is almost always different, and the DUT-side
  values are what the RTL TX state machine must use to drive the bus.

Failure mode caught by this gate:
  When L8_TIMING_WAVEFORM lists only host-side measurements, the
  Phase 2 RTL generator is forced to pick ONE set of numbers and will
  reuse the host-side values on the DUT side. If the host IBT is larger
  than the BR (break) threshold the tester uses, the tester interprets
  every inter-byte gap as a packet-end BR and truncates every response
  to zero usable bytes. End result: connect_test fails on every cycle.

  A gold reference L8 (hardware-PASSed) has both sets:
    rx_counters_at_2p5MHz  : {H1_low [1,9], H0_low [10,30], BR_low [31,65]}
    tx_cycles_at_5MHz      : {H1_low 9, H1_high 41, H0_low 35, H0_high 15,
                              BR_low 69, BR_high 18, IBT_high 60}
  The bad L8 has only the equivalent of the FIRST set (host-side), so this
  gate would have flagged it at generation time.

Rule enforced
-------------
For any protocol layer that does BOTH RX and TX on a shared wire, the
L8_TIMING_WAVEFORM document MUST carry TWO clearly-named groups:
  - An "rx_*" / "host_side_*" / "external_*" group: host-drive widths
    the DUT's RX decoder must tolerate.
  - A "tx_*" / "dut_side_*" / "internal_*" group: widths the DUT's TX
    encoder drives on the wire.
Both groups must cover the same symbol set (H0, H1, BR, IBT minimum).

Additionally, the DUT-side IBT **must** be strictly less than the
RX-side BR minimum threshold (otherwise the far side — which uses the
same RX spec — will misclassify IBT as a packet-end BR, which is the
exact v068 failure mode).

Usage
-----
    internal_vs_external_timing_check.py <L8_TIMING_WAVEFORM.json>
        [--layer L8_RTL_CONSTANTS.json]
        [--json]

If L8_RTL_CONSTANTS is provided, the IBT<BR cross-check also runs
against its numeric values (after unit normalisation to μs).

Exit codes
----------
    0 = both timing groups present with required symbols; IBT < BR
    1 = group missing, symbol missing, or IBT≥BR inversion
    2 = IO / JSON parse error
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _structural_absence as _sa   # noqa: E402  the class AND its enumeration

RX_NAME_HINTS = ("rx_", "host_", "external_", "host-side", "host_side",
                 "rx_counters", "detect_", "_tdt", "tHW", "tB_", "tDT",
                 "cmd_recv", "host_tx")
TX_NAME_HINTS = ("tx_", "dut_", "internal_", "dut-side", "dut_side",
                 "tx_cycles", "drive_", "_tdw", "TX_", "dut_tx",
                 "resp_emit", "cmd_resp", "wake_ack")
SYMBOLS_REQUIRED = ("H0", "H1", "BR", "IBT")


@dataclass
class Finding:
    severity: str          # "ERROR" | "WARN"
    rule: str
    field: str
    message: str


def _walk(obj: Any, path: str = ""):
    """Yield (path, value) for every leaf and every dict node."""
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        yield path, obj
    else:
        yield path, obj


def _classify_group(path_or_key: str) -> str | None:
    """Return 'rx' if path/key looks like host-side RX, 'tx' if DUT-side TX, else None."""
    s = path_or_key.lower()
    # Check tx first — "rx" substring may appear inside some tx keys
    for h in TX_NAME_HINTS:
        if h.lower() in s:
            return "tx"
    for h in RX_NAME_HINTS:
        if h.lower() in s:
            return "rx"
    return None


def _symbols_in(d: Any) -> set[str]:
    """Return the symbol set (H0, H1, BR, IBT) covered by a dict or list.

    IDENTIFIER TOKENS (`_identifier_tokens`), not substrings: "LIBRARY", "CALIBRATION"
    and "FABRIC" carry no BR and "CH0" no H0, which the substring reading
    counted as coverage (review of next/ictier1c). `break` is the spelled-out
    BR (`tB_break_us`).
    """
    def _syms(text: Any) -> set[str]:
        return {sym for t in _identifier_tokens(text)
                if (sym := _token_symbol(t)) is not None}

    out: set[str] = set()
    if isinstance(d, dict):
        for k in d.keys():
            out |= _syms(k)
    elif isinstance(d, list):
        for item in d:
            if isinstance(item, dict):
                for k in item.keys():
                    out |= _syms(k)
            elif isinstance(item, str):
                out |= _syms(item)
    return out


def _find_numeric_us(obj: Any, needle: str) -> float | None:
    """Lookup a timing value in μs under any key containing needle.

    Only returns values that can be confidently asserted in μs — either
    the key name contains '_us', OR the value has a '_us' sibling, OR
    a companion 'comment'/'desc' field explicitly names a μs figure.
    Returns None if we can't confirm μs (callers skip the cross-check
    rather than raise a false positive on tick-count values).
    """
    import re
    n = needle.upper()

    def _as_scalar(v):
        """Coerce a list/tuple [min, max] to its minimum; pass scalars through."""
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, (list, tuple)) and v and all(isinstance(x, (int, float)) for x in v):
            return float(min(v))
        return None

    # Pass 1: look for keys with _us and matching needle
    for p, v in _walk(obj):
        val = _as_scalar(v)
        if val is None:
            continue
        pu = p.upper()
        if n in pu and ("_US" in pu or "US_" in pu or pu.endswith("US")):
            return val
    # Pass 2: look for dict entries {name: ..., value: ...} where name contains needle
    # and sibling has explicit μs context.
    for p, v in _walk(obj):
        if not isinstance(v, dict):
            continue
        name = str(v.get("name", "")).upper()
        if n in name:
            comment = str(v.get("comment", "")) + " " + str(v.get("desc", ""))
            m = re.search(r'(\d+(?:\.\d+)?)\s*us', comment, re.IGNORECASE)
            if m:
                return float(m.group(1))
    # Pass 3: look for dict keys with min/nom containing μs values inside a
    # {"min_us": x, "nom_us": y, "max_us": z} shape where outer key has needle.
    for p, v in _walk(obj):
        if not isinstance(v, dict):
            continue
        if n not in p.upper():
            continue
        # pick nom else min else max
        for unit_key in ("nom_us", "nom", "min_us", "min", "max_us"):
            if unit_key in v and isinstance(v[unit_key], (int, float)):
                # Only accept when a sibling key suggests μs
                keys_str = " ".join(v.keys()).lower()
                if "us" in keys_str or p.lower().endswith("_us"):
                    return float(v[unit_key])
    return None


#: THE FULL PROBE'S VOCABULARY. Symbols are matched against IDENTIFIER TOKENS
#: (`_identifier_tokens`), never as substrings: "LIBRARY"/"CALIBRATION"/
#: "FABRIC" carry no BR and "CH0" no H0, while the fused spellings a timing
#: table really uses -- TIBT_US, TBR_MIN_US, IBTmin, H0low, tbreak_us -- do.
_TIMING_SYMBOLS = frozenset({"h0", "h1", "br", "ibt", "break"})
_TIMING_SIDE_WORDS = frozenset({"rx", "tx", "host", "dut", "master", "slave",
                                "external", "internal"})
#: The words main's escape read off top-level group names (its substring list
#: `rx_ tx_ host_ dut_ external_ internal_ _counters _cycles symbol _low _high
#: break ibt`), now as tokens. Kept so the escape is never narrower than main.
_LEGACY_GROUP_WORDS = frozenset({"rx", "tx", "host", "dut", "external",
                                 "internal", "counters", "cycles", "symbol",
                                 "symbols", "low", "high", "break", "ibt"})

#: PROVENANCE IS A SET OF FIELDS, NOT A SHAPE OF CONTENT (review of ictier1c
#: round 2). Where the design came from, how a value was extracted and what a
#: diagram is captioned say nothing about protocol timing, and reading them
#: FAILED designs main passed ("Wishbone master read cycle",
#: `L3_external_interface.asciidoc`). These fields are never probed; every
#: other field is, whatever its content looks like.
_PROVENANCE_FIELDS = frozenset({
    "source", "sources", "source_file", "source_files", "source_documents",
    "source_documents_derivation", "extraction_evidence",
    "extraction_strategy", "evidence", "provenance", "_generator",
    "caption", "title", "description", "desc", "comment", "comments", "note",
    "notes", "literal", "matched_substring", "strategy", "role", "doc_class",
    "ic_name", "schema_version",
})


def _identifier_tokens(text: Any) -> list[str]:
    """Lower-cased tokens of an identifier or phrase: split on anything that is
    not a letter or digit, on lower->Upper (`tIBT` -> t, ibt), on an upper run
    followed by lower (`IBTmin` -> ibt, min) and on digit->letter (`H0low` ->
    h0, low). Letter->digit never splits, so `H0` is `h0` and `CH0` is `ch0`."""
    import re
    out: list[str] = []
    for part in re.split(r"[^A-Za-z0-9]+", str(text)):
        part = re.sub(r"([a-z])([A-Z])", r"\1 \2", part)
        part = re.sub(r"([A-Z]{2,})([a-z])", r"\1 \2", part)
        part = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", part)
        out += [w.lower() for w in part.split() if w]
    return out


def _token_symbol(tok: str) -> str | None:
    """The timing symbol a token names, or None. A leading `t` is the timing-
    parameter prefix (`tIBT`, `TBR_MIN`, `tbreak`); `break` is the spelled-out
    BR."""
    for cand in (tok, tok[1:] if tok.startswith("t") else None):
        if cand in _TIMING_SYMBOLS:
            return "BR" if cand == "break" else cand.upper()
    return None


def _names_a_symbol(text: Any) -> bool:
    return any(_token_symbol(t) for t in _identifier_tokens(text))


def _names_a_side(text: Any) -> bool:
    return bool(set(_identifier_tokens(text)) & _TIMING_SIDE_WORDS)


def timing_content_probe(waveform: Any, rtl_constants: Any = None) -> list[str]:
    """Every place that carries protocol symbol timing, as paths.

    DECIDED BY FIELD (review of ictier1c round 2). Provenance fields are never
    read (`_PROVENANCE_FIELDS`, and a `waveforms[]` entry's own name, which is
    its caption); every other field is:
      * a key or a `name`/`symbol`/`signal` value, or a list string, that names
        a timing SYMBOL (H0/H1/BR/IBT/break, fused or not) -- a key only when
        it holds something (null/bool are declarations of absence);
      * a key that names a SIDE (rx/tx/host/dut/master/slave/external/
        internal) and holds a GROUP (dict or list) -- the shape `check()`
        treats as a side. A caption or a constant NAMED `master_clk` is not a
        side;
      * the `--layer` constants read exactly as `check()` reads them for the
        IBT<BR cross-check, through `_find_numeric_us`.
    """
    hits: list[str] = []

    def _walk(node: Any, path: str, in_waveforms: bool = False) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k) in _PROVENANCE_FIELDS:
                    continue
                sub = f"{path}.{k}" if path else str(k)
                if v is not None and not isinstance(v, bool):
                    if _names_a_symbol(k):
                        hits.append(sub)
                    elif isinstance(v, (dict, list)) and _names_a_side(k):
                        hits.append(sub)
                if (k in ("name", "symbol", "signal") and isinstance(v, str)
                        and not (in_waveforms and k == "name")
                        and _names_a_symbol(v)):
                    hits.append(f"{sub}={v}")
                _walk(v, sub, in_waveforms=False)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, str) and _names_a_symbol(item):
                    hits.append(f"{path}[{i}]={item}")
                _walk(item, f"{path}[{i}]", in_waveforms=in_waveforms)

    if isinstance(waveform, dict):
        for k, v in waveform.items():
            if str(k) in _PROVENANCE_FIELDS:
                continue
            _walk({k: v}, "L8", in_waveforms=False) if k != "waveforms" \
                else _walk(v, "L8.waveforms", in_waveforms=True)
    if rtl_constants is not None:
        _walk(rtl_constants, "layer")
        for needle in ("TX_IBT", "BR_MIN"):
            if _find_numeric_us(rtl_constants, needle) is not None:
                hits.append(f"layer:{needle}")
    return hits


def classified_groups(waveform: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(groups_rx, groups_tx)`` exactly as `check()` classifies them.

    THE ONE CLASSIFIER. `check()` and the structural-absence escape in `main()`
    both call this, so the escape can never certify "no RX/TX timing here" on a
    document `check()` would read as carrying (or naming) RX/TX timing. The
    review of next/icspm5-s2 measured the cost of two vocabularies: a
    `timing_groups` pair named `detect_thresholds` / `drive_widths` classified
    RX/TX here and was certified absent by the escape's own token list.

    v1.6.38 — first preference: explicit `timing_groups` field (new canonical
    L8 shape); its sub-keys are classified by name. Fallback: top-level keys.
    Only dicts and lists count as groups (free-form string notes often contain
    "internal"/"external" substrings but carry no per-symbol values); an EMPTY
    dict or list still counts -- a group that is named and empty is a missing-
    symbols finding, not an absence.
    """
    groups_rx: dict[str, Any] = {}
    groups_tx: dict[str, Any] = {}
    tg = waveform.get("timing_groups") if isinstance(waveform, dict) else None
    if isinstance(tg, dict):
        for key, val in tg.items():
            if not isinstance(val, (dict, list)):
                continue
            cls = _classify_group(key)
            if cls == "rx":
                groups_rx[key] = val
            elif cls == "tx":
                groups_tx[key] = val
    if not groups_rx and not groups_tx and isinstance(waveform, dict):
        for key, val in waveform.items():
            if not isinstance(val, (dict, list)):
                continue
            cls = _classify_group(key)
            if cls == "rx":
                groups_rx[key] = val
            elif cls == "tx":
                groups_tx[key] = val
    return groups_rx, groups_tx


def check(waveform: Any, rtl_constants: Any | None) -> list[Finding]:
    findings: list[Finding] = []
    groups_rx, groups_tx = classified_groups(waveform)

    if not groups_rx:
        findings.append(Finding(
            "ERROR", "missing_rx_group", "(root)",
            "No host-side / RX timing group found. L8 must carry a "
            "group with names like 'rx_*', 'host_*', 'external_*' "
            "containing per-symbol LOW-pulse widths the DUT's RX "
            "decoder must tolerate.",
        ))
    if not groups_tx:
        findings.append(Finding(
            "ERROR", "missing_tx_group", "(root)",
            "No DUT-side / TX timing group found. L8 must carry a "
            "group with names like 'tx_*', 'dut_*', 'internal_*' "
            "containing per-symbol pulse widths the DUT emits. "
            "Without this, Phase 2 will re-use host-side numbers for "
            "DUT TX — a known real-silicon FAIL root cause for half-duplex bus ICs.",
        ))

    # (2) Symbol coverage — each group that exists must cover H0,H1,BR,IBT
    # by default. v1.6.38 — for asymmetric half-duplex protocols where
    # one symbol is unidirectional (e.g. some bus protocols restrict
    # the BR / break symbol to host-side emission only), the L8 may
    # carry an explicit `symbol_directionality` map declaring which
    # symbols each side
    # actually emits. When present, only those symbols are required on
    # that side. Default behaviour (full SYMBOLS_REQUIRED on both sides)
    # is preserved when the map is absent.
    sym_dir = (waveform.get("symbol_directionality")
               if isinstance(waveform, dict) else None) or {}
    rx_required = set(sym_dir.get("rx_host_side", SYMBOLS_REQUIRED)) \
        if "rx_host_side" in sym_dir else set(SYMBOLS_REQUIRED)
    tx_required = set(sym_dir.get("tx_dut_side", SYMBOLS_REQUIRED)) \
        if "tx_dut_side" in sym_dir else set(SYMBOLS_REQUIRED)
    for g_name, g_val in groups_rx.items():
        syms = _symbols_in(g_val)
        missing = rx_required - syms
        if missing:
            findings.append(Finding(
                "ERROR", "rx_missing_symbols", g_name,
                f"host-side group missing symbols: {sorted(missing)}",
            ))
    for g_name, g_val in groups_tx.items():
        syms = _symbols_in(g_val)
        missing = tx_required - syms
        if missing:
            findings.append(Finding(
                "ERROR", "tx_missing_symbols", g_name,
                f"DUT-side group missing symbols: {sorted(missing)}",
            ))

    # (3) IBT < BR cross-check. Look in TX group first, fall back to
    #     RX group for BR threshold (RX BR threshold is what the far
    #     side uses to classify our IBT).
    tx_ibt_us = _find_numeric_us(groups_tx, "IBT") if groups_tx else None
    rx_br_us  = _find_numeric_us(groups_rx, "BR")  if groups_rx else None
    # Fall back to RTL constants if provided
    if tx_ibt_us is None and rtl_constants is not None:
        tx_ibt_us = _find_numeric_us(rtl_constants, "TX_IBT")
    if rx_br_us is None and rtl_constants is not None:
        rx_br_us = _find_numeric_us(rtl_constants, "BR_MIN")

    if tx_ibt_us is not None and rx_br_us is not None and tx_ibt_us >= rx_br_us:
        findings.append(Finding(
            "ERROR", "ibt_exceeds_br_threshold", "tx.IBT vs rx.BR",
            f"DUT-side IBT={tx_ibt_us}us is >= host-side BR={rx_br_us}us. "
            "Receiving side will classify our inter-byte gap as a "
            "packet-end BR and truncate the response. This is a real "
            "tester FAIL mode. Rule: IBT < BR minimum with margin.",
        ))

    return findings


# ORGANIC #617 — half-duplex protocol symbol-timing tokens. A `waveforms[]`
# entry carrying any of these references directional (rx/tx/host/dut) timing or
# H0/H1/BR/IBT-style symbol pulses; a generic single-/few-signal WaveDrom
# diagram (clk/addr/data/valid) emitted by doc-extraction carries none.
_WAVEFORM_PROTOCOL_TOKENS = (
    "rx_", "tx_", "host_", "dut_", "external_", "internal_",
    "_low", "_high", "ibt", "break", "_counters", "_cycles",
    "h0_", "h1_", "br_",
)


def _waveforms_carry_protocol_symbols(wfs: Any) -> bool:
    """ORGANIC #617 — True iff a `waveforms[]` value carries half-duplex
    protocol symbol-timing content (directional rx/tx/host/dut tokens or
    H0/H1/BR/IBT symbol pulses), as opposed to a GENERIC WaveDrom diagram
    (clk/addr/data/valid) auto-populated by doc-extraction. Conservative
    substring scan over the serialised value. chip-AGNOSTIC."""
    if not wfs:
        return False
    try:
        blob = json.dumps(wfs).lower()
    except (TypeError, ValueError):
        blob = str(wfs).lower()
    return any(tok in blob for tok in _WAVEFORM_PROTOCOL_TOKENS)


# ORGANIC #655 — half-duplex protocol per-symbol timing tokens for the
# `timing_constants[]` container. Mirrors the #617 `waveforms[]` treatment:
# a `timing_constants[]` entry carrying any of these references directional
# (rx/tx/host/dut) timing or H0/H1/BR/IBT-style symbol pulses; a bare scalar
# clock-frequency constant (e.g. {name:fclk,value:1.0,unit:MHz}) promoted into
# L8 by doc-extraction from an L5/spec clock table carries none. The tokens are
# the SAME protocol symbol-timing vocabulary as #617 — a scalar clk/fclk
# frequency name (clk*/fclk/sysclk/refclk…) with a Hz/MHz/GHz unit is NOT
# symbol-timing and must NOT defeat the VACUOUS_PASS escape.
_TIMING_CONSTANT_PROTOCOL_TOKENS = (
    "rx_", "tx_", "host_", "dut_", "external_", "internal_",
    "_low", "_high", "ibt", "break", "_counters", "_cycles",
    "h0_", "h1_", "br_", "_tdw", "_tdt", "tb_",
)


def _timing_constants_carry_protocol_symbols(tcs: Any) -> bool:
    """ORGANIC #655 — True iff a `timing_constants[]` value carries half-duplex
    protocol symbol-timing content (directional rx/tx/host/dut tokens or
    H0/H1/BR/IBT symbol pulses / per-symbol LOW/HIGH pulse widths), as opposed
    to a bare scalar clock-FREQUENCY constant (name like clk*/fclk, unit
    Hz/MHz/GHz, one numeric value) auto-promoted by doc-extraction from an
    L5/spec clock table. A scalar clock-frequency entry is NOT protocol
    symbol-timing — there is no RX/TX direction to split — so it must count as
    EMPTY for the VACUOUS_PASS escape. Conservative substring scan over the
    serialised value, SAME vocabulary as #617. chip-AGNOSTIC: keyed on the
    protocol symbol-timing token shape, not on any chip or unit."""
    if not tcs:
        return False
    try:
        blob = json.dumps(tcs).lower()
    except (TypeError, ValueError):
        blob = str(tcs).lower()
    return any(tok in blob for tok in _TIMING_CONSTANT_PROTOCOL_TOKENS)


def _resolve_waveform_path(arg: str) -> Path | None:
    """v1.6.38 — accept either an L8 JSON file directly OR a project_dir
    (in which case look up phase1/generated_docs/L8_TIMING_WAVEFORM.json).

    Returns None if neither shape resolves to an existing file.
    """
    p = Path(arg)
    if p.is_file():
        return p
    if p.is_dir():
        cand = p / "phase1" / "generated_docs" / "L8_TIMING_WAVEFORM.json"
        if cand.is_file():
            return cand
    return None


def _read_l2_half_duplex(arg: str) -> bool | None:
    """Return the project's L2 ``protocol_overview.half_duplex`` flag.

    Accepts EITHER a project_dir OR the L8 file path the flow manifest
    passes. When given a file under ``phase1/generated_docs/`` (the
    canonical flow-manifest invocation
    ``... L8_TIMING_WAVEFORM.json --layer ...``) we walk up to the
    project root and read the sibling ``L2_FRS.json``. Without this, the
    half-duplex VACUOUS_PASS escape never fires in the flow-manifest
    path, so every NON-half-duplex IC (memory-mapped register bus,
    pure-digital datapath, parallel bus) false-FAILs the RX/TX-split
    rule that only applies to single-wire half-duplex protocols.
    Returns None only when L2 genuinely cannot be located/parsed.
    Chip-AGNOSTIC.
    """
    p = Path(arg)
    candidates: list[Path] = []
    if p.is_dir():
        candidates.append(p / "phase1" / "generated_docs" / "L2_FRS.json")
    else:
        # arg is a file path — derive the generated_docs dir it lives in
        # and look for the sibling L2 doc. Also handle the project-root
        # form in case a caller passes <project>/phase1/...{L8}.
        gd = p.parent
        candidates.append(gd / "L2_FRS.json")
        # Walk up to a plausible project root (…/phase1/generated_docs/L8…)
        # → <project>/phase1/generated_docs/L2_FRS.json.
        for up in (p.parent, p.parent.parent, p.parent.parent.parent):
            candidates.append(up / "phase1" / "generated_docs" / "L2_FRS.json")
    for l2 in candidates:
        if not l2.is_file():
            continue
        try:
            d = json.loads(l2.read_text())
            po = d.get("protocol_overview") or {}
            if "half_duplex" in po:
                return bool(po["half_duplex"])
        except Exception:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Require L8 to separate host-side (RX) and DUT-side (TX) timing groups.",
    )
    ap.add_argument("waveform",
                    help=("L8_TIMING_WAVEFORM.json file path, OR a project "
                          "directory (auto-resolves "
                          "phase1/generated_docs/L8_TIMING_WAVEFORM.json)."))
    ap.add_argument("--layer", help="Optional L8_RTL_CONSTANTS.json for cross-check.")
    ap.add_argument("--json", nargs='?', const='-', default=None, metavar='PATH',
                    help="Emit JSON. With PATH writes to file; bare flag prints to stdout.")
    args = ap.parse_args()

    # v1.6.38 — accept project_dir as well as a direct file path.
    resolved = _resolve_waveform_path(args.waveform)
    if resolved is None:
        print(f"error: file not found: {args.waveform}", file=sys.stderr)
        return 2
    waveform_path = str(resolved)
    half_duplex_l2 = _read_l2_half_duplex(args.waveform)

    try:
        with open(waveform_path, "r") as f:
            waveform = json.load(f)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON in {waveform_path}: {e}", file=sys.stderr)
        return 2

    rtl_constants = None
    if args.layer:
        try:
            with open(args.layer, "r") as f:
                rtl_constants = json.load(f)
        except Exception as e:
            print(f"warning: could not read {args.layer}: {e}", file=sys.stderr)

    # v1.6.38 — VACUOUS_PASS gate when L2 explicitly declares this is NOT
    # a half-duplex protocol. The RX/TX split rule only applies to
    # half-duplex single-wire shared-direction protocols. Pure-digital
    # ICs and parallel-bus protocols don't have the directional-
    # asymmetry hazard the gate prevents.
    if half_duplex_l2 is False:
        msg = ("VACUOUS_PASS: L2 protocol_overview.half_duplex=false — "
               "RX/TX timing split rule does not apply.")
        if args.json:
            txt = json.dumps({
                "source_file": waveform_path,
                "total_findings": 0,
                "errors": 0,
                "findings": [],
                "verdict": "VACUOUS_PASS",
                # v1.15.45 (sha256 capture): the design's OWN L2 declaration
                # rules the rule out — say so in the vocabulary the audit
                # reads, or a green vacuous record is filed INCOMPLETE.
                "reason_class": "DESIGN_DECLARED_NA",
                "skip_kind": "class-not-applicable",
                "rationale": msg,
            }, indent=2)
            if args.json == "-":
                print(txt)
            else:
                Path(args.json).parent.mkdir(parents=True, exist_ok=True)
                Path(args.json).write_text(txt + "\n")
        else:
            print(msg)
        return 0

    # VACUOUS_PASS when the L8 waveform carries NO protocol / symbol timing
    # content at all. The RX/TX-split rule only applies to half-duplex
    # single-wire shared-direction protocols whose L8 records per-symbol
    # pulse widths (timing_windows / timing_constants / waveforms). A
    # pure-digital arithmetic / parallel-bus IC (e.g. an spm multiplier) has
    # only clock_domains and zero symbol timing — there is nothing to split,
    # so demanding rx_*/tx_* groups would FAIL every non-protocol IC. This
    # complements the explicit L2.half_duplex=false escape above for the
    # common case where L2 simply never mentions a protocol. chip-AGNOSTIC:
    # keyed on the absence of symbol-timing content, not on any chip.
    #
    # ORGANIC #617 — a `waveforms[]` entry counts as EMPTY for this escape
    # unless it actually carries half-duplex protocol symbol-timing content
    # (rx_/tx_/host_/dut_ directional tokens or H0/H1/BR/IBT-style symbol
    # pulses). doc-extraction auto-populates `waveforms[]` with a GENERIC
    # single-/few-signal WaveDrom diagram (e.g. a basic memory-transaction or
    # bus diagram from a source .rst) that has zero rx_/tx_ symbol groups; a
    # non-empty `waveforms[]` of that kind previously defeated the
    # all(_empty(...)) short-circuit, so a non-half-duplex compute/CPU IC
    # (whose L2 also has no protocol_overview → half_duplex_l2 is None, not
    # False) ran the hard rx_/tx_ demand and false-FAILed. A genuine
    # half-duplex L8 (rx_/tx_ groups, or symbol pulses in waveforms) is
    # unaffected — its waveforms carry protocol tokens so it is NOT treated
    # as empty.
    #
    # ORGANIC #655 — the SAME content-discriminator must apply to the
    # `timing_constants[]` container. doc-extraction promotes a bare scalar
    # clock-FREQUENCY constant (e.g. {name:fclk,value:1.0,unit:MHz}) into L8
    # from an L5/spec clock table. That single non-protocol entry previously
    # made `_empty('timing_constants')` False → defeated the all(_empty(...))
    # short-circuit → forced the hard half-duplex RX/TX per-symbol-group demand
    # on a non-protocol IC (delta-sigma ADC, CPU core, …) whose L2 also has no
    # protocol_overview (half_duplex_l2 is None, not False), so the gate
    # unconditionally FAILed with missing_rx_group + missing_tx_group. A scalar
    # clock-frequency entry is NOT protocol symbol-timing — there is no RX/TX
    # direction to split — so it counts as EMPTY unless it actually carries
    # directional / per-symbol protocol content (rx_/tx_/host_/dut_ tokens or
    # H0/H1/BR/IBT-style pulse widths). A genuine half-duplex L8 that stores
    # per-symbol counters/cycles in timing_constants[] carries those tokens and
    # is therefore NOT treated empty — the strict split still runs (no-leak).
    def _empty(key):
        v = waveform.get(key)
        if not v:  # None / [] / {} / 0 all count as empty
            return True
        if key == "waveforms" and not _waveforms_carry_protocol_symbols(v):
            return True  # generic WaveDrom diagram, no protocol symbols (#617)
        if key == "timing_constants" and \
                not _timing_constants_carry_protocol_symbols(v):
            return True  # scalar clock-frequency only, no protocol symbols (#655)
        return False
    # Only VACUOUS_PASS when there is genuinely NO protocol/symbol timing
    # content -- and "none" is decided by `check()`'s OWN classifier, not by a
    # second vocabulary. This escape used to keep its own token list
    # (`_PROTO_GROUP_TOKENS`) over key NAMES only, requiring non-empty values;
    # the review of next/icspm5-s2 CONFIRMED three documents it certified as a
    # structural absence while `check()` over the SAME document FAILs: the v068
    # flat `timing_parameters` {tDW0_us, tB_break_us, tIBT_us}; a
    # `timing_groups` pair `detect_thresholds` / `drive_widths` missing IBT; and
    # `timing_groups` {rx_timing: {}, tx_timing: {}}. Now the escape may fire
    # ONLY when `classified_groups` finds no RX group and no TX group AND
    # `_symbols_in` -- check()'s per-symbol reader -- finds no H0/H1/BR/IBT
    # content in the VALUE of any walked non-canonical container.
    _group_items = list(waveform.items())
    _tg = waveform.get("timing_groups")
    if isinstance(_tg, dict):
        _group_items += [(f"timing_groups.{k}", v) for k, v in _tg.items()]
    #
    # ROUND 2 (review of next/ictier1c, 8646e1862): the classifier and
    # `_symbols_in` read only group KEYS and dict KEYS, so the escape went
    # NARROWER than the token list it replaced -- scalar `H0_low_us`/`IBT_us`,
    # a `symbol_timing` list of `{"name": "H0"}`, `break_*` windows,
    # `master_side`/`slave_side`, `protocol_timing.rx_side`, and the clause's
    # own `--layer` constants all reached NOT_APPLICABLE_BY_STRUCTURE. The
    # escape now needs ALL of these to find nothing: check()'s classifier, the
    # FULL probe over every key/name/list string of the L8 AND the --layer
    # constants (`timing_content_probe`, whole-word), and the old group-name
    # words at the level they were always read (top level + timing_groups).
    _rx, _tx = classified_groups(waveform)
    _probe_hits = timing_content_probe(waveform, rtl_constants)
    _legacy_hit = any(
        v is not None and not isinstance(v, bool)
        and str(k).rsplit(".", 1)[-1] not in _PROVENANCE_FIELDS
        and (set(_identifier_tokens(k)) & _LEGACY_GROUP_WORDS
             or _names_a_symbol(k))
        for k, v in _group_items)
    _has_proto_group = bool(_rx or _tx or _probe_hits or _legacy_hit)
    _CANONICAL = ("timing_windows", "timing_constants", "waveforms")
    # AND THE ESCAPE MAY NOT OVERRIDE A DECLARATION. Its own comment says it
    # "fires when L2 says NOTHING and the gate ENUMERATES the L8 document
    # instead" -- but the condition never read L2 at all, so a design that
    # DECLARES `protocol_overview.half_duplex=true` and whose L8 happens to have
    # emitted its canonical containers empty reached this branch and was
    # published as a structural absence. A declared half-duplex protocol has two
    # sides by declaration; if its L8 carries no timing, that is a missing
    # document, which `check()` already answers with missing_rx_group /
    # missing_tx_group. Inference never outranks the design's own word.
    if (half_duplex_l2 is not True
            and (not _has_proto_group) and all(_empty(k) for k in _CANONICAL)):
        # THIS ESCAPE INFERS THE ABSENCE; IT IS NOT A DESIGN DECLARATION, and the
        # two were being reported with one word. The sibling escape above fires
        # when L2 EXPLICITLY declares `protocol_overview.half_duplex=false` --
        # there DESIGN_DECLARED_NA is exactly right and it keeps it. This one
        # fires when L2 says NOTHING and the gate ENUMERATES the L8 document
        # instead: the three canonical containers, plus every key of `waveform`
        # scanned for a directional / per-symbol token. Zero found. That is a
        # structural absence, and R-0915-124/125 require it be NAMED as one WITH
        # the enumeration behind it -- `_structural_absence.absence()` refuses a
        # claim that cannot say what it walked, which is the whole guard.
        #
        # MEASURED on run21, a signed serial-parallel multiplier: this clause was
        # one of the two step 2 reported as "PARTIALLY-VACUOUS (2 of 18 gate
        # clause(s) examined nothing)" while filing DESIGN_DECLARED_NA -- a class
        # whose own justification comment cites an L2 declaration this branch
        # never reads. The design genuinely is not a protocol IC and has no RX/TX
        # timing to split, so the ANSWER was right and only the word was wrong.
        # SCANNED COUNTS WHAT WAS WALKED, NOT WHAT COULD HAVE EXISTED. The
        # previous list named all three canonical containers whether or not the
        # document had them, so a document with none of them still published
        # "scanned 3" -- an enumeration of NAMES, which is exactly the claim
        # `_structural_absence` exists to refuse. A container that is absent was
        # not examined; only a container that is PRESENT (and then found empty
        # of protocol content) is evidence of an absence.
        _scanned_names = sorted(
            {str(k) for k in _CANONICAL if k in waveform}
            | {str(k) for k, _v in _group_items if str(k) not in _CANONICAL}
            # the --layer constants were probed too, so they are enumerated
            | ({f"layer:{k}" for k in rtl_constants}
               if isinstance(rtl_constants, dict) else set()))
        _absence = _sa.absence(
            population=("L8_TIMING_WAVEFORM container(s) and group key(s) that "
                        "could carry half-duplex protocol symbol timing"),
            scanned=len(_scanned_names),
            found=0,
            names=_scanned_names,
            detail=("check()'s classifier found no RX and no TX group, and a "
                    "whole-word probe of every key, name and list string of the "
                    "L8 and the --layer constants found no side (rx/tx/host/dut/"
                    "master/slave/external/internal) or symbol (H0/H1/BR/IBT/"
                    "break) word, so there are no two sides to split"))
        msg = _sa.sentence(_absence, "internal_vs_external_timing")
        if args.json:
            txt = json.dumps({
                "source_file": waveform_path,
                "total_findings": 0,
                "errors": 0,
                "findings": [],
                "verdict": "VACUOUS_PASS",
                # v1.15.45 established that a green vacuous record must state a
                # class the audit reads or be filed INCOMPLETE. That still holds;
                # what changes is WHICH class, because this branch establishes its
                # absence by ENUMERATION rather than by reading a declaration.
                # `attach` writes the class and its evidence where every reader
                # already looks, so the umbrella's guard (i) can VALIDATE the
                # claim instead of taking the token on trust.
                **_sa.attach({}, _absence),
                "skip_kind": "class-not-applicable",
                "rationale": msg,
            }, indent=2)
            if args.json == "-":
                print(txt)
            else:
                Path(args.json).parent.mkdir(parents=True, exist_ok=True)
                Path(args.json).write_text(txt + "\n")
        else:
            # THE TIER STAYS AT LINE START. `_stdout_signals_vacuous` believes a
            # vacuous disclosure only where `VACUOUS_PASS` BEGINS a line, and a
            # clause that invokes this gate WITHOUT `--json` has stdout as its
            # only channel. Printing the structural sentence alone put
            # `[NOT_APPLICABLE_BY_STRUCTURE]` first and silently removed the
            # disclosure, so such a clause would have read a bare PASS -- a
            # fail-open I introduced, caught by one G-arm red.
            #
            # The VERDICT TIER never changed: this branch has always been, and
            # still is, a VACUOUS_PASS (the JSON above says so). What R-0915-124/125
            # made precise is the CLASS. So stdout now carries BOTH -- the tier
            # word the flow reads, then the class and the enumeration a human
            # needs to check the claim.
            print(f"VACUOUS_PASS: {msg}")
        return 0

    findings = check(waveform, rtl_constants)
    errors = [f for f in findings if f.severity == "ERROR"]

    if args.json:
        _txt = json.dumps({
            "source_file": waveform_path,
            "total_findings": len(findings),
            "errors": len(errors),
            "findings": [asdict(f) for f in findings],
            "verdict": "PASS" if not errors else "FAIL",
        }, indent=2)
        if args.json == '-':
            print(_txt)
        else:
            from pathlib import Path as _P
            _P(args.json).parent.mkdir(parents=True, exist_ok=True)
            _P(args.json).write_text(_txt + "\n")
    else:
        for f in findings:
            print(f"[{f.severity}] {f.rule} @ {f.field}: {f.message}")
        print(f"\n{len(errors)} error(s), {len(findings)-len(errors)} warning(s)")
        print("PASS" if not errors else "FAIL")

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
