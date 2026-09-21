"""W5/28 -- the latch-up screen reads the IO LEFs this run RECORDED.

THE DEFECT. `_recorded_noncore_masters` reopens the `io_library_lefs` that this
run's own `reports/phase3/io_pad_chip_top.json` names, to learn which placed
masters are PAD/ENDCAP and therefore not core cells. It read them from the HOST
and swallowed failure:

    except (OSError, ValueError, UnicodeError):
        return set()

MEASURED on the two spm runs the W5 report cites: each records 15
`io_library_lefs` under `/foss/pdks/ciel/...`, and EVERY one of them is
host-unreadable. So the exclusion set was empty on every run, and every pad was
screened as a core cell by the latch-up spacing check.

THE CONSERVATIVE DIRECTION IS DELIBERATE AND IS KEPT. The function's own contract
is "unreadable metadata grants no exclusions", and that is the safe way to be
wrong: more masters screened, never fewer. What was wrong is that it fired ALWAYS,
and silently -- "no exclusions" and "this PDK declares no pad masters" were the
same answer, so a reader of the report could not tell a screen that had read the
PDK from one that had not.

So: the read goes through `_pdk_layer_authority._query` (the container this run
published, else the local filesystem), and a recorded LEF that cannot be read is
NAMED.
"""
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import latchup_esd_spacing_check as LE                  # noqa: E402

#: a PAD master and a core master, in one LEF, as a distribution ships them
_LEF_TEXT = (
    "MACRO PADX\n  CLASS PAD ;\n  SIZE 75.0 BY 260.0 ;\nEND PADX\n"
    "MACRO CORECELL\n  CLASS CORE ;\n  SIZE 1.0 BY 4.0 ;\nEND CORECELL\n"
)
_IMAGE_LEF = "/pdk-inside-the-image/libs.ref/io/lef/io.lef"


class _ImageReader:
    """Only the container has the LEF. Mirrors the reader protocol `_query` uses."""

    def __init__(self, files):
        self.files = dict(files)

    def is_dir(self, path):
        return any(str(f).startswith(str(path).rstrip("/") + "/") for f in self.files)

    def is_file(self, path):
        return str(path) in self.files

    def glob(self, root, pattern):
        import fnmatch
        return sorted(f for f in self.files
                      if str(Path(f).parent) == str(root)
                      and fnmatch.fnmatch(Path(f).name, pattern))

    def read_text(self, path):
        return self.files.get(str(path))


def _project(tmp_path: Path, lef_paths) -> Path:
    """A run tree carrying the record, and the DEF path the gate is handed."""
    project = tmp_path / "run"
    (project / "reports" / "phase3").mkdir(parents=True)
    (project / "reports" / "phase3" / "io_pad_chip_top.json").write_text(
        json.dumps({"verdict": "WROTE", "io_library_lefs": list(lef_paths)}))
    def_dir = project / "phase3" / "stage3" / "pnr"
    def_dir.mkdir(parents=True)
    def_file = def_dir / "routed.def"
    def_file.write_text("DESIGN top ;\n")
    return def_file


# -- DIRECTION 1: unreadable where the audit runs -> no exclusions, and it SAYS so
def test_an_unreadable_recorded_lef_grants_no_exclusions_and_is_named(tmp_path):
    def_file = _project(tmp_path, [_IMAGE_LEF])
    got = LE._recorded_noncore_masters(def_file)
    assert got == set(), "the conservative direction must not change"
    assert LE._unreadable_recorded_lefs == [_IMAGE_LEF], (
        "a recorded LEF that could not be read must be NAMED, or 'no exclusions' "
        "cannot be told from 'this PDK declares no pad masters'")


# -- DIRECTION 2: read where the run reads it -> the PAD master is excluded ----
def test_the_container_copy_yields_the_pad_exclusion(tmp_path):
    """THE RED. The same path, read through the seam, gives the real answer."""
    def_file = _project(tmp_path, [_IMAGE_LEF])
    reader = _ImageReader({_IMAGE_LEF: _LEF_TEXT})
    got = LE._recorded_noncore_masters(def_file, reader=reader)
    assert got == {"PADX"}, got
    assert LE._unreadable_recorded_lefs == []
    # and the consequence the screen cares about: a pad is no longer a std cell
    assert LE._is_std_cell("PADX", got) is False
    assert LE._is_std_cell("CORECELL", got) is True


def test_a_host_readable_lef_still_works(tmp_path):
    """No container, a real file on disk: unchanged behaviour."""
    lef = tmp_path / "io.lef"
    lef.write_text(_LEF_TEXT)
    def_file = _project(tmp_path, [str(lef)])
    assert LE._recorded_noncore_masters(def_file) == {"PADX"}
    assert LE._unreadable_recorded_lefs == []


# -- the disclosure reaches the RECORD, not just a module variable -------------
def test_the_record_discloses_that_the_exclusions_were_unavailable(tmp_path):
    def_file = _project(tmp_path, [_IMAGE_LEF])
    LE._recorded_noncore_masters(def_file)
    assert LE._unreadable_recorded_lefs, "fixture no longer reproduces the case"
    src = (PROGRAMS / "latchup_esd_spacing_check.py").read_text()
    assert "lef_noncore_masters_unavailable" in src
    assert "NOT a finding that the PDK declares no pad masters" in src, (
        "the report must say the conservative screen is not a claim about the PDK")


def test_a_conflicting_declaration_still_grants_no_exclusion(tmp_path):
    """Untouched contract: a master declared both PAD and CORE is not excluded."""
    conflict = ("MACRO BOTH\n  CLASS PAD ;\n  SIZE 1.0 BY 1.0 ;\nEND BOTH\n"
                "MACRO BOTH\n  CLASS CORE ;\n  SIZE 1.0 BY 1.0 ;\nEND BOTH\n")
    def_file = _project(tmp_path, [_IMAGE_LEF])
    reader = _ImageReader({_IMAGE_LEF: conflict})
    assert LE._recorded_noncore_masters(def_file, reader=reader) == set()
