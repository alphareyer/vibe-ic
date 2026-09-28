"""The MCP PSM session must exclude a core's promoted strap pins."""
from pathlib import Path
import re
import sys

PROGRAMS = Path(__file__).resolve().parents[2] / "programs"
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "tests"))

import _psm_source_model as SM  # noqa: E402
from _tcl_walk import walk as _walk  # noqa: E402
from test_fx_spm_psm_source_model import _DB  # noqa: E402

SRC = (Path(__file__).resolve().parents[1] / "src/index.js").read_text()


def _fragment() -> str:
    body = SRC[SRC.index('"eda_ir_drop"'):SRC.index('"eda_equiv"')]
    match = re.search(r"const psmSourceTcl = `([^`]*)`;", body, re.S)
    assert match, "eda_ir_drop has no PSM source exclusion in its actual Tcl"
    assert body.index("${psmSourceTcl}") < body.index("analyze_power_grid -net $_vddnet")
    assert "source_model:" in body and "PSM-0073" in body
    return match.group(1)


def _run(tcl: str, inst: str, tmp_path: Path) -> str:
    tmp_path.mkdir(parents=True)
    out, err, route = _walk(_DB + f"set ::insts {{{inst}}}\n" + tcl
                            + "\nputs TCL_DONE\n", "", tmp_path)
    assert "TCL_DONE" in out, f"{route}: {out}\n{err}"
    return out


def test_mcp_session_has_the_same_source_rule_as_the_flow(tmp_path):
    mirrored = _fragment()
    for inst in ("inst_core", "inst_pad"):
        actual = _run(mirrored, inst, tmp_path / inst)
        canonical = _run(SM.exclude_promoted_pins_tcl(), inst,
                         tmp_path / f"canonical_{inst}")
        assert SM.read(actual) == SM.read(canonical)
        if inst == "inst_core":
            assert SM.read(actual)["promoted_supply_pins_excluded"] == 3
        else:
            assert SM.read(actual)["placed_pads"] == 1
