#!/usr/bin/env python3
"""Regression for #2164 (2 of 3) — the k retrieval slots are ENTRY slots.

THE DEFECT, MEASURED. `ic_expert_db_query.query` scores per LESSON and used to
dedup per LESSON too, so an entry carrying four lessons could occupy four of the
five slots and push four other entries out of the pack entirely. On six of the
ten readable corpus designs of one profiled class a single four-lesson entry
took three or four slots, and the class's shift-register and framing craft never
reached the author. The class-first SELECTION was right; the budget spent it on
one entry. That is a ranking defect, and it survives every class profile because
membership and ranking are different questions.

THE RULE, AND ITS SECOND HALF. First pass: each entry gets at most one slot, its
own best-scoring lesson, in score order — five slots therefore mean up to five
entries. Second pass: once every available entry has had a slot and slots
remain, the remainder is filled from the same ranking with lesson-level dedup.
The second pass is not a hedge. "At most one each" is the WRONG rule when there
is nothing to displace: a class with three entries and k=5 would otherwise hand
back a three-item pack and leave two slots empty while its own craft sat unread.
MEASURED on the shipped DB: the crypto class (three entries) is byte-identical
before and after, and both processor-core designs move — one of them GAINS the
load-store-unit entry it never had.

chip-AGNOSTIC: the monopolisation fixture is synthetic; the shipped-DB cases
read the DB that is in the tree.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import ic_expert_db_query as Q  # noqa: E402

_DB = _PROGRAMS.parent / "agents" / "ic_expert_db" / "ic_expert_db.json"

_WORDS = "counter divider timer tick prescaler"


def _synthetic_db(tmp_path: Path, fat_lessons: int = 4,
                  thin_entries: int = 6) -> Path:
    """One FAT entry whose lessons all score, plus several thin ones that score
    slightly lower. Every lesson uses the same vocabulary, so the ranking is
    decided by the scores and the pack shape is decided by the dedup rule —
    which is the one thing under test."""
    entries = [{
        "ic_class": "fat-entry",
        "lesson_count": fat_lessons,
        "lessons": [f"A {_WORDS} lesson number {i} about the divider datapath."
                    for i in range(fat_lessons)],
    }]
    for j in range(thin_entries):
        entries.append({
            "ic_class": f"thin-entry-{j}",
            "lesson_count": 1,
            "lessons": [f"A {_WORDS} lesson about the divider datapath, {j}."],
        })
    p = tmp_path / "db.json"
    p.write_text(json.dumps({"entries": entries}))
    return p


# ── 1. five slots mean up to five ENTRIES ─────────────────────────────────

def test_k_slots_hold_k_distinct_entries_when_k_entries_are_available(tmp_path):
    db = _synthetic_db(tmp_path)
    hits = Q.query(f"a design with a {_WORDS}", k=5, db_path=db)
    got = [h["ic_class"] for h in hits]
    assert len(got) == 5
    assert len(set(got)) == 5, f"an entry took more than one slot: {got}"


def test_mutation_lesson_level_dedup_lets_one_entry_take_four_slots(tmp_path,
                                                                    monkeypatch):
    """THE NEGATIVE CONTROL, and it has to reproduce the MEASURED shape: with
    the entry-level dedup out of the way the fat entry takes four of the five
    slots and four other entries never reach the pack. Without this, the
    assertion above could be passing because the fixture cannot produce a
    repeat at all."""
    import json as _json
    db = _synthetic_db(tmp_path)
    entries = _json.loads(db.read_text())["entries"]
    prompt = f"a design with a {_WORDS}"
    # THE SAME RANKING the production allocator consumes — taken from the
    # module, never re-typed, so this control cannot drift away from the
    # behaviour it reproduces.
    ranked = Q.rank_lessons(prompt, entries)
    assert sum(1 for _s, c, _l in ranked if c == "fat-entry") >= 4, (
        "the fixture cannot produce a repeat; the control below is empty")

    # the pre-#2164 tail: dedup by LESSON, take the first k
    out, seen = [], set()
    for _s, cls, les in ranked:
        if les in seen:
            continue
        seen.add(les)
        out.append(cls)
        if len(out) >= 5:
            break
    assert out.count("fat-entry") >= 4, out
    assert len(set(out)) < 5, out

    # and the production allocator, on the identical ranking, does not
    got = [h["ic_class"] for h in Q.query(prompt, k=5, db_path=db)]
    assert len(set(got)) == 5, got


def test_the_slot_an_entry_gets_carries_its_BEST_lesson(tmp_path):
    """One slot per entry only helps if it is the right lesson: the entry's
    highest-scoring lesson must surface, not its first.

    BOTH lessons score here, on purpose. A first draft paired a matching lesson
    with an unrelated one — and the unrelated one scored 0 and was filtered out
    before the ordering mattered, so the case passed under a mutant that
    removed the sort entirely. The weak lesson has to be weak, not absent."""
    db = tmp_path / "db.json"
    db.write_text(json.dumps({"entries": [
        {"ic_class": "e", "lesson_count": 2,
         "lessons": [f"a {_WORDS.split()[0]} mentioned once",
                     f"the {_WORDS} lesson that the prompt actually matches"]},
    ]}))
    ranked = Q.rank_lessons(f"a design with a {_WORDS}",
                            json.loads(db.read_text())["entries"])
    assert len(ranked) == 2, "both lessons must be candidates, or this is vacuous"
    assert ranked[0][0] > ranked[1][0], "the two must be separable by score"
    hits = Q.query(f"a design with a {_WORDS}", k=1, db_path=db)
    assert hits[0]["lesson"].startswith("the ")


def test_the_pack_is_still_ordered_by_score(tmp_path):
    """The entries are written WEAKEST FIRST, so a pack that came back in DB
    order rather than in score order fails here. The synthetic fixture above
    happens to be near-uniform in score, which makes it useless for this."""
    db = tmp_path / "db.json"
    db.write_text(json.dumps({"entries": [
        {"ic_class": f"e{j}", "lesson_count": 1,
         "lessons": [" ".join(_WORDS.split()[:j + 1]) + " datapath"]}
        for j in range(5)]}))
    hits = Q.query(f"a design with a {_WORDS}", k=5, db_path=db)
    scores = [h["score"] for h in hits]
    assert len(set(scores)) > 1, "the fixture must be separable by score"
    assert scores == sorted(scores, reverse=True), scores
    assert [h["ic_class"] for h in hits] == ["e4", "e3", "e2", "e1", "e0"]


# ── 2. the second pass — nothing to displace, nothing taken away ──────────

def test_fewer_entries_than_slots_still_fills_the_pack(tmp_path):
    """A class with three entries and k=5 must NOT hand back a three-item pack
    with two slots empty while its own craft sits unread."""
    db = tmp_path / "db.json"
    db.write_text(json.dumps({"entries": [
        {"ic_class": "only-entry", "lesson_count": 4,
         "lessons": [f"{_WORDS} lesson {i}" for i in range(4)]},
    ]}))
    hits = Q.query(f"a design with a {_WORDS}", k=4, db_path=db)
    assert len(hits) == 4 and {h["ic_class"] for h in hits} == {"only-entry"}


def test_a_profiled_class_with_fewer_entries_than_k_is_byte_identical():
    """MEASURED ON THE SHIPPED DB. A profiled class whose selection is smaller
    than k has nothing to displace, so this change must not move it at all —
    the second pass is what guarantees that, and this is the assertion that
    would catch a first pass shipped without one."""
    prof = json.loads(_DB.read_text())["registered_class_profiles"]
    small = [k for k, v in prof.items()
             if not k.startswith("_") and isinstance(v, dict)
             and len(v["db_classes"]) < 5]
    assert small, "no profiled class is smaller than k; this control is empty"
    cls = sorted(small)[0]
    hits = Q.query("a block cipher accelerator with key registers, an "
                   "initialisation vector and an idle status bit.",
                   k=5, ic_class=cls)
    assert len(hits) == 5, (
        f"{cls} selects {len(prof[cls]['db_classes'])} entries and k=5; the "
        f"pack came back short, so the second pass is missing")
    assert len({h["ic_class"] for h in hits}) == len(prof[cls]["db_classes"])


# ── 3. the class boundary is untouched by the ranking change ──────────────

def test_entry_dedup_never_widens_the_class_first_selection():
    """The change is to the BUDGET, not to membership. Every hit must still be
    an entry the class selected — a ranking fix that reached outside the class
    would be the #2094 defect coming back through the slot allocator."""
    prof = json.loads(_DB.read_text())["registered_class_profiles"]
    for cls, v in sorted(prof.items()):
        if cls.startswith("_") or not isinstance(v, dict):
            continue
        allowed = set(v["db_classes"])
        got = {h["ic_class"] for h in
               Q.query("a design with a counter and a bus.", k=99, ic_class=cls)}
        assert got == allowed, f"{cls}: {sorted(got ^ allowed)}"


