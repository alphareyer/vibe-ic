"""The power-aware LVS emitter must receive the same IO LEFs PnR consumed."""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as P  # noqa: E402


def test_padring_io_lefs_are_staged_by_content_for_host_side_lvs(tmp_path,
                                                                  monkeypatch):
    """Container-only IO views must not disappear before LVS netlist emission."""
    project = tmp_path / "project"
    extracted = project / "phase3" / "stage3" / "extracted"
    lef_text = "MACRO io_pad\n PIN DVDD\n  USE POWER ;\n END DVDD\nEND io_pad\n"

    monkeypatch.setattr(P._pl, "extracted_dir", lambda _p: extracted)
    monkeypatch.setattr(P, "_discover_padring_io_views",
                        lambda _pdk, _container: (["/inside/a.lef", "/inside/b.lef"],
                                                   ["/inside/a.gds"]))
    monkeypatch.setattr(P, "_read_pdk_text",
                        lambda path, _container: lef_text + "# " + path + "\n")

    got = P._stage_padring_io_lefs_for_lvs(project, object(), "eda")

    assert len(got) == 2
    assert all(p.is_file() and p.read_text().startswith(lef_text) for p in got)
    assert all(p.name.startswith("padring_io_") and p.name.endswith(".extract.lef")
               for p in got)
