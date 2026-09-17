"""vibe-ic#2361 — a signoff_config.json that exists but does not parse REFUSES.

Before the fix the staged-PDK branch of `_detect_pdk` read the bridge
declaration inside `except Exception: _signoff_cfg = {}`, so one misplaced comma
was indistinguishable from an absent file: `lvs_engine` silently reverted to
"magic" (and tap_geom_layers, dummy_fill, ... to their defaults) with nothing
on stderr and nothing in any report. `_staged_pdk_tech_lef._declared_path` did
the same for the declared `tech_lef`.

Measured on main dfdb3e8d2 before the fix: a staged PDK whose
signoff_config.json is `{"lvs_engine": "klayout", "port_label_restore": true,}`
resolved to `lvs_engine='magic'`, `port_label_restore=None`, no stderr line.

After: ABSENT -> defaults (the file declared nothing); UNREADABLE ->
`SIGNOFF_CONFIG_UNREADABLE` naming the path and the parse error, and the flow
halts at PDK resolution; WELL-FORMED -> the declared values are the ones used.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _staged_pdk_tech_lef as T  # noqa: E402
import phase3_one_shot_runner as p3  # noqa: E402

_TECH = "LAYER MET1\n  TYPE ROUTING ;\nEND MET1\nEND LIBRARY\n"
_CELLS = ("SITE core ;\nMACRO INVX1\n  CLASS CORE ;\n  SITE core ;\n"
          "END INVX1\nEND LIBRARY\n")


def _stage(root: Path, signoff_text: str | None, techs=("x_tech.tlef",)) -> Path:
    pdk = root / "input" / "pdk"
    (pdk / "liberty").mkdir(parents=True)
    (pdk / "liberty" / "x_tt.lib").write_text("library(x){}\n")
    (pdk / "lef").mkdir()
    for t in techs:
        (pdk / "lef" / t).write_text(_TECH)
    (pdk / "lef" / "x_cells.lef").write_text(_CELLS)
    if signoff_text is not None:
        (pdk / "bridge").mkdir()
        (pdk / "bridge" / "signoff_config.json").write_text(signoff_text)
    return root


@pytest.fixture(autouse=True)
def _no_container(monkeypatch):
    monkeypatch.delenv("EDA_CONTAINER", raising=False)


_MALFORMED = {
    "trailing_comma": '{"lvs_engine": "klayout", "port_label_restore": true,}',
    "truncated": '{"lvs_engine": "klayout"',
    "top_level_list": '["lvs_engine", "klayout"]',
    "top_level_null": "null",
}


@pytest.mark.parametrize("text", list(_MALFORMED.values()),
                         ids=list(_MALFORMED))
def test_a_malformed_config_halts_pdk_resolution_by_name(tmp_path, text):
    project = _stage(tmp_path, text)
    cfg = project / "input/pdk/bridge/signoff_config.json"
    with pytest.raises(SystemExit) as ei:
        p3._detect_pdk(project)
    msg = str(ei.value)
    assert "SIGNOFF_CONFIG_UNREADABLE" in msg, msg
    assert str(cfg) in msg, msg


def test_the_refusal_carries_the_parse_error(tmp_path):
    project = _stage(tmp_path, _MALFORMED["trailing_comma"])
    with pytest.raises(SystemExit, match=r"JSONDecodeError: .*line 1 column"):
        p3._detect_pdk(project)


def test_a_malformed_config_is_named_even_when_the_stack_is_ambiguous(tmp_path):
    """With several tech LEFs the stack resolver reads the same file first. It
    must name the parse error, not ask the integrator to set a `tech_lef` key
    they already set in the file it could not read."""
    project = _stage(tmp_path, _MALFORMED["trailing_comma"],
                     techs=("a_tech.tlef", "b_tech.tlef"))
    with pytest.raises(SystemExit) as ei:
        p3._detect_pdk(project)
    assert "SIGNOFF_CONFIG_UNREADABLE" in str(ei.value), str(ei.value)


def test_the_shared_loader_refuses_for_every_consumer(tmp_path):
    pdk = _stage(tmp_path, _MALFORMED["truncated"]) / "input" / "pdk"
    with pytest.raises(T.SignoffConfigUnreadable):
        T.load_signoff_config(pdk)
    # The stack resolver's existing catchers (runner, via-stack gate) catch
    # TechLefResolutionError; the refusal must reach them as one.
    with pytest.raises(T.TechLefResolutionError, match="SIGNOFF_CONFIG_UNREADABLE"):
        T.select_staged_tech_lef(pdk, (pdk / "lef" / "x_tech.tlef",))


def test_a_well_formed_config_is_the_one_used(tmp_path):
    decl = {"lvs_engine": "klayout", "port_label_restore": True,
            "tap_geom_layers": {"nwell": "2/0", "nplus": "4/0"}}
    pdk = p3._detect_pdk(_stage(tmp_path, json.dumps(decl)))
    assert pdk.lvs_engine == "klayout"          # not the "magic" default
    assert pdk.port_label_restore is True
    assert pdk.tap_geom_layers == decl["tap_geom_layers"]


def test_an_absent_config_is_not_an_unreadable_one(tmp_path):
    pdk = p3._detect_pdk(_stage(tmp_path, None))
    assert pdk.lvs_engine == "magic"
    assert pdk.signoff_config_path is None
    assert T.load_signoff_config(tmp_path / "input" / "pdk") == {}


# ── no broad except left at the site ───────────────────────────────────────
def _fn(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _broad_handlers(fn: ast.FunctionDef):
    return [h.lineno for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)
            and (h.type is None
                 or (isinstance(h.type, ast.Name)
                     and h.type.id in ("Exception", "BaseException")))]


def test_detect_pdk_has_no_broad_except():
    tree = ast.parse((PROGRAMS / "phase3_one_shot_runner.py").read_text(
        encoding="utf-8"))
    assert _broad_handlers(_fn(tree, "_detect_pdk")) == []


def test_the_signoff_readers_have_no_broad_except():
    tree = ast.parse((PROGRAMS / "_staged_pdk_tech_lef.py").read_text(
        encoding="utf-8"))
    for name in ("load_signoff_config", "_declared_path"):
        assert _broad_handlers(_fn(tree, name)) == [], name
