"""vibe-ic#2193 — the rtl_gen hand-off must serve the skill's BYTES, not a name.

`design_one_shot_runner.step_rtl_gen` ends its authoring hand-off with
"AI invokes skill `spec-to-rtl`". A name carries no tree, so the author's skill
loader resolves it against whatever plugin is INSTALLED on the host —
`~/.claude/plugins/cache/vibe-ic-marketplace/vibe-ic/<version>/skills/...` —
which has nothing to do with the clone being measured.

MEASURED at base e2b3c08170b5 (tree 75d478b34c17, v1.19.43) on TWO hosts that
served two DIFFERENT versions of the same document:

    served install            8HD-6: cache 1.14.8      8hd-3: cache 1.14.31
    skills, tree vs served    70 / 70, membership IDENTICAL on both
    SKILL.md differing        12 of 70 on both
    skills/spec-to-rtl        tree 14 `## ` sections / served 12 on both

The two sections the served copy lacks are the ones written FOR the open
failures `#2089` and `#2081`; `#2081` is open against the very design the
hand-off was measured on. So the channel that exists to prevent that failure
is the one channel the author is not given, and the run record — which already
pins the image digest, the base sha, the tree sha and the plugin version — was
silent about the one input that wrote the RTL.

`lessons.md` in `_stage_author_knowledge_digests` already solves exactly this,
correctly, by copying the tree's own bytes into `phase2/stage1/`. The skill is
the same kind of input; `_stage_fallback_skill` now gives it the same treatment.

BOTH DIRECTIONS. `test_..._reachable_...` and `test_..._enumerates_...` are the
substantive controls: each OBSERVES A VALUE against the pre-fix code (0 of 14
sections reachable over ~400 KB of really-staged documents; 0 of N hand-off
sites carrying a path) rather than merely noticing that a key is absent.

ADVISORY, not blocking: staging never turns a WAIVE into a FAIL. The tree not
shipping the named skill is DISCLOSED loudly (a named `..._unstaged_reason` and
a stderr line), never a silent redirect to the installed copy.
"""
import ast
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

PROG_DIR = Path(__file__).resolve().parent.parent
if str(PROG_DIR) not in sys.path:
    sys.path.insert(0, str(PROG_DIR))

import _hostpaths                                              # noqa: E402

RUNNER_SRC = PROG_DIR / "design_one_shot_runner.py"

# Any class whose registry entry is rtl_gen=null + fallback_skill=spec-to-rtl.
# Read from the registry rather than hardcoded, so a registry edit cannot leave
# this module asserting about a class that no longer hands off.
_CLASS = "crypto_accelerator"


def _registry_classes_with_a_skill_handoff():
    reg = json.loads((PROG_DIR / "ic_class_registry.json").read_text())
    return [c["name"] for c in reg["classes"]
            if c.get("rtl_gen") is None and c.get("fallback_skill")]


def _tree_skill(name: str) -> Path:
    """THIS tree's copy of a skill, addressed the way the runner addresses it."""
    return PROG_DIR.parent / "skills" / name / "SKILL.md"


def _waive(td: Path, ic_class: str = _CLASS):
    from design_one_shot_runner import step_rtl_gen
    docs = td / "phase1" / "generated_docs"
    docs.mkdir(parents=True)
    (docs / "L1_DATASHEET.json").write_text(json.dumps({"part_name": "widget"}))
    (docs / "L2_FRS.json").write_text(json.dumps(
        {"notes": "A block that folds a message into a digest."}))
    return step_rtl_gen(td, ic_class)


def _reachable_text(res) -> tuple:
    """(text, [(key, path, size)]) — everything the hand-off actually delivers.

    This is the author's whole world: the hand-off message plus every file it
    names that really exists. Nothing else is reachable by an agent that
    follows the hand-off literally.
    """
    blob = [str(res.detail)]
    named = []
    for k, v in sorted((res.extras or {}).items()):
        if isinstance(v, str) and "/" in v:
            f = Path(v)
            if f.is_file():
                named.append((k, str(f), f.stat().st_size))
                blob.append(f.read_text(errors="ignore"))
    return "\n".join(blob), named


