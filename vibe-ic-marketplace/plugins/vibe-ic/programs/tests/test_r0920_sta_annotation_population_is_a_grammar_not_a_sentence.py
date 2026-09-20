"""`sta_annotation_population.classify` reads GRAMMAR, not prose — measured.

It is a `_NOT_PROSE` entry in `prose_polarity_consulted_check`, and that claim
has to be MEASURED rather than asserted: an exemption nobody drove is a waiver.
Its two inputs are both machine-written and both closed:

  * OpenSTA's own `report_annotated_delay` banner — `Found <n> unannotated
    drivers.` / `Found <n> partially unannotated drivers.`, anchored at line
    start and line end, with the driver inventory between them COUNTED against
    the banner's own number; plus this flow's `STA_LINK_INSTANCE` /
    `STA_UNCONNECTED_OUTPUT` records, one producer each.
  * a DEF `NETS` / `SPECIALNETS` section, whose statement count is likewise
    checked against the section header's own number.

Neither has a negation form: there is no way to write "this driver is NOT
unannotated" in either. A driver that is annotated is simply ABSENT from the
inventory, and absence is already how this function answers.

So this file drives every token of `_prose_polarity`'s vocabulary — both tiers,
the CJK spellings included — into every position a sentence can physically
occupy around each grammar, and measures that no published answer moves. The
negative controls at the end move the answer deliberately, so a zero here is a
fact about the grammar and not about a fixture that could not move anything.
"""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sta_annotation_population as S  # noqa: E402
import _prose_polarity as pol  # noqa: E402

_BODY = ("Found 1 unannotated drivers.\n"
         "u_pg/Y\n"
         "Found 0 partially unannotated drivers.\n"
         "STA_LINK_INSTANCE u_pg cellA\n")

_DEF = ("VERSION 5.8 ;\n"
        "DESIGN top ;\n"
        "NETS 1 ;\n"
        "- VSS ( u_pg Y ) + USE GROUND ;\n"
        "END NETS\n"
        "END DESIGN\n")

#: The whole vocabulary, taken from the module rather than restated — a private
#: copy is the divergence `_prose_polarity` exists to end.
_TOKENS = ("not", "no", "none", "without", "excluding", "never", "non",
           "removed", "obsolete", "superseded", "n/a", "inapplicable",
           "deprecated", "no longer", "does not apply",
           "非", "无", "無", "不", "否")

#: A denial that also carries a RIVAL declaration, so a reader that absorbed it
#: would publish something different rather than merely nothing.
def _denial(token: str) -> str:
    return (f"This inventory is {token} applicable: "
            f"Found 0 unannotated drivers.")


@pytest.fixture()
def defpath(tmp_path):
    p = tmp_path / "top.def"
    p.write_text(_DEF)
    return str(p)


def _answer(body, defpath):
    got = S.classify(body, defpath)
    return (got["complete"], got["reason"],
            tuple((d["driver"], d["classification"]) for d in got["drivers"]))


def test_every_token_is_a_denial_in_the_shared_vocabulary():
    """The fixture is driving real denials, not arbitrary words."""
    for token in _TOKENS:
        assert pol.is_denied(_denial(token)), token


def test_no_denial_around_the_sta_banner_moves_the_answer(defpath):
    base = _answer(_BODY, defpath)
    assert base[0] is True and base[2] == (("u_pg/Y",
                                            "EXPLICIT_PG_NOT_SIGNAL_PARASITICS"),)
    for token in _TOKENS:
        line = _denial(token)
        for body in (line + "\n" + _BODY,                    # above
                     _BODY + line + "\n",                    # below
                     line + "\n" + _BODY + line + "\n"):     # both
            assert _answer(body, defpath) == base, (token, body)


def test_no_denial_around_the_def_net_statement_moves_the_answer(tmp_path):
    p = tmp_path / "d.def"
    p.write_text(_DEF)
    base = _answer(_BODY, str(p))
    for token in _TOKENS:
        comment = f"# this net is {token} a ground net\n"
        for text in (comment + _DEF, _DEF + comment,
                     _DEF.replace("NETS 1 ;\n", "NETS 1 ;\n" + comment)):
            q = tmp_path / "v.def"
            q.write_text(text)
            got = S.classify(_BODY, str(q))
            # The DEF is hashed, so the sha MUST move; the VERDICT must not.
            assert (got["complete"], got["reason"],
                    tuple((d["driver"], d["classification"])
                          for d in got["drivers"])) == base, (token, text)
            assert got["def_sha256"] != hashlib.sha256(
                _DEF.encode()).hexdigest()


def test_a_token_spliced_inside_the_banner_never_publishes_a_verdict(defpath):
    """Inside the grammar, a denial can only DESTROY the match, never win it.

    The banner is anchored `^Found (\\d+) unannotated drivers\\.$`, so a token
    spliced into it stops the line being the banner — and the function refuses
    by name instead of reading the rival number.
    """
    for token in _TOKENS:
        body = _BODY.replace("Found 1 unannotated drivers.",
                             f"Found {token} 1 unannotated drivers.")
        got = S.classify(body, defpath)
        assert got["complete"] is False
        assert got["reason"] == "missing native annotation counts", token
        assert got["drivers"] == []


# ── NEGATIVE CONTROLS: the fixture CAN move the answer ──────────────────────

def test_changing_the_def_use_moves_the_classification(tmp_path):
    p = tmp_path / "s.def"
    p.write_text(_DEF.replace("+ USE GROUND", "+ USE SIGNAL"))
    got = S.classify(_BODY, str(p))
    assert got["complete"] is False
    assert got["drivers"][0]["classification"] == "REQUIRED_OR_UNKNOWN"


def test_changing_the_banner_count_moves_the_answer(defpath):
    got = S.classify(_BODY.replace("Found 1 unannotated",
                                   "Found 2 unannotated"), defpath)
    assert got["reason"] == "inconsistent driver inventory or partial annotation"


def test_an_absent_def_is_refused_by_name(tmp_path):
    got = S.classify(_BODY, str(tmp_path / "missing.def"))
    assert got["reason"] == "DEF authority absent"
