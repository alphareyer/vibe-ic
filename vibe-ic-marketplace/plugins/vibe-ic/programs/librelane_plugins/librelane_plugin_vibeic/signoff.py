"""Steps 37.3 and 37.5ic's custom steps (lane mig105).

`Vibeic.FinishingXOR` measures what finishing (KLayout.SealRing,
KLayout.Filler) did to the stream it started from, on the same KLayout DRC
engine as LibreLane's `xor.drc` (tiled, threaded): see `finishing_xor.drc`
for the four defects it counts.  LibreLane's only XOR compares the Magic and
KLayout streams BEFORE finishing, so nothing upstream checks that finishing
left the design layers alone.

`Vibeic.DatabaseUnit` compares the stream's own GDSII UNITS record with the
declared database unit.  No LibreLane step checks it, and a stream written at
another grid is off-grid everywhere at once.  An undeclared unit is
NOT_MEASURED and fails the step, never a pass.

Both are shipped with the plugin (never in the fork): they are vibe-ic's own
gates over LibreLane's state, like `Vibeic.IRDropChecker`.
"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Optional, Tuple

from librelane.common import Path
from librelane.config import Variable
from librelane.state import DesignFormat, State
from librelane.steps.step import MetricsUpdate, Step, StepError, ViewsUpdate

from .gds_units import gds_database_unit_um

__all__ = ["FinishingXOR", "DatabaseUnit", "gds_database_unit_um"]

FINISHING_XOR_SCRIPT = os.path.join(os.path.dirname(__file__), "finishing_xor.drc")


@Step.factory.register()
class FinishingXOR(Step):
    """Finishing never removes, moves or covers design geometry.

    Reads the state's GDS as the finished stream.  ``VIBEIC_FINISHING_PRE_GDS``
    is the stream finishing started from and ``VIBEIC_FINISHING_SEALED_GDS``
    the SealRing output (unset: no seal-ring step).  The core rectangle is the
    declared ``VIBEIC_FINISHING_CORE_AREA``; unset, the in-core count is not
    measured and the step fails rather than pass without it.
    """

    id = "Vibeic.FinishingXOR"
    name = "vibe-ic finishing XOR (pre-finish vs finished stream)"
    inputs = [DesignFormat.GDS]
    outputs = []

    config_vars = [
        Variable("VIBEIC_FINISHING_PRE_GDS", Path,
                 "The stream finishing started from (a StreamOut output)."),
        Variable("VIBEIC_FINISHING_SEALED_GDS", Optional[Path],
                 "KLayout.SealRing's output stream, when a ring was drawn."),
        Variable("VIBEIC_FINISHING_CORE_AREA", Optional[str],
                 "The declared core rectangle 'x0,y0,x1,y1' in um."),
        Variable("VIBEIC_FINISHING_XOR_THREADS", Optional[int],
                 "KLayout DRC threads; unset = the process limit."),
        Variable("VIBEIC_FINISHING_XOR_TILE_SIZE", Optional[int],
                 "Tile size in um (xor.drc's default, 500, when unset).",
                 units="µm"),
    ]

    def run(self, state_in: State, **kwargs) -> Tuple[ViewsUpdate, MetricsUpdate]:
        final = state_in[DesignFormat.GDS]
        if final is None:
            raise StepError(f"{self.id}: NOT_MEASURED -- the state carries no GDS")
        core = self.config.get("VIBEIC_FINISHING_CORE_AREA")
        if not core:
            raise StepError(f"{self.id}: NOT_MEASURED -- no declared core "
                            "rectangle (VIBEIC_FINISHING_CORE_AREA)")
        sealed = self.config.get("VIBEIC_FINISHING_SEALED_GDS")
        kwargs, env = self.extract_env(kwargs)
        threads = self.config.get("VIBEIC_FINISHING_XOR_THREADS") or os.cpu_count() or 1
        tile = self.config.get("VIBEIC_FINISHING_XOR_TILE_SIZE")
        result = self.run_subprocess(
            ["klayout", "-b", "-r", FINISHING_XOR_SCRIPT,
             "-rd", f"pre={os.path.abspath(str(self.config['VIBEIC_FINISHING_PRE_GDS']))}",
             "-rd", f"sealed={os.path.abspath(str(sealed)) if sealed else ''}",
             "-rd", f"final={os.path.abspath(str(final))}",
             "-rd", f"top_cell={self.config['DESIGN_NAME']}",
             "-rd", f"core={core}",
             "-rd", f"jobs={threads}",
             "-rd", f"tilesize={tile or ''}",
             "-rd", f"rdb_out={os.path.join(self.step_dir, 'finishing_xor.xml')}"],
            env=env)
        metrics = dict(result["generated_metrics"])
        if "vibeic__finishing_xor__defect__count" not in metrics:
            raise StepError(f"{self.id}: NOT_MEASURED -- the XOR printed no "
                            "defect count; see its log")
        return {}, metrics


@Step.factory.register()
class DatabaseUnit(Step):
    """The stream's UNITS agrees with the declared database unit."""

    id = "Vibeic.DatabaseUnit"
    name = "vibe-ic database unit vs the declared grid"
    inputs = [DesignFormat.GDS]
    outputs = []

    config_vars = [
        Variable("VIBEIC_DATABASE_UNIT_UM", Optional[Decimal],
                 "The declared database unit in um (tape-out declaration "
                 "`database_unit_um`).", units="µm"),
    ]

    def run(self, state_in: State, **kwargs) -> Tuple[ViewsUpdate, MetricsUpdate]:
        gds = state_in[DesignFormat.GDS]
        measured = gds_database_unit_um(str(gds)) if gds else None
        declared = self.config.get("VIBEIC_DATABASE_UNIT_UM")
        if measured is None:
            raise StepError(f"{self.id}: NOT_MEASURED -- the stream carries no "
                            "UNITS record")
        if declared is None:
            raise StepError(f"{self.id}: NOT_MEASURED -- database_unit_um is not "
                            f"declared; the stream says {measured} um")
        declared = float(declared)
        # A database unit is a ratio: equal within a relative 1e-9, as
        # general_precheck's own rung compares it.
        mismatch = 0 if abs(measured - declared) <= abs(declared) * 1e-9 else 1
        metrics: MetricsUpdate = {
            "vibeic__gds__database_unit_um": Decimal(repr(measured)),
            "vibeic__gds__database_unit_mismatch__count": mismatch,
        }
        if mismatch:
            # Recorded, not raised: the count is the measurement the host-side
            # judge (librelane_pv_signoff.judge_pv) fails on.
            self.err(f"UNITS declares {measured} um; the declaration says "
                     f"{declared} um")
        return {}, metrics
