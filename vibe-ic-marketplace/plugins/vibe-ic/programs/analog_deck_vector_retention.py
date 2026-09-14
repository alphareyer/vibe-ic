#!/usr/bin/env python3
"""analog_deck_vector_retention.py — retain the vectors a deck MEASURES, and
only those.

WHY THIS MODULE EXISTS (MEASURED, lane icadc, 2026-09-15)
=========================================================
`analog_a3_netlist_emit` renders its validation testbench with no `.save`, so
ngspice retains the full time vector of EVERY internal node for the whole
transient.  The deck it renders for a clocked converter runs the block's whole
conversion record, so that retention grows with the record.

A/B on ONE deck, two arms differing by exactly the `.save` line, same image,
same `--cpus 1`, same `--memory 24g`, launched together:

    t_sim 18.05 us   with `.save`     RSS   42.8 MiB
    t_sim 20.03 us   without          RSS  106.7 MiB

    growth   with `.save`    542 kB per us of simulated time
             without        3784 kB per us of simulated time

Extrapolated to the 13824 us record that deck runs: **~7.5 GB against ~52 GB**.
The `.save` arm's extrapolation was checked against an independent run of the
same circuit that has been going for two days — measured 7.22 GiB at 11.8 ms,
predicted ~7.5 GB, good to 4 %.  A 52 GB validation sim is how a shared host
freezes, and one did on 2026-09-14.

WHAT IT RETAINS
===============
Exactly the vectors the deck's OWN `meas`, `wrdata` and `print` cards read —
`v(...)` and `i(...)`, derived from the deck text, never from a list here.  A
node nothing asks for is dropped; a node something asks for is kept.  The
measurement the deck takes is therefore bit-for-bit the measurement it took
before, which is the whole point: this is a memory bound, not a scope change.

IT REFUSES BY NAME RATHER THAN EMIT A `.save` THAT BREAKS THE DECK
==================================================================
    no_transient          an `op`-only deck holds no time vectors to bound
    already_retained      the deck declares a `.save` of its own
    no_measured_vector    nothing in the deck reads a node, so a `.save` would
                          retain NOTHING and every later reference would fail
                          with `no such vector` on a run that still exits 0
In each the deck is returned BYTE FOR BYTE.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

PROGRAM = "analog_deck_vector_retention"

#: `v(node)` and `i(source)` as the deck's own cards spell them. Both forms are
#: collected: a deck that measures a branch current and is given a `.save` of
#: node voltages alone would answer `no such vector` for it, on a run that
#: still exits 0 — the same silent-empty-measurement failure this bounds.
_VECTOR = re.compile(r"(?i)\b([vi])\(\s*([^)]+?)\s*\)")
_SAVE = re.compile(r"(?im)^\s*\.save\b")
_CONTROL = re.compile(r"(?im)^\s*\.control\s*$")
_TRAN = re.compile(r"(?im)^\s*tran\s+\S+\s+\S+")
#: A `.save` is a TOP-LEVEL card. Inside `.control` it is the interactive
#: `save` command instead, which applies to the next analysis only — the same
#: placement rule `_TB_INTEGRATION_OPTIONS` states for `.options`, and wrong in
#: the same direction.
_SAVE_ANCHOR = _CONTROL


def measured_vectors(deck_text: str) -> List[str]:
    """Every vector the deck READS, in first-appearance order.

    Read off the cards, not off a table: a block whose emitter gains a
    measurement gains its retention in the same breath."""
    seen: List[str] = []
    # COMMENTS ARE NOT CARDS. A deck's `* condition:` prose describes the
    # measurement in the same vocabulary the cards use, and a `v(...)` written
    # there names nothing ngspice can retain — a `.save` that carried it would
    # be refused as an unknown vector and the whole retention lost. The `.save`
    # cards themselves are skipped for the opposite reason: this function
    # DERIVES the retention, it must never read back its own answer.
    body = "\n".join(ln for ln in deck_text.splitlines()
                     if not _SAVE.match(ln) and not ln.lstrip().startswith("*"))
    for kind, name in _VECTOR.findall(body):
        tok = f"{kind.lower()}({name})"
        if tok not in seen:
            seen.append(tok)
    return seen


def plan(deck_text: str) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"producer": PROGRAM, "applied": False}
    if _SAVE.search(deck_text):
        rec["refused"] = "already_retained"
        return rec
    if not _TRAN.search(deck_text):
        rec["refused"] = "no_transient"
        return rec
    vectors = measured_vectors(deck_text)
    if not vectors:
        # A `.save` with nothing in it retains NOTHING. Emitting one here
        # would turn a deck that measures nothing into a deck that cannot
        # measure anything, and ngspice reports that as `no such vector` on a
        # run that still exits 0.
        rec["refused"] = "no_measured_vector"
        return rec
    rec.update(applied=True, vectors=vectors, vector_count=len(vectors))
    return rec


def apply(deck_text: str) -> Tuple[str, Dict[str, Any]]:
    rec = plan(deck_text)
    if not rec.get("applied"):
        return deck_text, rec
    card = ".save " + " ".join(rec["vectors"])
    m = _SAVE_ANCHOR.search(deck_text)
    if m is None:                                   # no `.control`: append
        out = deck_text.rstrip("\n") + "\n" + card + "\n"
    else:
        out = deck_text[:m.start()] + card + "\n" + deck_text[m.start():]
    rec["provenance"] = (
        f"vector_retention={len(rec['vectors'])} vector(s) retained — exactly "
        f"the `v()`/`i()` this deck's own meas/wrdata/print cards read, so the "
        f"measurement is unchanged and the memory the transient holds stops "
        f"growing with every node the circuit happens to have "
        f"(see `{PROGRAM}`)")
    out = (out[:m.start()] + f"* condition: {rec['provenance']}\n"
           + out[m.start():]) if m is not None else out
    return out, rec
