#!/usr/bin/env python3
"""ic_expert_db_query.py — GENERAL-CORE retrieval over the IC Expert DB.

The IC Expert DB (`agents/ic_expert_db/ic_expert_db.json`) is a structured,
chip-AGNOSTIC design-class knowledge base (algorithm correctness / interface
convention / latency-reset discipline / common traps), distilled from
proven-correct designs. This module surfaces the RELEVANT lessons for a design
under authoring — consumed by the GENERAL Vibe-IC path (design_one_shot_runner's
spec-to-rtl / ic-expert authoring step), NOT a benchmark-only path. Any Phase-1
design doc or user prompt gets the same knowledge.

ADVISORY layer: lessons are design-craft ADVICE for the AI author; they never
override a deterministic gate/program verdict (enforced by
ic_expert_db_consistency_check.py).

Retrieval = structured ic_class match + lexical keyword/function-noun overlap
(NO vectors — IC knowledge is structured, so lexical+class match is precise).

CLASS-FIRST (#2094). When the caller passes the design's REGISTERED `ic_class`
(the one in `ic_class_registry.json`, persisted at `reports/ic_class.json`) AND
the DB declares a profile for it under `registered_class_profiles`, that profile
SELECTS the candidate entries and the lexical score only RANKS within them.
Lexical retrieval may then ADD an in-class entry; it can never substitute an
out-of-class one. The defect this closes was measured, not imagined: on a
crypto-accelerator input the sentence "the multipliers are implemented in a
serial-parallel fashion" (describing two internal field multipliers) scored the
`serial-parallel-multiplier` entry — craft about authoring a golden for a
serial-parallel multiplier DESIGN — into a crypto pack, where it has no
referent. A literal phrase collision is not a design-family match.

A registered class the DB maps to `null` is NOT YET PROFILED: retrieval falls
back to the unconfined lexical ranking, byte-identical to passing no ic_class at
all. That gap is DECLARED in the DB (every registered name appears there), never
silent.

Usage:
    ic_expert_db_query.py --prompt <file|-> [--k 5] [--db <path>]
                          [--ic-class <registered ic_class>]
    # prints the matched lessons (ready to inject into the author's context)
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

_DEFAULT_DB = (Path(__file__).resolve().parent.parent
               / "agents" / "ic_expert_db" / "ic_expert_db.json")

# design-family function-noun stems — the strongest "same kind of design" signal
_FN_STEMS = ["divid","divis","multipl","booth","adder","subtract","alu","mac","accumulat",
    "aes","sha","hmac","cipher","crc","galois","scrambl","lfsr","prbs","hamming","ecc",
    "axi","apb","ahb","wishbone","spi","uart","i2c","fifo","stream","serdes","serial",
    "cache","sdram","ddr","tlb","lru","register","ram","buffer","stack","lifo","skid",
    "fir","iir","filter","fft","sigma","delta","decimat","interpolat","cordic",
    "arbiter","interrupt","sequencer","microcode","vending","elevator","stopwatch",
    "counter","timer","sort","merge","priority","encoder","decoder","mux","shift",
    "gcd","fibonacci","booth","sprite","rotate","grayscale","border","line","vga",
    "clock","glitch","cdc","synchron","edge","perceptron","neuro","matrix","transform"]

def _fn(text: str) -> set:
    t = text.lower()
    return {s for s in _FN_STEMS if s in t}

def _kw(text: str) -> set:
    toks = re.findall(r"[A-Za-z_]{4,}", text.lower())
    stop = {"module","input","output","the","and","for","with","that","this","should",
            "must","when","value","signal","width","bit","bits","data","design",
            "implement","following","using","based","output","inputs","outputs"}
    return {w for w in toks if w not in stop}

def registered_class_profile(ic_class, db_path=None):
    """The DB's declared profile for a REGISTERED `ic_class`, or None.

    Three distinguishable answers, and they must stay distinguishable:
      * a dict          — the class is PROFILED; `db_classes` selects, and
                          `integration_contract` is what its packs carry;
      * None with the name present in `registered_class_profiles` — the class is
        registered and NOT YET PROFILED (a declared gap);
      * None with the name absent — the caller passed something that is not a
        registered class name at all.
    Callers that must tell the last two apart use `is_registered_class`.
    """
    if not ic_class:
        return None
    # Resolved at CALL time, never bound as a default: a default argument
    # freezes the path at import, so a caller that repoints the DB (a test, a
    # maintenance query against a candidate DB) is silently answered from the
    # shipped one.
    try:
        db = json.loads(Path(db_path or _DEFAULT_DB).read_text())
    except (OSError, ValueError):
        return None
    prof = db.get("registered_class_profiles")
    if not isinstance(prof, dict):
        return None
    p = prof.get(ic_class)
    return p if isinstance(p, dict) else None


def is_registered_class(ic_class, db_path=None) -> bool:
    """True when `ic_class` is named in the DB's `registered_class_profiles`
    (profiled or explicitly not-yet-profiled). "Not in the map" and "in the map
    as null" are different facts and a `.get()` that returns None for both is
    exactly how the second one goes silent."""
    if not ic_class:
        return False
    try:
        db = json.loads(Path(db_path or _DEFAULT_DB).read_text())
    except (OSError, ValueError):
        return False
    prof = db.get("registered_class_profiles")
    return isinstance(prof, dict) and ic_class in prof


def query(prompt: str, k: int = 5, db_path=None,
          expand_related: bool = False, ic_class=None):
    """Retrieve the top-k relevant lessons for `prompt`.

    `ic_class` is the design's REGISTERED class (default None → byte-identical
    to the tuned lexical ranking, which is what every pre-#2094 caller gets).
    When it names a class the DB PROFILES, retrieval is CLASS-FIRST: the
    profile's `db_classes` are the whole candidate set and the lexical score
    only orders them. A profiled class therefore never receives an out-of-class
    entry, however well a phrase happens to collide, and an in-class entry the
    prompt never mentions is still offered (score 0.0) — the class is the
    selection, the phrase is only the ranking.

    `expand_related` is OPT-IN (default False → byte-identical to the tuned
    lexical ranking). When True, after the ranked top-k is chosen, one lesson
    from each ic_class in the TOP hit's `related[]` graph is appended (score 0.0,
    tagged `related_to`) — a synthesis view that follows the concept links. It is
    off by default because an A/B on 94 designs showed that widening the author's
    context LOWERED recovery, so the production spec-to-rtl path keeps the tight
    top-k; the graph expansion is for maintenance / explicit synthesis queries.
    """
    # Resolved at CALL time, never bound as a default (see
    # `registered_class_profile`). A default argument freezes the path at
    # import, so a caller that repoints the module's DB is answered from the
    # shipped one — silently, and with a result that looks perfectly normal.
    db_path = db_path or _DEFAULT_DB
    db = json.loads(Path(db_path).read_text())
    entries = db.get("entries", [])
    profile = registered_class_profile(ic_class, db_path)
    if profile:
        allowed = [c for c in (profile.get("db_classes") or []) if isinstance(c, str)]
        if allowed:
            # CLASS-FIRST: the registered class SELECTS; the phrase only ranks.
            present = {e.get("ic_class") for e in entries}
            missing = [c for c in allowed if c not in present]
            if missing:
                # Degrade LOUDLY. A profile naming an entry the DB does not
                # carry would silently shrink the candidate set back towards
                # the unconfined behaviour this exists to stop.
                raise KeyError(
                    f"registered_class_profiles[{ic_class!r}].db_classes names "
                    f"{missing}, which entries[] does not carry")
            entries = [e for e in entries if e.get("ic_class") in allowed]
    q_fn, q_kw = _fn(prompt), _kw(prompt)
    ranked = []
    for e in entries:
        cls = e.get("ic_class", "")
        c_fn = _fn(cls)
        for les in e.get("lessons", []):
            l_fn = _fn(cls + " " + les)
            fn_ov = len(q_fn & l_fn)
            kw_ov = len(q_kw & _kw(les))
            score = 12.0 * len(q_fn & c_fn) + 4.0 * fn_ov + 0.5 * kw_ov
            # Outside class-first, a zero score means "the phrase never reached
            # this entry" and dropping it is the tuned behaviour. INSIDE
            # class-first the entry was selected by the CLASS, so a zero score
            # means only "the prompt did not happen to use these words" — that
            # is a ranking fact, not a membership one, and dropping it would let
            # the phrase decide membership again through the back door.
            if score > 0 or profile:
                ranked.append((score, cls, les))
    ranked.sort(key=lambda x: -x[0])
    # dedup identical lessons, keep top-k
    out, seen = [], set()
    for s, cls, les in ranked:
        if les in seen:
            continue
        seen.add(les)
        out.append({"ic_class": cls, "score": round(s, 2), "lesson": les})
        if len(out) >= k:
            break
    if expand_related and out:
        by_class = {e.get("ic_class"): e for e in entries}
        seed = out[0]["ic_class"]
        # `.get("related") or []` (not `.get("related", [])`) — an explicit
        # related:null (which the gate treats as "absent") must not crash expand.
        for r in (by_class.get(seed, {}).get("related") or []):
            rentry = by_class.get(r)
            if not rentry or not rentry.get("lessons"):
                continue
            les = rentry["lessons"][0]
            if les in seen:
                continue
            seen.add(les)
            out.append({"ic_class": r, "score": 0.0, "lesson": les, "related_to": seed})
            if len(out) >= 2 * k:
                break
    return out

def render(hits) -> str:
    if not hits:
        return ""
    lines = ["## IC Expert DB — relevant design-class knowledge (advisory)",
             "Proven design-craft for similar designs. Apply the algorithm / "
             "interface / latency insight; the deterministic gates still decide PASS.\n"]
    for h in hits:
        lines.append(f"- **[{h['ic_class']}]** {h['lesson']}")
    return "\n".join(lines) + "\n"

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt", required=True, help="prompt file, or '-' for stdin")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--db", type=Path, default=_DEFAULT_DB)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of rendered digest")
    ap.add_argument("--expand-related", action="store_true",
                    help="follow the top hit's related[] concept links (synthesis view; "
                         "OFF by default — the production path keeps the tight top-k)")
    ap.add_argument("--ic-class", default=None,
                    help="the design's REGISTERED ic_class; when the DB profiles it, "
                         "retrieval is CLASS-FIRST (the class selects, the phrase ranks)")
    a = ap.parse_args(argv)
    text = sys.stdin.read() if a.prompt == "-" else Path(a.prompt).read_text(errors="replace")
    hits = query(text, a.k, a.db, expand_related=a.expand_related, ic_class=a.ic_class)
    print(json.dumps(hits, indent=2, ensure_ascii=False) if a.json else render(hits))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
