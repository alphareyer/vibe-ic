#!/usr/bin/env python3
"""Step 1: every elaborated catalog-IP instance carries its synth-safe values.

A catalog manifest may declare ``synth_safe_params`` (serv: ``sim=0``, #492).
With the unsafe value Yosys aborts synthesis, or elaborates a simulation-only
block. The catalog-glue author is told to pin the value at the instantiation;
until now nothing checked that it did.

The judge is Yosys, not a regex. ``read_verilog -sv`` (LibreLane's front end
when ``USE_SLANG`` is off), then ``hierarchy -check -top <L9 top>`` and
``write_json``. After ``hierarchy`` each module that synthesis would reach
exists once per distinct parameter set, and its ``parameter_default_values``
are the values that instance really gets -- whether they came from the glue,
an intermediate wrapper or the module's own default. A module no path
reaches is not in the netlist and is not judged.

A module belongs to a pulled IP when its ``src`` names one of the files
``ip_catalog_pull`` copied for that IP. ``read_slang`` is not used: it
flattens the design and writes no per-module parameter values (measured on
the 0.3.79 image).

LibreLane's ``SYNTH_PARAMETERS`` reaches only the top module (``chparam
-set <p> <v> <top>``). When the catalogued IP is itself the top,
``librelane_contract.emit_synthesis_config`` pins the value there; this gate
still judges the RTL as the direct synthesis path elaborates it.

Verdicts: PASS (rc 0), FAIL (rc 1), NOT_APPLICABLE (rc 0: no pulled IP
declares a synth-safe parameter), NOT_MEASURED (rc 1: no top, or Yosys did
not elaborate). NOT_MEASURED exits 1 so no rc-reading consumer credits it:
flow_compliance_check reads rc 2 as a non-verdict it may promote to
VACUOUS_PASS.

chip-AGNOSTIC: parameter names and values come only from the manifests.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json  # noqa: E402
import instrument_calibration as _calibration  # noqa: E402

PROGRAM = "catalog_synth_safe_params_check"
DECLARATION_REL = "plugin_output/declaration.json"
REPORT_REL = "reports/phase2/gates/catalog_synth_safe_params.json"
YOSYS_JSON_REL = "reports/phase2/gates/catalog_synth_safe_params.yosys.json"
_PULLED = ("PASS", "PARTIAL")


def pulled_ips(project: Path) -> list[dict[str, Any]]:
    """IPs ``ip_catalog_pull`` copied, with their manifests' synth-safe values."""
    import ip_catalog_query
    path = project / DECLARATION_REL
    try:
        declaration = json.loads(path.read_text()) if path.is_file() else {}
    except (OSError, ValueError):
        declaration = {}
    used = declaration.get("ip_catalog_used") if isinstance(declaration, dict) else None
    manifests = {m.get("ip_name"): m for m in ip_catalog_query.load_manifests()}
    out = []
    for row in used if isinstance(used, list) else []:
        if not isinstance(row, dict) or row.get("status") not in _PULLED:
            continue
        manifest = manifests.get(row.get("ip_name")) or {}
        params = {}
        for entry in manifest.get("synth_safe_params") or []:
            if isinstance(entry, dict) and isinstance(entry.get("param"), str) \
                    and "synth_safe_value" in entry:
                params[entry["param"]] = entry["synth_safe_value"]
        files = sorted({Path(str(f.get("dest"))).name
                        for f in row.get("files_copied") or []
                        if isinstance(f, dict) and f.get("dest")})
        out.append({"ip_name": row.get("ip_name"), "files": files, "params": params,
                    "manifest": manifest.get("_manifest_path")})
    return out


def top_synth_parameters(project: Path, top: str) -> tuple[list[str], str] | None:
    """``SYNTH_PARAMETERS`` for a top that IS a pulled catalog IP's module.

    LibreLane applies these with ``chparam -set <p> <v> <top>``, which reaches
    the top alone; an IP instantiated below the top is pinned by its glue and
    judged by ``run``. Returns ``(["p=v", ...], provenance)`` or None.
    """
    rtl = project / "phase2/stage1/rtl"
    for ip in pulled_ips(project):
        if not ip["params"]:
            continue
        for name in ip["files"]:
            path = rtl / name
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
            if re.search(r"\bmodule\s+" + re.escape(top) + r"\b", text):
                pins = [f"{k}={v}" for k, v in sorted(ip["params"].items())]
                return pins, (f"{ip['manifest'] or ip['ip_name']}.synth_safe_params "
                              f"(top {top} is defined in pulled {name})")
    return None


def _value(raw: Any) -> Any:
    """A Yosys JSON parameter value: bit string -> int, else the string."""
    if isinstance(raw, int):
        return raw
    text = str(raw)
    if re.fullmatch(r"[01]+", text):
        return int(text, 2)
    if re.fullmatch(r"[01xz]+", text):
        return None  # an x/z bit is not a value anybody pinned
    return text[:-1] if text.endswith(" ") else text


