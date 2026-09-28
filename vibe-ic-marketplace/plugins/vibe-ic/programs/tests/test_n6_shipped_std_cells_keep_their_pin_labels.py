"""N6 — the shipped GDS's standard cells carried NO pin labels.

MEASURED (spm x gf180mcuD, same-RTL arm, image vibeic-eda 0.3.83
`sha256:7a01d48e…`): the delivered `spm.gds` has 36 leaf cells and 0 of them
carry a single text; LibreLane's stream of the same library labels all 20 of
its cells (34/10 pin labels, 21/10 and 204/10 well labels, 63/63 library
texts). An external Magic-extract + Netgen LVS of the vibe-ic GDS therefore saw
anonymous device nodes (`a_36_68#`, `VSUBS`, `w_n86_352#`) where every cell's
pins are, and failed pin matching in 28 cells.

WHERE THE LABELS WENT. Not the stream: the runner's own Magic stream-out of the
run's routed DEF, re-run in the same image, labels 31 of 31 cells. The
manufacturing-grid pass (`_GDS_GRID_SNAP_PY`, #600) rebuilds every layer of
every cell through `pya.Region`, which carries polygons only, and carried back
the TOP cell's texts alone -- measured on that same GDS: 31 labelled cells in,
0 out. The child texts were dropped on purpose (#2181): a FLATTEN lifts a
library cell's labels into the top, where `top_lvl_pins` promotes each labelled
net to a formal pin (5,655 on spm). That hazard is real but it belongs to the
flatten, and the shipped spm GDS is never flattened (the Magic path runs no
layer merge; the merge flattens only when its deck probe says it must).

THE RULE NOW. The snap keeps EVERY cell's texts. A child cell's texts are
cleared in the working layout immediately before each flatten -- the snap's
exotic-transform fallback and the layer merge's flatten branch -- and nowhere
else, so a flattened GDS still carries only the top's own labels (byte-for-byte
the old behaviour) while a hierarchical one keeps the library's pin labels.

EVERY FLATTEN, NOT TWO. The rule holds only if it holds at every flatten the
labelled GDS can reach, so the last tests enumerate them from the code rather
than by name: each KLayout `Cell.flatten(...)` a shipped program runs or embeds,
and each Magic `flatten` in a shipped source that also reads GDS. The Magic
ones are the device-level extraction routes the lvs-triage skill sends agents
to (the MCP `eda_extraction` tool and `magic_port_extract_emit` Route A); a
bare Magic flatten copies every library label into the flat cell under an
instance-prefixed name and `port makeall` promotes each to a top port.
MEASURED in the pinned image (magic 8.3.684), eda_extraction's script on a top
with two labelled gf180mcu_fd_sc_mcu7t5v0__inv_1 instances: bare -> 11 library
pins promoted; `flatten -dotoplabels` -> `.subckt chip_flat TOPPIN VSUBS`.

The behavioural tests execute the runner's own scripts under `pya` and skip
where KLayout's Python module is absent; run them in the pinned image. The
structural test below them runs everywhere.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

try:
    import pya  # noqa: F401
except ImportError:                               # the host has no KLayout
    pya = None

PIN = (34, 10)          # a label-purpose layer, as a library ships it
MET = (34, 0)           # the drawing layer the pin label sits on
TOPLBL = (36, 10)


def _need_pya():
    return pytest.importorskip("pya")


def _library_design(tmp_path, *, transform=None) -> Path:
    """A top with one instance of a 'library cell' that carries pin labels,
    one of them off the 5 nm grid, plus the top's own port label."""
    ly = pya.Layout()
    ly.dbu = 0.001
    met = ly.layer(*MET)
    pin = ly.layer(*PIN)
    top_l = ly.layer(*TOPLBL)
    cell = ly.create_cell("lib__inv_1")
    cell.shapes(met).insert(pya.Box(0, 0, 400, 1000))
    cell.shapes(pin).insert(pya.Text("A", pya.Trans(pya.Vector(100, 500))))
    cell.shapes(pin).insert(pya.Text("ZN", pya.Trans(pya.Vector(303, 700))))
    top = ly.create_cell("chip")
    top.insert(pya.CellInstArray(
        cell.cell_index(), transform or pya.Trans(pya.Vector(1000, 0))))
    top.shapes(top_l).insert(pya.Text("clk", pya.Trans(pya.Vector(0, 0))))
    p = tmp_path / "in.gds"
    ly.write(str(p))
    return p


