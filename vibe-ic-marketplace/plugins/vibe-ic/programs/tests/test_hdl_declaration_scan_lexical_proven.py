"""Source-bound controls for definition-time and parameter-fed regex scans."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import hdl_declaration_scan_strips_comments_check as G  # noqa: E402


_DEFINITION_EXPRESSIONS = {
    "default": "def inner(seed=rx.findall(code)):\n        return seed",
    "annotation": "def inner(seed: rx.findall(code)):\n        return seed",
    "decorator": "@retain(rx.findall(code))\n    def inner():\n        pass",
}


def _definition_source(kind, stripped):
    value = "_strip_comments(text)" if stripped else "text"
    definition = _DEFINITION_EXPRESSIONS[kind]
    return r'''
import re
def retain(matches):
    def decorate(fn):
        fn.matches = matches
        return fn
    return decorate
def build(text):
    rx = re.compile(r"\bmodule\s+(\w+)")
    code = VALUE
    DEFINITION
    return inner
'''.replace("VALUE", value).replace("DEFINITION", definition)


def _execute_definition(src, kind):
    import re

    namespace = {"_strip_comments": lambda text: re.sub(
        r"//[^\n]*|/\*.*?\*/", " ", text, flags=re.S)}
    exec(compile(src, "<definition-control>", "exec", dont_inherit=True), namespace)
    inner = namespace["build"]("// module phantom\nmodule actual; endmodule\n")
    if kind == "default":
        return inner.__defaults__[0]
    if kind == "annotation":
        return inner.__annotations__["seed"]
    return inner.matches


@pytest.mark.parametrize("kind", _DEFINITION_EXPRESSIONS)
def test_proven_definition_time_raw_scan_uses_the_enclosing_scope(kind):
    src = _definition_source(kind, stripped=False)
    observed = (_execute_definition(src, kind), G.scan_source(src, "m"))
    assert observed == (["phantom", "actual"], ["m::build::rx(code)"])


@pytest.mark.parametrize("kind", _DEFINITION_EXPRESSIONS)
def test_proven_stripped_definition_time_scan_stays_clean(kind):
    src = _definition_source(kind, stripped=True)
    assert (_execute_definition(src, kind), G.scan_source(src, "m")) == (
        ["actual"], [])


@pytest.mark.parametrize("stripped", [False, True])
def test_proven_flat_body_scan_preserves_both_directions(stripped):
    src = r'''
import re
def build(text):
    rx = re.compile(r"\bmodule\s+(\w+)")
    code = VALUE
    return rx.findall(code)
'''.replace("VALUE", "_strip_comments(text)" if stripped else "text")
    expected = [] if stripped else ["m::build::rx(code)"]
    assert G.scan_source(src, "m") == expected


def test_pep709_inline_dictcomp_keeps_the_scanner_lexically_bound():
    source = (PROGRAMS / "_a6_drc_authority.py").read_text(encoding="utf-8")
    assert isinstance(G.scan_source(source, "_a6_drc_authority"), list)


def test_pep709_comprehension_target_shadows_and_restores_outer_pattern():
    src = r'''
import re
rx = re.compile(r"\bmodule\s+(\w+)")
def detect(text):
    code = _strip_comments(text)
    values = [rx.findall(line) for line in code.splitlines()
              for rx in (re.compile(r"/container(?=/|$)"),)]
    return values, rx.findall(text)
'''
    assert G.scan_source(src, "m") == ["m::detect::rx(text)"]


@pytest.mark.parametrize("keyword", [False, True])
def test_proven_compiled_argument_reaches_only_its_actual_parameter(keyword):
    src = r'''
import re
def parse(text, pat):
    return pat.findall(text)
def request(text):
    pat = re.compile(r"\bmodule\s+(\w+)")
    return parse(text, ARGUMENT)
def unrelated(text):
    pat = re.compile(r"/container(?=/|$)")
    return pat.findall(text)
'''.replace("ARGUMENT", "pat=pat" if keyword else "pat")
    assert G.scan_source(src, "m") == ["m::parse::pat(text)"]


def test_proven_unrelated_local_compile_does_not_taint_a_parameter():
    src = r'''
import re
def parse(text, pat):
    return pat.findall(text)
def request(text):
    pat = re.compile(r"/container(?=/|$)")
    return parse(text, pat)
def unrelated(text):
    pat = re.compile(r"\bmodule\s+(\w+)")
    return text
'''
    assert G.scan_source(src, "m") == []


def test_proven_real_parameter_fed_raw_scan_precedes_producer_masking():
    """Trace real helper matches, then prove its real caller supplies the regex."""
    import re
    from _hostpaths import repo_path

    runner = repo_path("vibe-ic-marketplace", "plugins", "vibe-ic",
                       "programs", "design_one_shot_runner.py")
    src = runner.read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {"_rcvar_code_mask", "_rcvar_label_guarded", "_rcvar_sub_code_only"}
    helpers = [node for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in helpers} == names
    caller = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "step_reset_clock_variant_aliases")
    declaration = next(node for node in ast.walk(caller)
                       if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "decl_pat"
                               for t in node.targets))
    namespace = {"re": re, "List": list, "Tuple": tuple, "tgt": "probe"}
    exec(compile(ast.Module(body=helpers, type_ignores=[]), str(runner), "exec"),
         namespace)
    regex = eval(compile(ast.Expression(declaration.value), str(runner), "eval"),
                 namespace)
    seen = []

    class TracePattern:
        def finditer(self, text):
            matches = list(regex.finditer(text))
            seen.extend((match.start(), match.group(0)) for match in matches)
            return iter(matches)

    text = "// module probe is a comment\nmodule probe; endmodule\n"
    rewritten, count = namespace["_rcvar_sub_code_only"](
        text, TracePattern(), r"module\g<1>renamed", count=1)
    assert len(seen) == 2
    assert count == 1
    assert rewritten == ("// module probe is a comment\n"
                         "module renamed; endmodule\n")
    hits = [hit for hit in G.scan_source(src, "runner")
            if "::_rcvar_sub_code_only::" in hit]
    observed = (len(seen), count, hits)
    assert observed == (2, 1, ["runner::_rcvar_sub_code_only::pat(txt)"])