def judge(netlist: dict, ips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (elaborated module of a pulled IP, synth-safe parameter).

    ``netlist`` is Yosys's ``write_json`` after ``hierarchy -check -top``.
    """
    _calibration.assert_calibrated(f"{PROGRAM}::judge")
    rows = []
    for name, module in sorted((netlist.get("modules") or {}).items()):
        attrs = module.get("attributes") or {}
        src = str(attrs.get("src", "")).split("|")[0].rsplit(":", 1)[0]
        values = module.get("parameter_default_values") or {}
        for ip in ips:
            if Path(src).name not in ip["files"]:
                continue
            for param, safe in ip["params"].items():
                if param not in values:
                    continue
                got = _value(values[param])
                rows.append({"ip_name": ip["ip_name"], "module": name,
                             "hdlname": attrs.get("hdlname", name), "param": param,
                             "value": got, "synth_safe_value": safe,
                             "safe": got == safe})
    return rows


def _yosys(project: Path, image: str, script: str, out_dir: Path):
    """Host yosys, else the EDA image with only ``out_dir`` writable."""
    import shutil
    import subprocess
    if shutil.which("yosys"):
        command = ["yosys", "-q", "-p", script]
    elif shutil.which("docker"):
        root, out = str(project.resolve()), str(out_dir.resolve())
        command = ["docker", "run", "--rm", "--network", "none", "-u",
                   f"{os.getuid()}:{os.getgid()}", "-v", f"{root}:{root}:ro",
                   "-v", f"{out}:{out}", "--entrypoint", "yosys", image,
                   "-q", "-p", script]
    else:
        raise FileNotFoundError("yosys and docker unavailable")
    return subprocess.run(command, cwd=project, capture_output=True, text=True,
                          check=False)


def run(project: Path, image: str) -> tuple[int, dict[str, Any]]:
    import p0_tool_frontend_check as frontend
    from _specrtl_common import rtl_source_files
    report: dict[str, Any] = {"program": PROGRAM, "project": str(project)}
    ips = [ip for ip in pulled_ips(project) if ip["params"]]
    report["ips"] = ips
    if not ips:
        report.update(verdict="NOT_APPLICABLE",
                      reason="no pulled catalog IP declares synth_safe_params")
        return 0, report
    top = frontend._top(project)
    files = [str(p.resolve()) for p in rtl_source_files(project)]
    if not top or not files:
        report.update(verdict="NOT_MEASURED",
                      reason="no L9 top_module or no RTL to elaborate")
        return 1, report
    out = project / YOSYS_JSON_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    script = ("read_verilog -sv " + " ".join(files) + f"; hierarchy -check -top {top}"
              f"; proc; write_json {out.resolve()}")
    try:
        result = _yosys(project, image, script, out.parent)
    except FileNotFoundError as exc:
        report.update(verdict="NOT_MEASURED", reason=str(exc))
        return 1, report
    report["yosys"] = {"exit_code": result.returncode,
                       "log_tail": (result.stdout + result.stderr)[-2000:]}
    try:
        netlist = json.loads(out.read_text())
    except (OSError, ValueError):
        netlist = None
    if result.returncode or not isinstance(netlist, dict):
        last = [line for line in (result.stdout + result.stderr).splitlines()
                if line.strip()][-1:] or ["no output"]
        report.update(verdict="NOT_MEASURED",
                      reason=f"Yosys did not elaborate the RTL: {last[0][-300:]}")
        return 1, report
    rows = judge(netlist, ips)
    report["instances"] = rows
    unsafe = [r for r in rows if not r["safe"]]
    if unsafe:
        report.update(verdict="FAIL", reason="; ".join(
            f"{r['hdlname']} ({r['ip_name']}) elaborates {r['param']}={r['value']!r}, "
            f"synth-safe value is {r['synth_safe_value']!r}" for r in unsafe))
        return 1, report
    report.update(verdict="PASS", reason=(
        f"{len(rows)} elaborated IP module(s) carry their synth-safe values"
        if rows else "no module declaring a synth-safe parameter is reached from the top"))
    return 0, report


def main(argv: list[str] | None = None) -> int:
    import p0_tool_frontend_check as frontend
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", type=Path)
    parser.add_argument("--image", default=None)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    project = args.project.resolve()
    image = args.image or os.environ.get("VIBEIC_EDA_IMAGE", frontend.DEFAULT_IMAGE)
    rc, report = run(project, image)
    write_json(args.json or project / REPORT_REL, report)
    print(f"[{report['verdict']}] {PROGRAM}: {report['reason']}",
          file=sys.stderr if rc else sys.stdout)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