def _run(script: str, gds_in: Path, gds_out: Path, **env) -> None:
    saved = {k: os.environ.get(k) for k in ("GDS_IN", "GDS_OUT", "MFG_GRID_UM",
                                            "KEEP_HIERARCHY", *env)}
    try:
        os.environ.update(GDS_IN=str(gds_in), GDS_OUT=str(gds_out),
                          MFG_GRID_UM="0.005")
        os.environ.pop("KEEP_HIERARCHY", None)
        os.environ.update({k: str(v) for k, v in env.items()})
        exec(compile(script, "<runner script>", "exec"), {"__name__": "__main__"})
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _texts(gds: Path):
    """{cell name: sorted [(string, x, y)]} for every cell with a text."""
    ly = pya.Layout()
    ly.read(str(gds))
    out = {}
    for c in ly.each_cell():
        got = []
        for li in ly.layer_indexes():
            for s in c.shapes(li).each():
                if s.is_text():
                    got.append((s.text_string, s.text.trans.disp.x,
                                s.text.trans.disp.y))
        if got:
            out[c.name] = sorted(got)
    return out


def test_the_grid_snap_keeps_every_cells_pin_labels(tmp_path):
    """RED ON MAIN: main's snap returns the library cell with 0 labels."""
    _need_pya()
    out = tmp_path / "out.gds"
    _run(R._GDS_GRID_SNAP_PY, _library_design(tmp_path), out)
    got = _texts(out)
    assert got.get("lib__inv_1") == [("A", 100, 500), ("ZN", 305, 700)], (
        "the grid snap must carry a library cell's pin labels through its "
        "Region round-trip (the off-grid anchor 303 snapped to 305, "
        f"nothing else moved); got {got}")
    assert got.get("chip") == [("clk", 0, 0)], got


def test_the_snap_flatten_fallback_does_not_lift_library_labels(tmp_path):
    """A non-orthogonal placement makes the snap flatten. A flattened
    library label would become a TOP label, which is the 5,655-pin shape."""
    _need_pya()
    out = tmp_path / "out.gds"
    _run(R._GDS_GRID_SNAP_PY,
         _library_design(tmp_path, transform=pya.ICplxTrans(1.0, 30.0, False,
                                                            1000, 0)),
         out)
    got = _texts(out)
    assert list(got) == ["chip"] and got["chip"] == [("clk", 0, 0)], (
        f"after a flatten only the top's own labels may remain: {got}")


def test_the_layer_merge_flatten_does_not_lift_library_labels(tmp_path):
    """RED ON MAIN: main's merge flattens a labelled library cell and puts
    its pin labels in the top."""
    _need_pya()
    out = tmp_path / "out.gds"
    _run(R._GDS_LAYER_MERGE_PY, _library_design(tmp_path), out)
    got = _texts(out)
    assert list(got) == ["chip"] and got["chip"] == [("clk", 0, 0)], (
        f"a flattened GDS carries only the top's own labels: {got}")


def test_the_layer_merge_keeping_hierarchy_keeps_library_labels(tmp_path):
    _need_pya()
    out = tmp_path / "out.gds"
    _run(R._GDS_LAYER_MERGE_PY, _library_design(tmp_path), out,
         KEEP_HIERARCHY="1")
    got = _texts(out)
    assert got.get("lib__inv_1") == [("A", 100, 500), ("ZN", 303, 700)], got
    assert got.get("chip") == [("clk", 0, 0)], got


def _flatten_guarded(script: str, clear_marker: str) -> None:
    """Every `.flatten(` in `script` sits after a child-text clear that is
    closer to it than any earlier flatten."""
    pos = 0
    n = 0
    while True:
        i = script.find(".flatten(", pos)
        if i < 0:
            break
        j = script.rfind(clear_marker, 0, i)
        k = script.rfind(".flatten(", 0, i)
        assert j >= 0 and j > k, (
            f"a flatten at offset {i} is not preceded by `{clear_marker}`; a "
            "library cell's pin labels would be lifted into the top")
        n += 1
        pos = i + 1
    assert n, "no flatten found; this guard would be vacuous"


