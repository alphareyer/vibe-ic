"""R-0915-164 (b) — the L2's own `duplex` text can VETO half_duplex=true.

Phase 1 sets `protocol_overview.half_duplex=True` from a document-level
keyword hit: single-wire evidence plus "half-duplex" ANYWHERE in the same
document, with no negation or contrast handling. On pcie_gen5 and ufs that hit
came from sentences attributing half-duplex to the interface each REPLACED
("…limitations of the latter, including half-duplex operation"; "…than the …
halfduplex interface of eMMCs"), while the same L2's synth-written `duplex`
text says "dual-simplex" / "full-duplex". The duplex text may veto, never
grant: measured over 98 real L2s, exactly 3 move (pcie_gen5, ufs and the
plugin fixture copy of pcie_gen5); spm has no protocol_overview.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import l2_half_duplex_reconcile as R                        # noqa: E402

#: The three real duplex texts that must veto (verbatim prefixes from the corpus).
PCIE = ("dual-simplex (independent TX + RX differential pairs per Lane; both "
        "directions transmit simultaneously and continuously)")
UFS = "full-duplex (independent M-PHY TX and RX differential lane pairs per direction)"
#: Real texts that must NOT veto.
SENT = "Half-duplex / unidirectional: the sensor transmits, the ECU receives."
HDLC = ("Half-duplex (NRM, ARM) or full-duplex (ABM); both modes carried over a "
        "single serial link")
RS485 = ("Half-duplex on a 2-wire (1 differential-pair) bus; full-duplex on a "
         "4-wire (2 differential-pairs) bus.")


#: The designs' OWN document sentences (verbatim from the corpus inputs).
PCIE_DOCS = {"pcie_gen5_spec.txt": (
    "In contrast, a PCI Express bus link supports full-duplex communication "
    "between any two endpoints, with no inherent limitation on concurrent "
    "access across multiple endpoints. The bonded serial bus architecture was "
    "chosen over the traditional parallel bus because of the inherent "
    "limitations of the latter, including half-duplex operation.")}
UFS_DOCS = {"ufs_spec.txt": (
    "UFS implements a full-duplex serial LVDS interface that scales better to "
    "higher bandwidths than the 8-lane parallel and halfduplex interface of "
    "eMMCs.")}
#: The K-line shape: the UART synth's template says full duplex, the design's
#: own document says single-wire half-duplex.
UART_TEMPLATE = "full duplex (independent TX SOUT and RX SIN lines)"
KLINE_DOCS = {"kline_spec.txt": (
    "The K-line is a single-wire, bidirectional half-duplex bus: the tester and "
    "the ECU take turns driving the same line with UART-framed bytes.")}


def _l2(half, duplex=None):
    po = {"half_duplex": half, "evidence": "half-duplex/single-wire/1-wire keyword match"}
    if duplex is not None:
        po["duplex"] = duplex
    return {"doc_id": "L2", "protocol_overview": po}


def test_the_designs_own_documents_state_it_so_the_veto_applies():
    """R-0915-167: pcie_gen5 and ufs -- template AND their own documents state
    full-duplex / dual-simplex -> half_duplex=true is vetoed, and the evidence
    names the document that states it."""
    for text, docs, fname in ((PCIE, PCIE_DOCS, "pcie_gen5_spec.txt"),
                              (UFS, UFS_DOCS, "ufs_spec.txt")):
        doc = _l2(True, text)
        change = R.reconcile(doc, docs)
        assert change is not None, text
        po = doc["protocol_overview"]
        assert po["half_duplex"] is False
        assert fname in po["evidence"], po["evidence"]
        assert po["half_duplex_keyword_evidence"].startswith("half-duplex")


def test_the_synth_template_alone_never_vetoes():
    """R-0915-167: the duplex text is synth boilerplate (115 of 116 sites write
    a fixed literal). With no document statement -- the plugin fixture ships no
    inputs -- nothing changes; step 2 then reads INCOMPLETE, never FAIL."""
    for docs in ({}, None, {"notes.txt": "A register map with no link layer."}):
        doc = _l2(True, PCIE)
        assert R.reconcile(doc, docs) is None, docs
        assert doc["protocol_overview"]["half_duplex"] is True


def test_a_kline_document_under_a_uart_template_is_not_vetoed():
    """The review's hazard: UART synth template 'full duplex' over a document
    that describes a single-wire half-duplex K-line."""
    doc = _l2(True, UART_TEMPLATE)
    assert R.reconcile(doc, KLINE_DOCS) is None
    assert doc["protocol_overview"]["half_duplex"] is True


def test_the_text_never_grants_and_ambiguity_never_vetoes():
    for half, text in ((True, SENT), (True, HDLC), (True, RS485),
                       (True, None), (False, PCIE), (False, "half-duplex on DQ"),
                       (None, UFS)):
        doc = _l2(half, text)
        assert R.reconcile(doc, PCIE_DOCS) is None, (half, text)
        assert doc["protocol_overview"]["half_duplex"] is half


def test_an_l2_without_a_protocol_overview_is_untouched():
    """spm's shape: no protocol_overview at all."""
    doc = {"doc_id": "L2", "protocol_overview": None}
    assert R.reconcile(doc, PCIE_DOCS) is None
    assert R.reconcile({"doc_id": "L2"}, PCIE_DOCS) is None


def test_reconcile_file_rewrites_only_a_vetoed_l2(tmp_path):
    p = tmp_path / "L2_FRS.json"
    p.write_text(json.dumps(_l2(True, PCIE)))
    assert R.reconcile_file(p, PCIE_DOCS) is True
    assert json.loads(p.read_text())["protocol_overview"]["half_duplex"] is False
    q = tmp_path / "L2b.json"
    q.write_text(json.dumps(_l2(True, SENT)))
    before = q.read_text()
    assert R.reconcile_file(q, PCIE_DOCS) is False
    assert q.read_text() == before


def test_phase1_runs_the_veto_after_the_protocol_synths():
    """The `duplex` text is written by the protocol synths, so the veto must run
    after the synth chain and before anything downstream reads L2."""
    src = (PROGRAMS / "phase1_doc_one_shot_runner.py").read_text()
    i = src.index("from avalon_protocol_synth import")
    j = src.index("[14e3/15] Universal packet/PDU-protocol")
    call = src.find("l2_half_duplex_reconcile")
    assert i < call < j, (i, call, j)
    # R-0915-167: it is handed the design's own documents (`extracted`, what
    # gen_l2_frs reads), never only the L2
    assert "reconcile_file(_l2p_hdx, extracted)" in src[call:j]
