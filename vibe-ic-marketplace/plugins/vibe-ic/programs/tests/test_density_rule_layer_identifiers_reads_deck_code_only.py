"""The deck-density extractors read deck CODE, never the sentences a rule block
carries — and the `_NOT_PROSE` exemption that states so is measured, not argued.

WHAT THE RATCHET FOUND. `metal_fill_config_gen::density_rule_layer_identifiers`
shipped reading the RAW rule block: a `re.findall` for `<layer>.area` over text
that also contains the `# Rule <id>:` header, the `##` notes beside it, and the
human-readable violation message the rule prints. That is the #706/#711 shape —
a value taken out of a sentence and written as a declaration, with nothing
asking whether the sentence DENIES it — and `prose_polarity_consulted_check
--ratchet` refused the landing by name.

THE FIX REMOVES THE REFERENT rather than arguing about it: every input now goes
through `die_level_deck_rule_attribution.deck_code_only`, which blanks every
quoted string and every `#` comment in ONE left-to-right scan that tracks both
states together, keeping offsets so the deck's own line structure — which the
section-banner block boundary is computed from — is unchanged. After it, no
sentence reaches any regex, so the polarity question has no referent, and the
claim is a `_NOT_PROSE` entry rather than a debt-register entry.

BOTH DIRECTIONS, ON THE SAME 84 TRIALS:
  * all 21 tokens of `_prose_polarity`'s own denial vocabulary — both tiers,
    the five CJK spellings included — each carrying a DECLARATION-SHAPED
    payload, in each of the 4 positions a sentence can physically occupy here,
    move 0 of 84 published answers;
  * NEGATIVE CONTROL: the identical payload spliced in as CODE moves it, so the
    fixture could have moved;
  * MUTATION: with `deck_code_only` replaced by the identity — the strip
    deleted — the same 84 trials move 84 of 84.
And the EXEMPTION's own control: the function is still what the scanner flags,
and with the entry removed the ratchet refuses again.

chip/PDK-AGNOSTIC: every deck below is written by this file.
"""
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import _prose_polarity as P  # noqa: E402
import die_level_deck_rule_attribution as D  # noqa: E402
import metal_fill_config_gen as G  # noqa: E402
import prose_polarity_consulted_check as C  # noqa: E402

EXEMPT_NAME = "metal_fill_config_gen::density_rule_layer_identifiers"

#: Every token `_prose_polarity` calls a denial, spelled out so the sweep below
#: exercises the vocabulary itself rather than a sample of it. Asserted against
#: that module in `test_the_vocabulary_is_the_modules_own`, so a token added
#: there and not here is a test failure rather than a quiet gap in the sweep.
TOKENS = ["not", "no", "none", "without", "excluding", "excluded", "never",
          "non", "非", "无", "無", "不", "否", "removed", "obsolete",
          "superseded", "n/a", "inapplicable", "deprecated", "no longer",
          "does not apply"]

#: The four places a SENTENCE can physically sit in this production.
SLOTS = {"H": " — {s}",      # the `# Rule <id>:` header itself
         "N": " {s}",        # a `##` note line under it
         "T": "  # {s}",     # a trailing comment on the code line
         "S": " {s}"}        # inside the violation MESSAGE string

PAYLOAD = "plant_layer.area / chip_area"

BASE = D.FILE_SEP + """/deck/density.rb
chip_area = extent.sized(0.0).area

# Rule COV1.a: layer_one coverage over the entire die{H}
##{N}
if (layer_one.area / chip_area) * 100 < 30{T}
  extent.output('COV1.a', 'COV1.a : layer_one coverage{S}')
end
"""


def _deck(**kw):
    f = {"H": "", "N": "", "T": "", "S": ""}
    f.update(kw)
    return BASE.format(**f)


def _answer(**kw):
    return G.density_rule_layer_identifiers(_deck(**kw))


def test_the_vocabulary_is_the_modules_own():
    for t in TOKENS:
        assert P.NEGATION_RE.search(t), f"{t!r} is not a denial to _prose_polarity"
    assert len(TOKENS) == 21


