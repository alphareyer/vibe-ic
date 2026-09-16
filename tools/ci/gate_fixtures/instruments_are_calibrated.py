"""`instruments are calibrated` — an uncalibrated reader of what a TOOL said.

THE MUTATION IS SYNTHESISED, NEVER STORED, and it is synthesised from the
GATE'S OWN GRAMMAR rather than from a token typed here. `_TOOL_PREFIXES` and
`_TOOL_PHRASES` in `programs/instrument_calibration.py` are what the gate reads
for; the fixture takes one entry out of each at run time, so a prefix or phrase
added tomorrow is the mutation this fixture plants tomorrow, with no edit here.
A fixture whose mutation is a frozen literal can drift away from the rule it
claims to falsify, and this repo has measured that.

TWO SHAPES, because the gate names two channels and a fixture that planted only
one would leave the other unfalsified:

  * family A — a module-level tuple of tool diagnostic ids, consulted with
    `marker in text`. This is the shape `phase3_one_shot_runner::
    antenna_routing_incomplete` has, and the first predicate this gate was
    written with could not see it.
  * family B — a bare `transcript.count("<tool phrase>")`, an id-less tool
    transcript grammar. This is the shape `sdf_gate_sim::sdf_annotation_census`
    has.

`can_fail` plants family A (the refusal must name it by `module::function`);
`can_pass` carries the SAME program with the grammar removed and everything
else identical — same file, same function, same `{"status": …}` return — so a
gate that refused on the shape rather than on the grammar cannot pass this pair.

WHAT THE SUBJECT MUST LOOK LIKE, read off the declared argv rather than guessed:

    python3 "$PG/instrument_calibration.py" --ratchet --root "$ROOT/vibe-ic-marketplace/plugins/vibe-ic"

so the program comes from the RUNTIME tree and the SUBJECT is
`<fixture root>/vibe-ic-marketplace/plugins/vibe-ic/`, whose `programs/` is what
`scan()` walks. The subject deliberately carries NONE of the modules named in
`_UNCALIBRATED_REGISTER`: the register speaks only about modules a subject
actually holds, so a tree without them is simply a different tree and the pair
measures the scan, not the register.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_mutation_fixtures as F

GATE = "instruments are calibrated"

_CAL = F.PROGRAMS / "instrument_calibration.py"


def _grammar_from_the_gate() -> tuple:
    """One tool diagnostic id and one tool transcript phrase, from the gate."""
    sys.path.insert(0, str(F.PROGRAMS))
    import instrument_calibration as C          # noqa: E402
    prefix = C._TOOL_PREFIXES[0]
    phrase = C._TOOL_PHRASES[0]
    if not prefix or not phrase:
        raise RuntimeError(
            f"{_CAL} declares no tool grammar — this fixture cannot synthesise "
            f"the mutation and must say so rather than pass vacuously")
    return f"[ERROR {prefix}-0305]", phrase


def _program(markers: str, counted: str) -> str:
    """The subject program. `markers`/`counted` are what the two arms vary."""
    return (
        "#!/usr/bin/env python3\n"
        '"""A reader that turns a tool artefact into a status."""\n'
        f"_ABORT = ({markers})\n"
        "\n"
        "\n"
        "def route_is_dead(log_text):\n"
        "    for marker in _ABORT:\n"
        "        if marker in log_text:\n"
        '            return {"status": "FAIL"}\n'
        '    return {"status": "PASS"}\n'
        "\n"
        "\n"
        "def annotated(transcript):\n"
        f"    return transcript.count({counted!r}) > 0\n")


def _tree(work: Path, markers: str, counted: str) -> Path:
    root = F.git_init(work / "subject")
    progs = root / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
    progs.mkdir(parents=True)
    (progs / "planted_reader.py").write_text(_program(markers, counted))
    F.git_commit(root)
    return root


def can_pass(work: Path) -> Path:
    """The same two functions with NO tool grammar anywhere in them.

    `_ABORT` holds a flow marker of the subject's own invention and `annotated`
    counts an ordinary word, so neither function reads what a tool said. The
    gate must stay silent: nothing here is an instrument.
    """
    return _tree(work, '"SUBJECT_OWN_MARKER",', "finished")


def can_fail(work: Path):
    ident, phrase = _grammar_from_the_gate()
    return _tree(work, f"{ident!r},", phrase), "planted_reader::route_is_dead"
