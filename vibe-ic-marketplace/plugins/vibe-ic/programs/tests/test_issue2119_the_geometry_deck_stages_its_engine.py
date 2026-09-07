#!/usr/bin/env python3
"""vibe-ic#2119 — the GDS-geometry antenna deck must STAGE its engine.

`gds_antenna_deck_check.run` asks `runner.covers(engine)` — so it is honest
about reachability — but until this change it never staged the engine into a
mount the runner can reach. The engine resolves to a HOST path under this
plugin's own installation; a container built the way this repo's own helper
builds one (`tools/vibeic-eda/restart-eda.sh` binds the designs directory and
nothing else) has no such path. The one INDEPENDENT antenna opinion in the flow
therefore DISCLOSED_SKIPped on every PDK, for every design, and said so in a
line nobody had to act on.

THREE DIRECTIONS, because a stage that always succeeds proves nothing:

  * a runner that reaches the project but NOT the plugin tree now RUNS the deck
    (and discloses the copy) instead of skipping;
  * a runner that reaches the GDS and NOTHING beside it — docker binds single
    files as readily as directories — is a NAMED FAIL, never a skip and never a
    pass;
  * a runner that already reaches the engine stages nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import gds_antenna_deck_check as G                            # noqa: E402

_ENGINE = _PROGRAMS / "gds_antenna" / "antenna_check.py"

_DECK = {"layers": [{"name": "m1", "ratio_limit": 400}], "mode": "layer"}


def _project(tmp_path: Path) -> Path:
    """A tree carrying a streamed GDS and a declared deck config."""
    proj = tmp_path / "proj"
    gdsdir = proj / "phase3" / "stage4" / "gds"
    gdsdir.mkdir(parents=True)
    (gdsdir / "top.gds").write_bytes(b"\x00\x06\x00\x02\x00\x07")
    sign = proj / "signoff"
    sign.mkdir(parents=True)
    (sign / "gate_oxide_deck.json").write_text(json.dumps(_DECK))
    return proj


class _ProjectOnlyRunner:
    """The measured shape: the project tree is mounted, the plugin tree is not."""

    kind = "container"
    detail = "vibeic-eda"

    def __init__(self, root: Path, verdict="PASS"):
        self._root = root
        self._verdict = verdict
        self.ran = []

    def covers(self, p):
        try:
            Path(str(p)).resolve().relative_to(self._root.resolve())
        except ValueError:
            return False
        return True

    def exists(self, p):
        return True

    def cpath(self, p):
        return str(p)

    def run(self, script, env, *, path_keys=(), timeout=1800):
        self.ran.append(str(script))
        Path(env["ANT_OUT"]).write_text(json.dumps(
            {"verdict": self._verdict, "worst_ratio": 12.5, "violations": 0}))
        return 0, "", ""


@pytest.fixture()
def proj(tmp_path):
    return _project(tmp_path)


def test_the_engine_is_staged_into_a_directory_the_runner_reaches(proj, monkeypatch):
    runner = _ProjectOnlyRunner(proj)
    monkeypatch.setattr(G._kl, "find_runner", lambda *a, **k: runner)
    res = G.run(proj, None, None, None, None)

    assert res["verdict"] == "PASS", res
    # It RAN, and it ran a path the runner can open.
    assert runner.ran, res
    assert runner.covers(runner.ran[0]), runner.ran
    # …and the copy is DISCLOSED, because a copy nobody is told about is
    # indistinguishable from an engine that was reachable all along.
    staged = res.get("engine_staged")
    assert staged, res
    assert Path(staged).read_bytes() == _ENGINE.read_bytes()
    assert G._STAGE_REL in staged, staged
    # The staging directory must not be swallowed by the sibling router-report
    # gate, which rglobs `*antenna*` across the whole project.
    assert "antenna" not in G._STAGE_REL


def test_an_engine_the_runner_still_cannot_open_is_a_named_failure(proj, monkeypatch):
    """The other direction. A runner that reaches the GDS but nothing beside it
    leaves the engine unreachable even after staging. That is THIS PROGRAM
    failing, and it must be recorded as FAIL naming the engine — never a
    DISCLOSED_SKIP (which reads as "this PDK declares no deck") and never a
    PASS."""
    gds = proj / "phase3" / "stage4" / "gds" / "top.gds"

    class _OnlyTheGds(_ProjectOnlyRunner):
        def covers(self, p):
            return str(p) in (str(gds), str(proj / "reports" / "phase3"))

    runner = _OnlyTheGds(proj)
    monkeypatch.setattr(G._kl, "find_runner", lambda *a, **k: runner)
    res = G.run(proj, None, None, None, None)

    assert res["verdict"] == "FAIL", res
    assert "cannot be opened where KLayout runs" in res["reason"], res
    assert _ENGINE.name in res["reason"], res
    assert runner.ran == [], "it must refuse BEFORE launching the engine"


def test_a_runner_that_already_reaches_the_engine_stages_nothing(tmp_path):
    class _Everything(_ProjectOnlyRunner):
        def covers(self, p):
            return True

    into = tmp_path / "stage"
    got, why = G.stage_engine(_Everything(tmp_path), _ENGINE, into)
    assert why is None
    assert got == _ENGINE
    assert not into.exists()


def test_an_unreachable_engine_is_staged_byte_for_byte(tmp_path):
    into = tmp_path / "stage"
    got, why = G.stage_engine(_ProjectOnlyRunner(tmp_path), _ENGINE, into)
    assert why is None
    assert got == into / _ENGINE.name
    assert got.read_bytes() == _ENGINE.read_bytes()
