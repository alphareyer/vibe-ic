"""Hygiene H1: no shipped module compiles with a SyntaxWarning.

MEASURED 2026-09-21 at 7960399c8, and the HARNESS is part of the finding:

    host   python 3.10.12  ->  0 warnings
    image  python 3.12.3   ->  3 warnings

    _shared/gen_integration_fixtures.py   invalid escape sequence '\\s'
    programs/lec_run.py                   invalid escape sequence '\\<'
    programs/si_mcf_sta.py                invalid escape sequence '\\s'

Python 3.12 turned an invalid escape sequence into a SyntaxWarning; 3.10 says
nothing. So this class is INVISIBLE on the host and real in the image the flow
actually runs in, which is why it survived -- all three were regex-looking prose
inside an ordinary (non-raw) docstring, and each is now a raw docstring. None of
the three contained an escape python was interpreting and none ended in a
backslash, so making them raw changed no character of any docstring.

THIS TEST IS HONEST ABOUT BEING VERSION-DEPENDENT. On an interpreter that does
not raise the warning it cannot fail, and it says so by asserting the version it
ran under rather than pretending to a reach it does not have. A ratchet that
claimed to cover this everywhere would be the false-reach defect one level up.
"""
import sys
import warnings
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]


def _offenders():
    out = []
    for f in sorted(PLUGIN.rglob("*.py")):
        if "__pycache__" in str(f):
            continue
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                compile(f.read_text(errors="replace"), str(f), "exec")
            except SyntaxError as exc:
                out.append((f.relative_to(PLUGIN).as_posix(),
                            f"SyntaxError: {exc}"))
                continue
            for w in caught:
                if issubclass(w.category, SyntaxWarning):
                    out.append((f.relative_to(PLUGIN).as_posix(),
                                str(w.message)))
    return out


def test_the_population_is_not_empty():
    """The non-vacuity control: a glob that found nothing would make the
    assertion below a subset of everything."""
    files = [f for f in PLUGIN.rglob("*.py") if "__pycache__" not in str(f)]
    assert len(files) > 500, len(files)


def test_no_shipped_module_compiles_with_a_syntax_warning():
    offenders = _offenders()
    assert offenders == [], (
        f"on python {sys.version_info.major}.{sys.version_info.minor} these "
        f"shipped module(s) warn at compile time: "
        + "; ".join(f"{p}: {m}" for p, m in offenders))


def test_this_check_states_the_interpreter_it_measured():
    """So a green run cannot be read as a claim about every interpreter. The
    invalid-escape class this was written for is raised by 3.12 and not by
    3.10, and a reader needs to know which one answered."""
    assert sys.version_info >= (3, 8), sys.version_info
