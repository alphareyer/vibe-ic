# Compliance Testing Strategy — three layers, not one

## The trap we avoided

A naive single-layer `test_good_output_passes_all_required` per skill forces
the test generator to BOTH (a) exercise the compliance engine, AND (b)
synthesise a markdown document that satisfies every required regex. Those
two goals pull in opposite directions: the compliance engine wants tight
patterns, the fixture synthesiser wants loose literal text. As patterns get
more structured (markdown headings, tables, SVA blocks), the synthesiser
cannot keep up, and every test ends in either a false PASS (accepting bad
output) or a silent SKIP. v0.50 audit caught 59/64 skills in the SKIP
state — not because the plugin was broken, but because the test generator
was naive about what "generate good output" means.

The fix is not a smarter generator. It's decoupling fixture quality from
gate correctness.

## Three layers

### Layer 1 — Compliance Engine

**Question**: given an output + compliance.yaml, does the engine correctly
identify missing required elements?

**Test**: feed the engine a known-bad input (empty file, a real Unicode
mix) and a known-good input. Assert engine verdict matches expectation.
This is entirely independent of per-skill pattern complexity.

**Location**: `_shared/test_compliance_engine.py`.

### Layer 2 — Pattern Validity

**Question**: does each regex in each compliance.yaml actually compile,
and does it match what the skill author INTENDED (a small positive
sample) and NOT match what they DID NOT intend (a small negative sample)?

**Test**: parametric across every yaml; requires authors to supply
`positive_sample` and `negative_sample` strings per requirement (short
and focussed, usually one line each).

**Location**: `_shared/test_compliance_patterns.py`.

### Layer 3 — Integration / Golden Fixture

**Question**: for the ~8 critical gatekeeper skills (spec-to-rtl,
integration-spec-gen, flow-orchestrate, phase1-orchestrate,
tapeout-checklist, datasheet-gen, rtl-review, testbench-gen), does a
realistic full-markdown fixture pass all requirements end-to-end?

**Test**: hand-written golden fixtures per skill, run through the
compliance engine with strict PASS expectation. These are the skills
whose failure breaks the whole flow; worth the maintenance cost.

**Location**: `_shared/integration_fixtures/<skill>.md` + a shared
driver test.

## Testability tagging

Each compliance.yaml carries a top-level field:

```yaml
testability: simple | structured | full_markdown
```

- `simple` — patterns are literal keywords or simple alternations the
  auto-fixture can synthesise. Layer 1 + Layer 2 suffice; no Layer 3
  needed. The auto `test_good_output_passes_all_required` runs and
  passes for these.
- `structured` — patterns reference markdown headings, SVA constructs,
  tables, or code blocks. Auto-fixture SKIPs Layer 3 with an explicit
  marker; Layer 2 pattern validity still runs.
- `full_markdown` — critical gatekeeper skill. A hand-written fixture
  lives under `_shared/integration_fixtures/<skill>.md`; a dedicated
  integration test runs it through the engine and asserts full PASS.

## What a regression looks like per layer

| Symptom | Affected layer | Fix |
|---------|---------------|-----|
| New skill ships with non-compiling regex | L2 | skill owner fixes the regex |
| Existing skill's regex breaks on real output | L2 | update positive_sample to current spec, then fix regex |
| Engine miscategorises a requirement as satisfied | L1 | engine bug, debug `skill_compliance_check.py` |
| Critical skill's full-flow output stops satisfying spec | L3 | golden fixture fails → either fix skill or update fixture |

## Why this is better than "auto-generate 63 golden fixtures"

- 63 hand-written files × every spec change is a maintenance sink.
- False confidence: fixture may match regex but not reflect real agent
  output, so passing L3 for a non-critical skill proves nothing useful.
- The L2 regex unit test is tighter: it tests WHAT the regex is meant
  to match (positive sample) and WHAT it must reject (negative sample),
  which is exactly what most compliance checks need — no more.

## Why we still keep Layer 3 for the top-8 skills

Those skills are the ones that, if the compliance.yaml drifts out of
sync with the actual skill output, break the downstream flow (RTL
generation blocked, tapeout refused). For them the extra hand-fixture
cost is justified as continuous verification that the engine + spec +
realistic output still agree end-to-end.

## Select the required scope; name FULL only when it was run

The plugin has **two** test trees. A FULL claim includes **both**:

- `programs/tests/` — unit tests for the deterministic programs.
- `tests/` — integration / regression checks, including program inventory,
  orchestrator input-branch regressions and the end-to-end skill audit.

`pytest.ini` declares the primary collection, including `testpaths`,
`python_files` and `norecursedirs`. Use the canonical landing cadence and trusted
selection for release verification; include affected integration tests even when
the edited source is under `programs/`. A targeted run is not a FULL-suite PASS.
Do not start another full census automatically after each local repair. Explicit
full-audit or benchmark-completeness requests retain their stated acceptance.

## Skill authoring does not automatically create a new gate

A guidance skill needs valid instructions and valid references, not a mandatory
`compliance.yaml`, copied test module or literal heading. Author a machine-readable
contract only when the skill has a specific consumer requirement to verify.
Existing authored requirements and receipt-evidence checks remain effective.

The authoring helpers act only on explicitly selected skills (`--skill NAME`):
`bootstrap_compliance.py` produces a reviewable draft, `add_compliance_gate.py`
describes an authored contract's scope, and `gen_compliance_tests.py` uses a shared
implementation with thin wrappers. No-argument invocations do not mutate every
skill. Report-pattern success is not evidence that an EDA tool or flow ran.

Prefer extending the existing behavioral case or shared implementation to adding
another global blocking check. A new global check needs a concrete uncovered risk,
applicable scope, expected cost and owner in review; this guidance is not itself
another automated gate. Preserve real positive and negative cases when removing
duplicate representation. Program inventory registration remains distinct from
appointing a program as a release-wide gate.
