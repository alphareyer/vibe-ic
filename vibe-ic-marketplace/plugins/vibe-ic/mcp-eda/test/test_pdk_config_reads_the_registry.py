#!/usr/bin/env python3
"""mcp-eda's PDK facts come from programs/pdk_registry.json (audit §3.23-1).

MEASURED before the cut-over: index.js pdkConfig selected gf180's
`gf180mcu_fd_sc_mcu7t5v0__tt_025C_3v30.lib`, while the registry the flow reads
pins the 5 V library's nominal corner `tt_025C_5v00` (vibeic-eda 0.3.86:
`nom_voltage : 5`). Every mcp-eda synth / STA / PnR / IR run on gf180 therefore
used a different library than the flow. These tests EXECUTE the module with
node: the config is the registry's, a registry glob must resolve to exactly one
file, and a PDK the registry does not name is an error, not gf180.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

MCP_ROOT = Path(__file__).resolve().parents[1]
MOD = MCP_ROOT / "src" / "lib" / "pdk_registry.mjs"
REGISTRY = MCP_ROOT.parent / "programs" / "pdk_registry.json"
SRC = (MCP_ROOT / "src" / "index.js").read_text()


def _node(script: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is required to execute the mcp-eda module")
    r = subprocess.run([node, "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _cfg(key: str, hits=None) -> dict:
    hits = json.dumps(hits if hits is not None else [])
    return _node(f"""
import {{ registryPdkConfig, assetPath }} from {json.dumps(str(MOD))};
const out = {{}};
try {{
  const cfg = registryPdkConfig({json.dumps(key)}, "/foss/pdks");
  out.cfg = cfg;
  try {{ out.lib = assetPath(cfg, cfg.liberty_rel, () => {hits}); }}
  catch (e) {{ out.lib_error = String(e.message); }}
}} catch (e) {{ out.error = String(e.message); }}
console.log(JSON.stringify(out));
""")


def _entry(key: str) -> dict:
    doc = json.loads(REGISTRY.read_text())
    return next(e for e in doc["pdks"] if e.get("per_pdk_table_key") == key)


def test_gf180_takes_the_registry_liberty_corner_not_3v30():
    e = _entry("gf180")
    got = _cfg("gf180", ["/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0"
                         "/lib/gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib"])
    cfg = got["cfg"]
    assert cfg["liberty_rel"] == e["liberty_glob"]
    assert "5v00" in cfg["liberty_rel"] and "3v30" not in cfg["liberty_rel"]
    assert got["lib"].endswith("__tt_025C_5v00.lib")
    assert (cfg["site"], cfg["metal_prefix"]) == (e["site"], e["metal_prefix"])
    assert cfg["pdk_path"] == "/foss/pdks/" + e["name"]
    assert cfg["antenna_diode_cell"] == e["antenna_diode_cell"]


def test_a_glob_that_is_not_unique_is_refused_by_name():
    got = _cfg("gf180", ["/a/x__tt_025C_5v00.lib", "/a/y__tt_025C_5v00.lib"])
    assert "PDK_ASSET_GLOB_NOT_UNIQUE" in got["lib_error"]
    got = _cfg("gf180", [])
    assert "matched 0 file(s)" in got["lib_error"]


def test_an_exact_registry_path_needs_no_container():
    e = _entry("sky130")
    got = _cfg("sky130")
    assert got["lib"] == "/foss/pdks/" + e["name"] + "/" + e["liberty_glob"]
    assert got["cfg"]["vdd_pin"] == "VPWR" and got["cfg"]["vdd_net"] == "VDD"


def test_a_pdk_the_registry_does_not_name_is_an_error_not_gf180():
    got = _cfg("no_such_pdk")
    assert "PDK_NOT_IN_REGISTRY" in got["error"]


def test_index_js_carries_no_second_liberty_table():
    body = SRC[SRC.index("function pdkConfig("):SRC.index("function libNomVoltage(")]
    assert "lib_suffix:" not in body.split('if (pdk === "custom"')[0]
    assert "registryPdkConfig(" in body
    assert "__tt_025C_3v30.lib`" not in SRC  # no literal gf180 3v30 path left