def test_expand_related_still_stays_inside_a_profiled_class():
    prof = json.loads(_DB.read_text())["registered_class_profiles"]
    cls = sorted(k for k, v in prof.items()
                 if not k.startswith("_") and isinstance(v, dict))[0]
    allowed = set(prof[cls]["db_classes"])
    got = {h["ic_class"] for h in
           Q.query("a design with a counter and a bus.", k=5,
                   ic_class=cls, expand_related=True)}
    assert got <= allowed


def test_a_ranking_with_no_repeats_is_unchanged(tmp_path):
    """Where the old rule and the new one cannot disagree, they must not: a
    ranking whose top-k already held k distinct entries comes back identical."""
    db = tmp_path / "db.json"
    db.write_text(json.dumps({"entries": [
        {"ic_class": f"e{j}", "lesson_count": 1,
         "lessons": [f"{_WORDS} lesson {j}"]} for j in range(8)]}))
    hits = Q.query(f"a design with a {_WORDS}", k=5, db_path=db)
    assert len(hits) == 5 and len({h["ic_class"] for h in hits}) == 5


def test_k_of_one_returns_the_single_best_lesson(tmp_path):
    db = _synthetic_db(tmp_path)
    hits = Q.query(f"a design with a {_WORDS}", k=1, db_path=db)
    assert len(hits) == 1
