"""vibe-ic's LibreLane plugin: custom steps that travel with the tool flow.

LibreLane imports every top-level module named ``librelane_plugin_*`` it finds
on ``sys.path`` (``librelane/plugins.py``), and each module registers its steps
with ``Step.factory``. The plugin ships inside the vibe-ic plugin (never in the
vibeic/librelane fork): the step's logic is vibe-ic's own Design-for-ECO
policy, it imports vibe-ic's plan builder (``programs/_spare_plan.py``), and it
must move with the plugin version that the gates reading its output belong to.
``librelane_contract`` mounts ``programs/`` read-only and puts
``programs/librelane_plugins`` on ``PYTHONPATH`` for exactly the steps whose id
starts with ``Vibeic.`` -- nothing else sees it.

``Vibeic.InsertSpareCells`` (flow step 18) runs after
``OpenROAD.DetailedPlacement``: it inserts the Design-for-ECO spare pool,
legalizes only the spares, ties every floating spare input to that spare's own
tie-low driver, connects the spares' supply pins explicitly, locks them FIRM,
marks them dont_touch, and records ``check_placement``'s count. See
``insert_spare_cells.py``.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import List, Optional, Tuple

from librelane.common import Path
from librelane.config import Variable
from librelane.state import DesignFormat, State
from librelane.steps.common_variables import dpl_variables
from librelane.steps.odb import OdbpyStep
from librelane.steps.step import MetricsUpdate, Step, StepError, ViewsUpdate

__all__ = ["InsertSpareCells"]


@Step.factory.register()
class InsertSpareCells(OdbpyStep):
    """Design-for-ECO spare-cell pool, inserted into the placed database."""

    id = "Vibeic.InsertSpareCells"
    name = "Insert Design-for-ECO Spare Cells"

    inputs = [DesignFormat.ODB]
    outputs = [DesignFormat.ODB, DesignFormat.DEF, DesignFormat.NETLIST,
               DesignFormat.POWERED_NETLIST]

    config_vars = dpl_variables + [
        Variable(
            "VIBEIC_SPARE_PLAN",
            Optional[Path],
            "A spare plan written by `_spare_plan.build_spare_cells_plan` "
            "(names, classes, cells, target positions). When set, exactly "
            "these spares are inserted, so a caller that reserves their names "
            "elsewhere (the route deck) agrees with the database.",
        ),
        Variable(
            "VIBEIC_SPARE_DENSITY",
            Optional[Decimal],
            "Spare density as a fraction of the placed logic cells. Used to "
            "build the plan in-step from the placed database when "
            "VIBEIC_SPARE_PLAN is unset; 0 inserts nothing.",
        ),
        Variable(
            "VIBEIC_SPARE_TIELO_CELL",
            Optional[str],
            "Tie-low `cell/pin` that drives each spare's floating inputs. "
            "Defaults to the PDK's SYNTH_TIELO_CELL.",
        ),
    ]

    def get_script_path(self) -> str:
        return os.path.join(os.path.dirname(__file__), "insert_spare_cells.py")

    def _report_path(self) -> str:
        return os.path.join(self.step_dir, "spare_cells.json")

    def get_command(self) -> List[str]:
        assert self.config_path is not None, "get_command called before start()"
        design = self.config["DESIGN_NAME"]
        return super().get_command() + [
            "--step-config", self.config_path,
            "--report", self._report_path(),
            "--output-nl", os.path.join(self.step_dir, f"{design}.nl.v"),
            "--output-pnl", os.path.join(self.step_dir, f"{design}.pnl.v"),
        ]

    def run(self, state_in: State, **kwargs) -> Tuple[ViewsUpdate, MetricsUpdate]:
        views, metrics = super().run(state_in, **kwargs)
        design = self.config["DESIGN_NAME"]
        views[DesignFormat.NETLIST] = Path(os.path.join(self.step_dir, f"{design}.nl.v"))
        views[DesignFormat.POWERED_NETLIST] = Path(
            os.path.join(self.step_dir, f"{design}.pnl.v"))
        with open(self._report_path(), encoding="utf8") as stream:
            record = json.load(stream)
        measured = record.get("measured", {})
        metrics = dict(metrics)
        for key, value in (
                ("vibeic__spare__planned", record.get("count")),
                ("vibeic__spare__inserted", measured.get("inserted")),
                ("vibeic__spare__tieoff__candidates", measured.get("tieoff_candidates")),
                ("vibeic__spare__tieoff__connected", measured.get("tieoff_connected")),
                ("vibeic__spare__pg__unconnected", measured.get("pg_unconnected")),
                ("vibeic__spare__check_placement__violations",
                 measured.get("check_placement_violations"))):
            if isinstance(value, int):
                metrics[key] = value
        violations = measured.get("check_placement_violations")
        if not isinstance(violations, int) or violations:
            # Same contract as OpenROAD.DetailedPlacement: an illegal database
            # never leaves the step. The record above stays for the reader.
            raise StepError(
                f"{self.id}: check_placement reported {violations!r} "
                f"violation(s) after spare insertion; see {self._report_path()}")
        return views, metrics


# Step 24 (lane mig101): the IR gate and the fork's transient solve.
from .ir_drop import IRDropChecker, TransientIR  # noqa: E402,F401

__all__ += ["IRDropChecker", "TransientIR"]
