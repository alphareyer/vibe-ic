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
        (pnr / "d_pnr.v").write_text("m"); (pnr / "constraint.sdc").write_text("c")
        sp = td / "phase3/stage3/extracted/spef_corners"; sp.mkdir(parents=True)
        (sp / "d.max.spef").write_text("s")
        r = td / "reports/phase3"; r.mkdir(parents=True)
        (r / "sta_mcorner_ocv.rpt").write_text(rpt)
        _, rec = G.characterise_liberty(td, "d", "", td / "hm")
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


def test_denials_around_the_banner_only_ever_refuse(monkeypatch):
    truth = _decide(REPORT.read_text(), monkeypatch)
    for make, expected in (
            (lambda t: f"the SETUP corner is {t} liberty=/pdk/rival.lib, SPEF=x.max.spef", 42),
            (lambda t: f"=== SETUP corner: {t} process=SS liberty=/pdk/rival.lib, SPEF=x ===", 42)):
        moved = _moves((x for t in TOKENS for x in _neighbours(make(t))), monkeypatch, truth)
        assert len(moved) == expected and set(moved) == {REFUSED}, (len(moved), set(moved))


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
