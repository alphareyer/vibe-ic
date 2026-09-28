"""The whole-flow custom steps name the tool that wrote their views."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _tool_log_provenance as provenance  # noqa: E402


def test_placed_spares_and_postroute_repair_are_openroad_runs():
    assert provenance.underlying_tool("Vibeic.InsertSpareCells") == "openroad"
    assert provenance.underlying_tool("Vibeic.PostRouteRepair") == "openroad"
    assert provenance.underlying_tool("Vibeic.PostRouteRepair-1") == "openroad"