# ── guard the guard ──────────────────────────────────────────────────────
def test_the_registry_still_has_classes_that_hand_off_to_a_skill():
    """A registry with no skill hand-off would make every test below vacuous."""
    names = _registry_classes_with_a_skill_handoff()
    assert len(names) >= 5, names
    assert _CLASS in names, names
    assert _tree_skill("spec-to-rtl").is_file(), "this tree ships no spec-to-rtl"


# ── T1 — the message names a readable path ───────────────────────────────
def test_the_handoff_message_names_a_readable_path_for_the_skill():
    with tempfile.TemporaryDirectory() as td:
        res = _waive(Path(td))
        assert res.status == "WAIVED", res.detail
        p = (res.extras or {}).get("fallback_skill_path") or ""
        observed = (bool(p), Path(p).is_file() if p else False,
                    bool(p) and p in str(res.detail))
        assert observed == (True, True, True), (
            "(path named, path readable, path in the text the author reads) "
            f"= {observed}; the hand-off says "
            f"{(res.extras or {}).get('fallback_skill')!r} and the record "
            f"carries {sorted((res.extras or {}).keys())}")


# ── T2 — the bytes are THIS tree's, not the installed plugin's ───────────
def test_the_staged_skill_is_this_trees_bytes():
    with tempfile.TemporaryDirectory() as td:
        res = _waive(Path(td))
        skill = (res.extras or {}).get("fallback_skill")
        staged = Path((res.extras or {}).get("fallback_skill_path") or "")
        served = (hashlib.sha256(staged.read_bytes()).hexdigest()
                  if staged.is_file() else "(the hand-off staged no document)")
        tree = hashlib.sha256(_tree_skill(skill).read_bytes()).hexdigest()
        assert served == tree, (
            f"the document served for skill {skill!r} is {served}; this "
            f"tree's copy is {tree}")


# ── T3 — THE SUBSTANTIVE CONTROL: what the author can actually reach ─────
def test_every_section_of_the_trees_skill_is_reachable_from_the_handoff():
    """Reads the REAL in-repo skill (a checked-in artefact, #400) and measures
    coverage over the documents the hand-off really staged — so pre-fix this
    reports an observed 0 of 14 over ~400 KB of real files, not an absent key.
    """
    real = _hostpaths.require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic",
        "skills", "spec-to-rtl", "SKILL.md")
    titles = [ln[3:].strip() for ln in real.read_text(errors="ignore").splitlines()
              if ln.startswith("## ")]
    assert len(titles) >= 10, f"only {len(titles)} sections — is this the skill?"
    with tempfile.TemporaryDirectory() as td:
        res = _waive(Path(td))
        text, named = _reachable_text(res)
        assert named, "the hand-off staged nothing at all"
        reachable = [t for t in titles if t in text]
        assert reachable == titles, (
            f"{len(reachable)} of {len(titles)} sections of the skill the "
            f"hand-off names are reachable from it, over the "
            f"{len(named)} document(s) / {len(text)} bytes it did deliver "
            f"({[k for k, _, _ in named]}); unreachable: "
            f"{[t for t in titles if t not in text][:4]}")


# ── T4 — the record says which document authored the RTL ─────────────────
def test_the_record_pins_the_sha256_of_the_document_that_authored():
    with tempfile.TemporaryDirectory() as td:
        res = _waive(Path(td))
        e = res.extras or {}
        raw = _tree_skill(e["fallback_skill"]).read_bytes()
        observed = (e.get("fallback_skill_sha256", "(no such field)"),
                    e.get("fallback_skill_staged", "(no such field)"),
                    e.get("fallback_skill_bytes", "(no such field)"))
        assert observed == (hashlib.sha256(raw).hexdigest(), True, len(raw)), (
            "the record pins the image digest, the base sha, the tree sha and "
            "the plugin version, and says this about the document that "
            f"authored the RTL: {observed}. Record = {sorted(e.keys())}")