def test_every_flatten_is_preceded_by_the_child_text_clear():
    """Host-runnable: the top-only rule lives at each flatten, and the snap's
    per-cell text collection is unconditional."""
    # The CALL (`...()` then a newline), never the `def _drop_child_texts():`
    # line, which would satisfy a bare name search from above every flatten.
    _flatten_guarded(R._GDS_GRID_SNAP_PY, "_drop_child_texts()\n")
    _flatten_guarded(R._GDS_LAYER_MERGE_PY, "clear(pya.Shapes.STexts)")
    body = R._GDS_GRID_SNAP_PY.split("def _snap_local_shapes")[1].split(
        "return n")[0]
    assert "_texts = [s_.text for s_ in sh.each() if s_.is_text()]" in body, (
        "the snap must collect EVERY cell's texts, not only the top's")


# ── Every flatten the plugin ships, enumerated from the code ────────────────

PLUGIN = PROG.parent
INDEX_JS = PLUGIN / "mcp-eda" / "src" / "index.js"

#: A layout flatten takes arguments (`Cell.flatten(levels, prune)`,
#: `Layout.flatten(cell, levels, prune)`); a netlist's or an array's takes none.
_KL_FLATTEN = re.compile(r"\.flatten\(\s*[^)\s]")
_TEXT_CLEAR = re.compile(r"\.clear\(\s*(?:pya|db|klayout\.db)\.Shapes\.STexts\s*\)")
_DROP_CALL = re.compile(r"^\s*_drop_child_texts\(\)\s*$")
#: A Magic `flatten [-options] [<name>]` command. In a Python string constant's
#: VALUE it starts a line (a `\\n` escape is a real newline there); in other
#: sources it starts a line, follows a `\\n` escape, or opens a quoted string
#: (`= "flatten ...`, `("flatten ...`). The name may be absent when the string
#: ends there and the name is concatenated on.
_FLATTEN_TAIL = (r"""[ \t]*flatten((?:[ \t]+-\w+)*)"""
                 r"""(?:[ \t]+([^\s;"'`\\)]+)|[ \t]*(?:["']|$))""")
_MAGIC_FLATTEN_PY = re.compile(r"^" + _FLATTEN_TAIL)
_MAGIC_FLATTEN_SRC = re.compile(
    r"""(?:^|\\n|[(=,+\[:?][ \t]*["'])""" + _FLATTEN_TAIL)


def _shipped_sources(*suffixes):
    """Every source file the plugin ships with one of `suffixes` -- programs,
    the MCP server, skills, hooks, tools -- test code excluded."""
    skip = {"tests", "test", "node_modules", "__pycache__", ".pytest_cache"}
    for p in sorted(PLUGIN.rglob("*")):
        rel = p.relative_to(PLUGIN).parts
        if (p.suffix in suffixes and p.is_file() and not skip & set(rel)
                and not p.name.startswith("test_") and p.name != "conftest.py"):
            yield p