def test_no_sentence_moves_the_published_answer():
    base = _answer()
    assert base == {"COV1.a": ["layer_one"]}
    moved = []
    for tok in TOKENS:
        for pos, tmpl in SLOTS.items():
            got = _answer(**{pos: tmpl.format(s=f"{tok} {PAYLOAD}")})
            if got != base:
                moved.append((tok, pos, got))
    assert moved == [], f"{len(moved)} of {len(TOKENS)*len(SLOTS)} prose trials moved"


def test_the_negative_control_moves_it():
    """The same payload as CODE. Without this the zero above could be a fixture
    that was never able to move."""
    base = _answer()
    txt = _deck().replace("if (layer_one.area",
                          f"if ({PAYLOAD}) * 100 < 1\n  end\n  if (layer_one.area")
    got = G.density_rule_layer_identifiers(txt)
    assert got != base
    assert got == {"COV1.a": ["layer_one", "plant_layer"]}


def test_the_strip_is_load_bearing(monkeypatch):
    """MUTATION: delete the strip and every one of the same trials moves."""
    monkeypatch.setattr(D, "deck_code_only", lambda t: t)
    base = _answer()
    moved = sum(1 for tok in TOKENS for pos, tmpl in SLOTS.items()
                if _answer(**{pos: tmpl.format(s=f"{tok} {PAYLOAD}")}) != base)
    assert moved == len(TOKENS) * len(SLOTS), (
        "the mutation must move EVERY trial the fixed code leaves alone; "
        f"it moved {moved}")


# ---------------------------------------------------------------------------
# `deck_code_only` itself, including the shape that was wrong first.
# ---------------------------------------------------------------------------

def test_an_apostrophe_in_a_comment_does_not_swallow_the_code_below():
    """The measured defect in the FIRST shape. Blanking string literals with a
    regex and then cutting at the first `#` lets an English apostrophe open a
    literal that closes far below — on the two open decks in the pinned image
    that dropped one rule from a family of 8 and reduced another deck's layer
    identifiers from six names to NONE."""
    text = ("# the rule doesn't apply to the marker layer\n"
            "chip_area = extent.sized(0.0).area\n"
            "if (metal2.area / chip_area) * 100 < 30\n")
    code = D.deck_code_only(text)
    assert "chip_area = extent" in code
    assert "metal2.area" in code
    assert "doesn" not in code


def test_a_hash_inside_a_string_is_not_a_comment():
    code = D.deck_code_only("x.output(\"DM#{i}.3\", 'msg') ; y = layer.area\n")
    assert "y = layer.area" in code
    assert "DM" not in code


def test_offsets_and_line_count_are_preserved():
    text = "a = one.area  # note\n'msg'\nb = two.area\n"
    code = D.deck_code_only(text)
    assert len(code) == len(text)
    assert code.count("\n") == text.count("\n")
    assert code.splitlines()[0].startswith("a = one.area")
    assert "msg" not in code


# ---------------------------------------------------------------------------
# The exemption pays for itself.
# ---------------------------------------------------------------------------

def test_the_exemption_is_registered_with_a_real_argument():
    assert EXEMPT_NAME in C._NOT_PROSE
    reason = C._NOT_PROSE[EXEMPT_NAME].strip()
    assert len(reason) >= C._EXEMPT_REASON_MIN
    # the argument must name what makes the claim true and how it was checked
    low = reason.lower()
    for token in ("deck_code_only", "84", "negative control", "mutation"):
        assert token in low, f"the argument does not mention {token!r}"


def test_the_exemption_is_load_bearing_and_its_removal_refuses(monkeypatch):
    """CONTROL. The scanner must still flag this function — an exemption for
    something the scan no longer finds is dead weight, and `exemption_audit`
    says so — and with the entry removed the ratchet must refuse again."""
    root = PROGRAMS.parent
    flagged = C.scan(root)
    assert EXEMPT_NAME in flagged, (
        "the exemption no longer names an offender; delete it with the fix")
    assert C.exemption_audit(flagged, root) == []
    # THE GATE ITSELF, the way the landing runs it.
    assert C.main(["--ratchet", "--root", str(root)]) == 0

    monkeypatch.delitem(C._NOT_PROSE, EXEMPT_NAME)
    assert C.main(["--ratchet", "--root", str(root)]) == 1, (
        "with the entry removed the ratchet must refuse again — an exemption "
        "that changes nothing is not carrying the claim")