# ── T5 — degrade LOUDLY when the tree does not ship the named skill ──────
def test_a_skill_this_tree_does_not_ship_is_disclosed_not_silently_redirected(
        monkeypatch, capsys):
    """The other direction of the same contract. A `fallback_skill` naming a
    skill this tree has no copy of must produce a named refusal record — a
    silent decline reads downstream as 'nothing needed doing', and here it
    would read as 'the author got this tree's document' when they did not.
    """
    import design_one_shot_runner as dosr
    with tempfile.TemporaryDirectory() as empty:
        # raising=False so the PRE-FIX tree, which has no SKILLS_DIR at all,
        # still RUNS this test and answers it wrongly. A control that dies of
        # AttributeError has observed nothing about the hand-off.
        monkeypatch.setattr(dosr, "SKILLS_DIR", Path(empty), raising=False)
        with tempfile.TemporaryDirectory() as td:
            res = _waive(Path(td))
            e = res.extras or {}
            detail, err = str(res.detail), capsys.readouterr().err
            observed = (
                e.get("fallback_skill_staged", "(no such field)"),
                "spec-to-rtl" in (e.get("fallback_skill_unstaged_reason") or ""),
                "spec-to-rtl" in (e.get("fallback_skill_source") or ""),
                "fallback_skill_path" in e,
                "NOT IN THIS TREE" in detail,
                "INSTALLED" in detail,
                "NOT staged" in err)
            assert observed == (False, True, True, False, True, True, True), (
                "(staged flag, reason names the skill, source names the skill, "
                "a path was nevertheless claimed, the text warns the author, "
                "the text says the name resolves to the INSTALLED copy, "
                f"stderr said so) = {observed}; record = {sorted(e.keys())}")


# ── T6 — THE SECOND SUBSTANTIVE CONTROL: no branch may forget ────────────
def test_every_authoring_handoff_in_step_rtl_gen_stages_the_skill():
    """Derived from the tree by AST, not from a hand-written list or a window.

    `_stage_author_knowledge_digests` exists because the digests were staged in
    ONE of three hand-off branches — "the knowledge an author receives must
    depend on the fact that it is authoring, never on which branch happened to
    notice". The skill is the same kind of input, so the same rule has to hold
    for it: every `extras` dict literal in the rtl_gen path that names a
    non-None `fallback_skill` must also carry the staging, either as explicit
    keys or as the `**_sk_extras` spread.

    NOT A TEXT WINDOW. The first draft of this test read `src[i:i+1400]` after
    each match. A fixed source-text window measures COMMENT LENGTH, not code:
    one added paragraph between the key and the spread turns it red, and an
    unrelated neighbour drifting into the window turns it falsely green. The
    dict literal is asked directly instead, so comments cannot move the answer.

    The hand-off branches live in `_step_rtl_gen_bound`, not in the thin
    `step_rtl_gen` wrapper — both are taken from the AST rather than named, an
    earlier draft that read only the wrapper found 0 sites and called the
    runner clean.
    """
    tree = ast.parse(RUNNER_SRC.read_text(errors="ignore"))
    fns = [n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
           and "rtl_gen" in n.name and not n.name.startswith("test_")]
    assert [f.name for f in fns] != [], "no rtl_gen function in the runner"

    def _names_a_skill(d: ast.Dict):
        """(line, source of the value) for a non-None `fallback_skill` key."""
        for k, v in zip(d.keys, d.values):
            if not (isinstance(k, ast.Constant) and k.value == "fallback_skill"):
                continue
            if isinstance(v, ast.Constant) and v.value is None:
                return None                    # a deliberate no-route
            return (getattr(k, "lineno", -1), ast.unparse(v))
        return None

    def _stages(d: ast.Dict) -> bool:
        for k, v in zip(d.keys, d.values):
            if k is None:                      # `**something`
                if isinstance(v, ast.Name) and v.id.endswith("_sk_extras"):
                    return True
                continue
            if (isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                    and k.value in ("fallback_skill_path",
                                    "fallback_skill_staged")):
                return True
        return False

    sites = []
    for fn in fns:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Dict):
                continue
            named = _names_a_skill(node)
            if named is None:
                continue
            sites.append((named[0], named[1], _stages(node)))
    sites.sort()
    assert len(sites) >= 5, (
        f"only {len(sites)} authoring hand-off(s) found in the rtl_gen path — "
        f"the scanner has gone blind, not the runner")
    staging = [(ln, v) for ln, v, ok in sites if ok]
    every = [(ln, v) for ln, v, _ in sites]
    assert staging == every, (
        f"{len(staging)} of {len(every)} authoring hand-offs in the rtl_gen "
        f"path stage the skill they name; these do not (line, value): "
        f"{[x for x in every if x not in staging]}")