def _is_code(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith("#")


def _klayout_sites_in(src: str):
    """(script, 0-based line, kind) for every layout flatten in a module:
    kind "script" for one inside an embedded script (a string constant the
    program runs under KLayout), "call" for one the module itself makes, with
    the enclosing function as its script."""
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and ".flatten(" in node.value:
            lines = node.value.splitlines()
            out += [(node.value, i, "script") for i, ln in enumerate(lines)
                    if _is_code(ln) and _KL_FLATTEN.search(ln)]
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "flatten" and node.args:
            owner = min((f for f in funcs
                         if f.lineno <= node.lineno <= f.end_lineno),
                        key=lambda f: f.end_lineno - f.lineno, default=None)
            body = ast.get_source_segment(src, owner) if owner else src
            first = owner.lineno if owner else 1
            out.append((body, node.lineno - first, "call"))
    return out


def _klayout_verdict(script: str, idx: int, kind: str):
    """None when no library text can reach the flatten at line `idx`, else why.

    (a) a child-text clear sits between it and any earlier flatten, or
    (b) an embedded script that reads no layout and writes no text, so it
        has no text to lift."""
    lines = script.splitlines()
    for ln in reversed(lines[:idx]):
        if not _is_code(ln):
            continue
        if _KL_FLATTEN.search(ln):
            break
        if _TEXT_CLEAR.search(ln):
            return None
        if _DROP_CALL.match(ln):
            helper = script.split("def _drop_child_texts", 1)
            if len(helper) == 2 and _TEXT_CLEAR.search(
                    helper[1].split("\ndef ", 1)[0]):
                return None
            return "calls _drop_child_texts() but no such helper clears STexts"
    if kind == "script" and ".read(" not in script and "Text(" not in script:
        return None
    return "no child-text clear precedes it and its layout can carry texts"


def test_the_klayout_flatten_census_calibrates():
    """The scanner must flag the shape main shipped and pass the guarded one,
    in both an embedded script and a direct call."""
    bare = ('S = r"""\nly = pya.Layout(); ly.read("x.gds")\n'
            'for tc in ly.top_cells():\n    tc.flatten(-1, True)\n"""\n')
    guarded = bare.replace(
        "for tc in", "for c in ly.each_cell():\n"
        "    c.shapes(0).clear(pya.Shapes.STexts)\nfor tc in")
    built = ('S = r"""\nly = pya.Layout(); t = ly.create_cell("T")\n'
             't.flatten(-1, True)\n"""\n')
    call = ("def f(ly):\n    top = ly.top_cell()\n    top.flatten(-1, True)\n")
    verdicts = {}
    for name, src in (("bare", bare), ("guarded", guarded), ("built", built),
                      ("call", call)):
        sites = _klayout_sites_in(src)
        assert len(sites) == 1, (name, sites)
        verdicts[name] = _klayout_verdict(*sites[0])
    assert verdicts["bare"] and verdicts["call"], verdicts
    assert verdicts["guarded"] is None and verdicts["built"] is None, verdicts


def test_every_klayout_flatten_is_guarded_or_has_no_text_to_lift():
    """Enumerated from the code: every layout flatten a shipped program runs
    or embeds either clears child texts first or holds a layout with no text."""
    seen, bad = [], []
    for p in _shipped_sources(".py"):
        src = p.read_text(errors="replace")
        if ".flatten(" not in src:
            continue
        for script, idx, kind in _klayout_sites_in(src):
            seen.append((p.name, script))
            why = _klayout_verdict(script, idx, kind)
            if why:
                bad.append(f"{p.relative_to(PLUGIN)} [{kind}] "
                           f"`{script.splitlines()[idx].strip()}`: {why}")
    # The census must see the two flattens the behavioural tests above drive.
    scripts = {sc for _n, sc in seen}
    assert R._GDS_GRID_SNAP_PY in scripts and R._GDS_LAYER_MERGE_PY in scripts, (
        "the census went blind: it no longer finds the runner's own flattens")
    assert not bad, ("a flatten would lift library pin labels into the top:\n"
                     + "\n".join(bad))


def test_the_rcx_clip_flatten_keeps_only_the_top_texts(tmp_path):
    """The rcx reference clip reads the routed DEF with every label producer
    off, so it has no texts today; its clear is exercised here on a layout
    that has them."""
    _need_pya()
    import rcx_field_solver_reference as X
    # The clip's own lines from its read through its flatten, run on a
    # layout whose library cell carries pin labels.
    body = X._CLIP_PY.split('ly.read(cfg["def"], opt)', 1)[1].split(
        "top.flatten(-1, True)", 1)[0] + "top.flatten(-1, True)\n"
    ly = pya.Layout()
    ly.read(str(_library_design(tmp_path)))
    g = {"db": pya, "ly": ly}
    exec(compile(body, "<clip>", "exec"), g)
    got = sorted(s.text_string for li in ly.layer_indexes()
                 for s in g["top"].shapes(li).each() if s.is_text())
    assert got == ["clk"], f"after the clip's flatten only the top's label: {got}"


def _magic_sites_in(text: str, suffix: str):
    """(line, snippet, options) for every Magic flatten a source emits. Python
    is read through its string constants (an f-string with `{}` for each
    field), so comments are never counted and `\\n`-joined scripts are split;
    other sources line by line, skipping comment lines."""
    pieces = []
    if suffix == ".py":
        tree = ast.parse(text)
        inner = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)
                 for v in n.values}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                    and id(n) not in inner:
                val = n.value
            elif isinstance(n, ast.JoinedStr):
                val = "".join(v.value if isinstance(v, ast.Constant) else "{}"
                              for v in n.values)
            else:
                continue
            pieces += [(n.lineno + i, ln) for i, ln in enumerate(val.splitlines())]
    else:
        pieces = [(i, ln) for i, ln in enumerate(text.splitlines(), 1)
                  if not ln.strip().startswith(("//", "*", "/*", "#"))]
    rx = _MAGIC_FLATTEN_PY if suffix == ".py" else _MAGIC_FLATTEN_SRC
    out = []
    for lineno, ln in pieces:
        for m in rx.finditer(ln):
            if m.group(2) != "class":          # netgen's `flatten class`
                out.append((lineno, ln.strip(), m.group(1).split()))
    return out


