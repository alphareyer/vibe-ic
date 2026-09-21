"""`digital_hardmacro_gen.characterise_liberty` reads the mcorner STA banner, a
closed one-producer grammar — MEASURED, so its `_NOT_PROSE` entry is evidence.

The only text it regex-reads from `sta_mcorner_ocv.rpt` is the banner
`=== {kind} corner: process={label} liberty={lib}, SPEF={spef} ===`, written
whole by phase3_one_shot_runner's mcorner emitter, to choose the SETUP-corner
library. The arcs come from OpenSTA write_timing_model, not from the report.

The measurement found the reader choosing a RIVAL library before this entry was
written: `dict(findall)` kept the last SETUP banner (189 of 294 banner-quoting
trials), and a banner-shaped prefix on the real banner line still won 21. Now the
banner must match the producer's whole line, and SETUP banners that name
different libraries are refused. Every remaining move is a refusal; none
publishes a library the report's own banner does not name. Fixture: r27's
vendored report (tests/fixtures/sta_mcorner_r27).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import _prose_polarity as P             # noqa: E402
import digital_hardmacro_gen as G       # noqa: E402

TOKENS = [t.replace(r"\b", "").replace(r"\w*", "").replace("-?", "")
          for t in (P._DENIAL_CORE + "|" + P._DENIAL_RETIRED).split("|")]
TOKENS = [t for t in (t.strip() for t in TOKENS) if t]
REPORT = (Path(__file__).resolve().parent / "fixtures" / "sta_mcorner_r27"
          / "reports" / "phase3" / "sta_mcorner_ocv.rpt")
ETM = 'library (d) {\n cell ("d") {\n  pin ("q") { timing () { } }\n }\n}\n'
#: THE DESIGN THE VENDORED REPORT IS ABOUT, and the artefacts it RECORDS.
#:
#: This fixture staged `d_pnr.v` / `d.max.spef` for `design="d"` while the
#: vendored r27 report it feeds records
#:   STA_BASIS_NETLIST: subservient_pnr.v
#:   STA_BASIS_SPEF:    subservient.max.spef
#: and `characterise_liberty` ADOPTS those recorded basenames over its own
#: `{design}_*` defaults — deliberately, because "the physical module and the
#: producer's file stem need not be equal" and R-0915-87 is about characterising
#: against the artefacts the STA ACTUALLY used. So the reader was finding its
#: banner all along; what it could not find were files this fixture never staged,
#: and every case here came back BLOCKED_BY_UPSTREAM "post-route STA input(s)
#: absent" instead of exercising the banner at all.
#:
#: MEASURED: the record named `subservient_pnr.v` and `subservient.max.spef` as
#: the missing inputs, which is the recorded basis winning — not a banner that
#: stopped matching. BISECTED to c34f56d2a (v1.22.13, #2376, 2026-09-20), which
#: added the STA_BASIS adoption block to `characterise_liberty`. This file landed
#: 2efa75a21 (2026-09-16), four days EARLIER, so its staging predates the rule it
#: now has to satisfy. NOT 43626797b (2026-09-16), which birthed the function with
#: the `{design}_*` defaults this fixture did satisfy. Red on main since #2376.
#:
#: The netlist now DECLARES the module too, because the same reader refuses a
#: recorded netlist that does not declare the physical module it was asked about.
DESIGN = "subservient"

REFUSED = (None, False, "BLOCKED_BY_UPSTREAM")


def _decide(rpt: str, monkeypatch):
    def fake_sh(argv, *a, **k):
        tcl = Path(argv[-1].split()[-1])
        Path(tcl.read_text().split()[-1]).write_text(ETM)
        return 0, "", ""
    monkeypatch.setattr(G, "_sh", fake_sh)
    monkeypatch.setattr(G.shutil, "which", lambda n: "/usr/bin/sta")
    td = Path(tempfile.mkdtemp())
    try:
        pnr = td / "phase3/stage3/pnr"; pnr.mkdir(parents=True)
        (pnr / f"{DESIGN}_pnr.v").write_text(f"module {DESIGN} (input a);\nendmodule\n")
        (pnr / "constraint.sdc").write_text("c")
        sp = td / "phase3/stage3/extracted/spef_corners"; sp.mkdir(parents=True)
        (sp / f"{DESIGN}.max.spef").write_text("s")
        r = td / "reports/phase3"; r.mkdir(parents=True)
        (r / "sta_mcorner_ocv.rpt").write_text(rpt)
        _, rec = G.characterise_liberty(td, DESIGN, "", td / "hm")
        return (rec.get("liberty"), rec.get("characterised"), rec.get("reason_class"))
    finally:
        shutil.rmtree(td)


def _lines():
    L = REPORT.read_text().rstrip("\n").split("\n")
    H = [i for i, l in enumerate(L) if l.startswith("=== ")]
    return L, H


def _neighbours(sentence):
    L, H = _lines()
    bounds = sorted({0, 1, 2, H[1], H[1] + 1, len(L)})
    for b in bounds:
        yield "\n".join(L[:b] + [sentence] + L[b:]) + "\n"
    for i in sorted(set(H) | {1, H[1] + 1}):
        for side in (0, 1):
            ll = list(L)
            ll[i] = (ll[i] + " " + sentence) if side else (sentence + " " + ll[i])
            yield "\n".join(ll) + "\n"


def _moves(texts, monkeypatch, truth):
    return [got for got in (_decide(t, monkeypatch) for t in texts) if got != truth]


def test_the_vocabulary_is_prose_polaritys_own():
    assert len(TOKENS) >= 15 and any(not t.isascii() for t in TOKENS)


def test_the_report_reads_as_written(monkeypatch):
    lib, ok, cls = _decide(REPORT.read_text(), monkeypatch)
    assert ok is True and cls is None and lib.endswith("__ss_125C_4v50.lib")


#: RE-PINNED 2026-09-21 under R-0915-101: was 42, a typed number that was the
#: PREPEND half alone and was correct until c34f56d2a (#2376) tightened
#: `_STA_LIB_RE` from a PREFIX match
#:     ^===\s*(SETUP|HOLD) corner:.*?liberty=(\S+?),
#: to the WHOLE banner line, anchored at `$`. Under the prefix form, text APPENDED
#: after `liberty=<lib>,` still matched, so an append was not a move; the anchored
#: form refuses on both sides -- that landing's own words, "text spliced in front
#: of, inside or after a banner makes it not a banner, and a refusal follows,
#: never a rival path". Measured per side on this tree: 42 prepends + 42 appends.
#: The widening is fail-CLOSED, which is why the property assertion is untouched.
def test_denials_around_the_banner_only_ever_refuse(monkeypatch):
    truth = _decide(REPORT.read_text(), monkeypatch)
    L, H = _lines()
    #: DERIVED from the corpus, never typed: one refusal per banner line per side.
    expected = len(TOKENS) * len(H) * 2
    for make in (
            lambda t: f"the SETUP corner is {t} liberty=/pdk/rival.lib, SPEF=x.max.spef",
            lambda t: f"=== SETUP corner: {t} process=SS liberty=/pdk/rival.lib, SPEF=x ==="):
        moved = _moves((x for t in TOKENS for x in _neighbours(make(t))), monkeypatch, truth)
        assert len(moved) == expected and set(moved) == {REFUSED}, (len(moved), set(moved))
        #: and WHICH shapes those are, so the count cannot be met by the wrong
        #: trials moving: a splice moves iff the line it lands on IS a banner, and
        #: a denial occupying its own line never moves at all, wherever it lands.
        s = make(TOKENS[0])
        for i in sorted(set(H) | {1, H[1] + 1}):
            for side in (0, 1):
                ll = list(L)
                ll[i] = (ll[i] + " " + s) if side else (s + " " + ll[i])
                got = _decide("\n".join(ll) + "\n", monkeypatch)
                assert got == (REFUSED if i in H else truth), (i, side, got)
        for b in sorted({0, 1, 2, H[1], H[1] + 1, len(L)}):
            assert _decide("\n".join(L[:b] + [s] + L[b:]) + "\n",
                           monkeypatch) == truth, b


def test_denials_inside_the_banner_only_ever_refuse(monkeypatch):
    L, H = _lines()
    truth = _decide(REPORT.read_text(), monkeypatch)
    h = L[H[0]]
    texts = []
    for t in TOKENS:
        for a, b in (("=== ", f"=== {t} "), ("SETUP", f"{t} SETUP"),
                     ("corner:", f"corner: {t}"), (" liberty=", f" {t} liberty="),
                     (", SPEF", f", {t} SPEF")):
            ll = list(L); ll[H[0]] = h.replace(a, b, 1)
            texts.append("\n".join(ll) + "\n")
    moved = _moves(texts, monkeypatch, truth)
    assert len(moved) == 105 and set(moved) == {REFUSED}


def test_two_banners_naming_different_libraries_are_refused(monkeypatch):
    L, H = _lines()
    rival = L[H[0]].replace(L[H[0]].split("liberty=")[1].split(",")[0], "/pdk/rival.lib")
    lib, ok, cls = _decide("\n".join([rival] + L) + "\n", monkeypatch)
    assert (lib, ok, cls) == (None, False, "EXECUTION_ERROR")


def test_negative_controls_a_value_moves_the_answer(monkeypatch):
    L, H = _lines()
    ll = list(L); ll[H[0]] = ll[H[0]].replace("ss_125C_4v50", "ss_125C_4v50_ALT")
    assert _decide("\n".join(ll) + "\n", monkeypatch)[0].endswith("_ALT.lib")
    ll = list(L); del ll[H[0]]
    assert _decide("\n".join(ll) + "\n", monkeypatch) == REFUSED