def _magic_flatten_sites():
    for p in _shipped_sources(".py", ".js", ".mjs", ".tcl", ".sh"):
        src = p.read_text(errors="replace")
        if "gds read" not in src:          # Magic reading a GDS
            continue
        for lineno, snippet, opts in _magic_sites_in(src, p.suffix):
            yield p, lineno, snippet, opts


def test_every_magic_flatten_over_a_gds_keeps_only_top_labels():
    """Enumerated from the code: every Magic `flatten` in a shipped source that
    reads GDS carries `-dotoplabels`. RED ON MAIN at both emitters."""
    # Calibration: main's two spellings, a `\\n`-joined script and a name
    # concatenated on are sites; prose and comments are not.
    for suffix, src, want in (
            (".py", 'out.append(f"flatten {top_cell}")\n', [[]]),
            (".js", "flatten ${top_cell}_flat\n", [[]]),
            (".py", 's = "gds read g\\nflatten t\\nload t\\n"\n', [[]]),
            (".js", 'const s = "gds read g\\nflatten t\\n";\n', [[]]),
            (".js", 'const s = "flatten " + top;\n', [[]]),
            (".py", 's = "flatten -dotoplabels " + top\n', [["-dotoplabels"]]),
            (".js", "// (`flatten <top>` -> `.subckt <top>_flat`)\n", []),
            (".js", 'd("a `flatten <top>` gives (`flatten x`)");\n', []),
            (".py", 'd = "the `nonorthogonal > 0` flatten fallback"\n', []),
            (".py", "# flatten t\n", [])):
        got = [o for _l, _s, o in _magic_sites_in(src, suffix)]
        assert got == want, (src, got)
    sites = list(_magic_flatten_sites())
    members = {p for p, *_ in sites}
    required = {INDEX_JS, PROG / "magic_port_extract_emit.py"}
    assert required <= members, (
        "the Magic flatten census must keep every known GDS-reading source "
        f"in scope; missing={sorted(map(str, required - members))}, "
        f"found={sorted(map(str, members))}")
    bare = [f"{p.relative_to(PLUGIN)}:{i} `{ln}`"
            for p, i, ln, opts in sites if "-dotoplabels" not in opts]
    assert not bare, ("a bare Magic flatten copies every library cell's pin "
                      "labels into the flat cell, and `port makeall` makes "
                      "each a top port:\n" + "\n".join(bare))


def test_eda_extraction_flattens_with_the_top_labels_only():
    """The MCP tool's own script: one flatten, `-dotoplabels`, into the cell
    it then loads and extracts."""
    src = INDEX_JS.read_text()
    block = re.search(r'server\.tool\(\s*"eda_extraction".*?^\);', src,
                      re.DOTALL | re.MULTILINE)
    assert block, "eda_extraction tool registration not found"
    script = re.search(r"const magicScript = `(.*?)`;", block.group(0),
                       re.DOTALL)
    assert script, "eda_extraction's Magic script not found"
    cmds = [ln.split() for ln in script.group(1).splitlines() if ln.strip()]
    flat = [c for c in cmds if c[0] == "flatten"]
    assert flat == [["flatten", "-dotoplabels", "${top_cell}_flat"]], flat
    assert ["load", "${top_cell}_flat"] in cmds[cmds.index(flat[0]):], cmds


def test_route_a_flattens_with_the_top_labels_only():
    import magic_port_extract_emit as M
    tcl = M.build_extraction_tcl("chip", "/g.gds", "/o.spice")
    flat = [ln.split() for ln in tcl.splitlines()
            if ln.split()[:1] == ["flatten"]]
    assert flat == [["flatten", "-dotoplabels", "chip"]], flat
