"""The ISA-suite producer: pinned public suites, run on the design's own RTL,
judged by each suite's own criterion (lane isaprod, 2026-09-28).

MEASURED on a reused bit-serial RV32I core (vibeic-eda 0.3.84): riscv-arch-test
3.9.1 against Spike matched 9/40 signatures on the supplied RTL and 40/40 on
the upstream fix, while riscv-tests alone gave a false green. These tests pin
every refusal the producer makes and every verdict it can reach, without a
container: the tool runs are replaced by an executor that writes the file the
container job would write, so the REAL host path (lock, acquisition, env,
binding, judging, rows, transcript) is what runs.

chip-AGNOSTIC: the fixture design is a synthetic top with a byte-wide
simple-dual-port SRAM port; no chip, vendor, PDK or case-name literal.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import isa_suite_producer as I  # noqa: E402
import instruction_coverage_measure as ICM  # noqa: E402

CAL = PROGRAMS / "calibration"
ROOT = "suite-0123"
PROG_S = b"/* a program */\n"
HDR = b"/* a header */\n"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _tarball(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, blob in files.items():
            ti = tarfile.TarInfo(f"{ROOT}/{rel}")
            ti.size = len(blob)
            tf.addfile(ti, io.BytesIO(blob))
    return buf.getvalue()


FILES = {"src/add-01.S": PROG_S, "src/Fencei.S": PROG_S, "env/h.h": HDR}
TARBALL = _tarball(FILES)


def _lock(**over) -> dict:
    suite = {
        "commit": "f" * 40, "root": ROOT,
        "tarball_url": "https://example.invalid/suite.tar.gz",
        "tarball_sha256": _sha(TARBALL),
        "include_dirs": ["env"],
        "files": {k: _sha(v) for k, v in FILES.items()},
        "programs": [
            {"id": "s-add", "path": "src/add-01.S", "unit": "I",
             "instruction": "add", "judge": "signature", "role": "primary"},
            {"id": "s-fencei", "path": "src/Fencei.S", "unit": "Zifencei",
             "instruction": "fence.i", "judge": "signature", "role": "primary"},
        ],
    }
    suite.update(over)
    return {"isa_units": {
        "I": {"instructions": ["add", "sub", "ecall", "ebreak"],
              "trap_dependent": ["ecall", "ebreak"],
              "trap_dependent_reason": "no trap target"},
        "Zifencei": {"instructions": ["fence.i"], "trap_dependent": []}},
        "suites": {"s": suite}}


RTL = """module soc_top #(parameter memsize = 1024, parameter aw = $clog2(memsize))
  (input wire clk_i, input wire rst_i,
   output wire [aw-1:0] o_m_waddr, output wire [7:0] o_m_wdata,
   output wire o_m_wen, output wire [aw-1:0] o_m_raddr,
   input wire [7:0] i_m_rdata, output wire o_m_ren, output wire o_led);
endmodule
"""


def _project(tmp: Path, units=("I", "Zifencei"), **decl_over) -> Path:
    p = tmp / "proj"
    (p / "plugin_output").mkdir(parents=True)
    decl = {"top_module": "soc_top", "clock_port_name": "clk_i",
            "memsize_bytes": 1024, "reset_polarity": "active_high",
            "sram_interface_protocol": "sdp8_waddr_raddr_wen_ren_sync1r",
            "isa_extensions": list(units), "rf_storage": "shared_sram"}
    decl.update(decl_over)
    (p / "plugin_output" / "declaration.json").write_text(json.dumps(decl))
    gd = p / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    (gd / "L10_TEST_CASES.json").write_text(json.dumps({"fields": {"test_cases": [
        {"name": "base_isa", "kind": "functional_vector",
         "stimulus": "the whole RV32I instruction set, unit tests",
         "expected": "100% PASS (compliance suite)"},
        {"name": "fence_ext", "kind": "functional_vector",
         "stimulus": "Zifencei instruction", "expected": "PASS"},
        {"name": "mul_ext", "kind": "functional_vector",
         "stimulus": "(if M is chosen) Mul/Div", "expected": "PASS",
         "applies_when": {"option": "M"}},
        {"name": "boot", "kind": "functional_vector",
         "stimulus": "first fetch after reset", "expected": "within 10 cycles"},
    ]}}))
    (gd / "L8_RTL_CONSTANTS.json").write_text(json.dumps(
        {"params": [{"name": "RESET_PC", "default": "`0x00000000`",
                     "source": "L3_external_interface.md"}]}))
    rtl = p / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "soc_top.v").write_text(RTL)
    return p


def _fake_executor(outcome: dict):
    """Writes the results.json the container job would write. `outcome` maps
    program id -> (ref_words|None, ref_rc, dut transcript, dut words|None)."""
    def _run(job_path: Path, work: Path):
        job = json.loads(Path(job_path).read_text())
        res = {"programs": {}, "builds": {}, "sims": {}, "done": True,
               "memsize_full": 1 << 21}
        for arm in job["arms"]:
            res["builds"][f"{arm['name']}_full"] = {"ok": True}
        for prog in job["programs"]:
            ref_words, ref_rc, tr, dut_words = outcome[prog["id"]]
            d = Path(work) / "o" / prog["id"]
            d.mkdir(parents=True)
            (d / "dut.dis").write_text((CAL / "isa_objdump_no_relax_negative.dis").read_text())
            ref = None
            if ref_words is not None:
                ref = d / "ref.sig"
                ref.write_text("\n".join(ref_words) + "\n")
            res["programs"][prog["id"]] = {
                "build": {"ok": True, "size": 4096, "ref_size": 4096,
                          "tohost": 100, "disassembly": str(d / "dut.dis")},
                "ref": {"rc": ref_rc, "sig": str(ref) if ref else None}}
            for arm in job["arms"]:
                for iv in job["init_patterns"]:
                    sig = None
                    if dut_words is not None:
                        sig = d / f"{arm['name']}_{iv}.sig"
                        sig.write_text("\n".join(dut_words) + "\n")
                    res["sims"].setdefault(prog["id"], {}).setdefault(
                        f"{arm['name']}_full", {})[iv] = {
                        "rc": 0, "transcript": tr, "sig": str(sig) if sig else None}
        (Path(work) / "results.json").write_text(json.dumps(res))
        return 0, "fake"
    return _run


HALT = (CAL / "isa_tb_halt_negative.log").read_text()
HANG = (CAL / "isa_tb_hang_positive.log").read_text()
WORDS = ["00000001", "deadbeef", "00000000"]
ALLOW = {"external_suite_fetch": "allowed"}


def _produce(p, outcome, **kw):
    return I.produce(p, executor=_fake_executor(outcome),
                     fetch=lambda _u: TARBALL, lock=kw.pop("lock", _lock()),
                     pol=kw.pop("pol", ALLOW), **kw)


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBEIC_ISA_SUITE_CACHE", str(tmp_path / "cache"))


# ── 1. the lock: a digest that does not match is NOT_MEASURED ─────────────

def test_a_tarball_digest_mismatch_is_refused(tmp_path):
    root, why = I.acquire("s", _lock()["suites"]["s"], tmp_path / "x",
                          fetch=lambda _u: TARBALL + b"\0")
    assert root is None and "tarball sha256" in why


def test_a_member_digest_mismatch_is_refused_naming_the_file(tmp_path):
    lock = _lock()
    lock["suites"]["s"]["files"]["src/add-01.S"] = "0" * 64
    root, why = I.extract_verified(TARBALL, lock["suites"]["s"], tmp_path)
    assert root is None and "src/add-01.S" in why


def test_the_verified_tarball_extracts_only_locked_files(tmp_path):
    root, why = I.extract_verified(TARBALL, _lock()["suites"]["s"], tmp_path)
    assert root is not None and "3 locked file(s) verified" in why
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) \
        == sorted(FILES)


def test_no_network_makes_every_bound_case_not_measured(tmp_path):
    p = _project(tmp_path)

    def _down(_u):
        raise OSError("no route to host")
    rec = I.produce(p, executor=_fake_executor({}), fetch=_down, lock=_lock(),
                    pol=ALLOW)
    assert "no route to host" in rec["refusal"]
    assert {r["id"]: r["verdict"] for r in rec["rows"]} == {
        "base_isa": "NOT_EXECUTED", "fence_ext": "NOT_EXECUTED"}
    assert all(r["sim_executed"] is False and "NOT_MEASURED" in r["detail"]
               for r in rec["rows"])


def test_a_mismatched_tarball_through_the_producer_is_not_measured(tmp_path):
    p = _project(tmp_path)
    rec = I.produce(p, executor=_fake_executor({}), fetch=lambda _u: b"junk",
                    lock=_lock(), pol=ALLOW)
    assert "tarball sha256" in rec["refusal"]
    assert all(r["verdict"] == "NOT_EXECUTED" for r in rec["rows"])


def test_a_refused_fetch_policy_names_itself(tmp_path):
    p = _project(tmp_path)
    rec = I.produce(p, executor=_fake_executor({}), fetch=lambda _u: TARBALL,
                    lock=_lock(), pol={"external_suite_fetch": "refused"})
    assert "external_suite_fetch='refused'" in rec["refusal"]


def test_the_shipped_policy_allows_the_fetch():
    assert I.fetch_allowed(I.load_policy())[0] is True


def test_the_shipped_lock_pins_commits_and_digests():
    lock = I.load_lock()
    for name, s in lock["suites"].items():
        assert len(s["commit"]) == 40, name
        assert len(s["tarball_sha256"]) == 64, name
        for prog in s["programs"]:
            assert prog["path"] in s["files"], prog["id"]
    act = [p for p in lock["suites"]["riscv-arch-test"]["programs"]]
    assert len([p for p in act if p["unit"] == "I"]) == 39
    covered = {p["instruction"] for p in act if p["unit"] == "I"}
    enum = lock["isa_units"]["I"]["instructions"]
    assert sorted(set(enum) - covered) == ["ebreak", "ecall"]


# ── 2. the env is the flow's own, at the design's reset vector ────────────

def test_the_env_is_csr_free_and_links_at_the_reset_vector():
    env = I.gen_env({"reset_pc": 0x100})
    assert ". = 0x100;" in env["link_dut.ld"]
    assert f". = {I.SPIKE_DRAM_BASE:#x};" in env["link_ref.ld"]
    for name in ("riscv_test.h", "model_test.h"):
        code = re.sub(r"/\*.*?\*/", "", env[name], flags=re.S).lower()
        assert "tohost" in code
        assert not re.search(r"\bcsr\w*\b|\becall\b|\bebreak\b|mtvec", code)


def test_design_facts_come_from_the_declaration_and_the_ports(tmp_path):
    facts, why = I.design_facts(_project(tmp_path))
    assert facts is not None, why
    assert facts["sram_ports"] == {
        "waddr": "o_m_waddr", "wdata": "o_m_wdata", "wen": "o_m_wen",
        "raddr": "o_m_raddr", "rdata": "i_m_rdata", "ren": "o_m_ren"}
    assert (facts["reset"], facts["memsize_param"], facts["reset_pc"],
            facts["rf_reserved_bytes"]) == ("rst_i", "memsize", 0, 128)
    tb = I.gen_tb(facts)
    assert "soc_top #(.memsize(MEMSIZE)) dut" in tb
    assert "mem[i] = iv[7:0]" in tb           # powered up to +init, not zero


def test_an_unmodellable_sram_protocol_is_refused_by_name(tmp_path):
    p = _project(tmp_path, sram_interface_protocol="wishbone_classic")
    facts, why = I.design_facts(p)
    assert facts is None and "wishbone_classic" in why


# ── 3. the objdump guard ───────────────────────────────────────────────────

def test_a_planted_compressed_parcel_is_caught():
    real = (CAL / "isa_objdump_rvc_parcel_positive.dis").read_text()
    assert I.objdump_guard(real, allow_compressed=False) == [
        "2 16-bit instruction(s) on a core that declares no C"]
    planted = (CAL / "isa_objdump_no_relax_negative.dis").read_text() + \
        "  1c0:\t0001                \tc.nop\n"
    assert I.objdump_guard(planted, allow_compressed=False)
    assert I.objdump_guard(planted, allow_compressed=True) == []


def test_a_clean_image_passes_the_guard():
    clean = (CAL / "isa_objdump_no_relax_negative.dis").read_text()
    assert I.objdump_guard(clean, allow_compressed=False) == []


def test_a_csr_instruction_and_an_oversized_image_are_caught():
    dis = "   0:\t30529073          \tcsrrw\tzero,mtvec,t0\n" \
          "   4:\t00000073          \tecall\n"
    v = I.objdump_guard(dis, allow_compressed=False, size_bytes=900,
                        program_area=896)
    assert "CSR/system instruction(s) csrrw x1, ecall x1" in v
    assert "image ends at 900 B, beyond the 896 B program area" in v


# ── 4. the judge ───────────────────────────────────────────────────────────

def test_a_signature_mismatch_fails_naming_the_first_differing_word():
    dut = {"status": "halted", "tohost": 1, "cycles": 10}
    st, why = I.judge_signature(WORDS, 0, dut,
                                ["00000001", "deadbeee", "00000001"])
    assert st == I.FAIL
    assert why.startswith("signature word 1 (byte offset 0x4) is deadbeee, "
                          "reference deadbeef (2 of 3 words differ)")


def test_a_hang_is_fail():
    dut = I.parse_tb_transcript(HANG)
    assert dut["status"] == "hang" and dut["cycles"] == 20_000_000
    assert I.judge_signature(WORDS, 0, dut, None)[0] == I.FAIL
    assert I.judge_tohost(0, dut)[0] == I.FAIL


def test_a_reference_timeout_or_missing_signature_is_not_measured():
    dut = I.parse_tb_transcript(HALT)
    assert I.judge_signature(WORDS, 124, dut, WORDS)[0] == I.NOT_MEASURED
    assert I.judge_signature(None, 0, dut, WORDS)[0] == I.NOT_MEASURED
    assert I.judge_tohost(124, dut)[0] == I.NOT_MEASURED


def test_a_matching_signature_and_a_tohost_of_one_pass():
    dut = I.parse_tb_transcript(HALT)
    assert dut == {"status": "halted", "tohost": 1, "cycles": 154515}
    assert I.judge_signature(WORDS, 0, dut, list(WORDS))[0] == I.PASS
    assert I.judge_tohost(0, dut)[0] == I.PASS
    assert I.judge_tohost(0, dict(dut, tohost=5)) == (I.FAIL,
                                                      "self-check failed at test 2")


def test_a_set_is_pass_only_when_every_member_passes():
    assert I.fold([I.PASS, I.PASS]) == I.PASS
    assert I.fold([I.PASS, I.NOT_MEASURED]) == I.NOT_MEASURED
    assert I.fold([I.NOT_MEASURED, I.FAIL]) == I.FAIL
    assert I.fold([]) == I.NOT_MEASURED


# ── 5. the denominator is derived from the declaration ────────────────────

def test_no_trap_support_excludes_ecall_and_ebreak_by_name(tmp_path):
    facts, _ = I.design_facts(_project(tmp_path))
    total, excl, why = I.instruction_total(_lock(), facts)
    assert (total, excl) == (2, ["ecall", "ebreak"])
    assert "no Zicsr" in why


def test_a_trap_capable_core_is_judged_on_every_instruction(tmp_path):
    facts, _ = I.design_facts(_project(tmp_path, units=("I", "Zicsr")))
    assert I.instruction_total(_lock(), facts)[:2] == (4, [])
    facts, _ = I.design_facts(_project(tmp_path / "b", trap_support=True))
    assert I.instruction_total(_lock(), facts)[:2] == (4, [])


def test_the_shipped_lock_gives_38_without_traps_and_40_with():
    lock = I.load_lock()
    assert I.instruction_total(lock, {"trap_support": False})[:2] == (
        38, ["ecall", "ebreak"])
    assert I.instruction_total(lock, {"trap_support": True})[:2] == (40, [])


# ── 6. which cases bind ────────────────────────────────────────────────────

def test_only_cases_naming_a_declared_unit_bind(tmp_path):
    p = _project(tmp_path)
    assert I.bound_cases(p, ["I", "Zifencei"]) == {
        "base_isa": ["I"], "fence_ext": ["Zifencei"]}
    assert I.bound_cases(p, ["I"]) == {"base_isa": ["I"]}
    assert I.bind_case({"stimulus": "(if M is chosen) Mul/Div"},
                       ["I", "M"]) == []
    assert I.bind_case({"stimulus": "RV32IM subset"}, ["I"]) == []


# ── 7. the producer end to end, and the coverage line ─────────────────────

def test_the_coverage_line_is_the_instruments_grammar():
    line = I.coverage_line(37, 38)
    m = ICM.EMISSION_RE.search(line)
    assert m and (m.group("dim"), m.group("covered"), m.group("total")) == (
        "instruction", "37", "38")


def test_a_passing_suite_writes_pass_rows_and_the_coverage_line(tmp_path):
    p = _project(tmp_path)
    rec = _produce(p, {"s-add": (WORDS, 0, HALT, WORDS),
                       "s-fencei": (WORDS, 0, HALT, WORDS)})
    rows = {r["id"]: r for r in rec["rows"]}
    assert rows["base_isa"]["verdict"] == "PASS"
    assert rows["base_isa"]["sim_executed"] is True
    assert rows["fence_ext"]["verdict"] == "PASS"
    cov = rec["cases"]["base_isa"]["coverage"]
    assert cov == {"covered": 1, "total": 2, "excluded": ["ecall", "ebreak"],
                   "uncovered": ["sub"]}
    text = (p / I.TRANSCRIPT_REL.format(case="base_isa")).read_text()
    assert I.coverage_line(1, 2) in text
    assert "excluded from the instruction total: ecall, ebreak" in text
    assert "(parameter only); delivered-size subset at memsize 1024" in text
    assert (p / I.RECEIPT_REL).is_file()


def test_a_mismatch_or_a_hang_fails_the_case(tmp_path):
    p = _project(tmp_path)
    rec = _produce(p, {"s-add": (WORDS, 0, HALT, ["00000001", "0", "0"]),
                       "s-fencei": (WORDS, 0, HANG, None)})
    rows = {r["id"]: r["verdict"] for r in rec["rows"]}
    assert rows == {"base_isa": "FAIL", "fence_ext": "FAIL"}
    assert rec["cases"]["base_isa"]["coverage"]["covered"] == 0


def test_a_reference_timeout_keeps_the_case_not_measured(tmp_path):
    p = _project(tmp_path)
    rec = _produce(p, {"s-add": (None, 124, HALT, WORDS),
                       "s-fencei": (WORDS, 0, HALT, WORDS)})
    rows = {r["id"]: r for r in rec["rows"]}
    assert rows["base_isa"]["verdict"] == "NOT_EXECUTED"
    assert rows["base_isa"]["sim_executed"] is False
    assert rows["fence_ext"]["verdict"] == "PASS"


def test_a_design_with_no_isa_case_is_left_alone(tmp_path):
    p = _project(tmp_path, units=())
    rec = _produce(p, {})
    assert rec["rows"] == [] and "nothing to produce" in rec["refusal"]
    assert not (p / I.RECEIPT_REL).exists()


def test_the_isa_row_replaces_the_scaffold_row():
    old = [{"id": "base_isa", "verdict": "NOT_EXECUTED"},
           {"id": "boot", "verdict": "PASS"}]
    new = [{"id": "base_isa", "verdict": "FAIL"}]
    assert I.merge_rows(old, new) == [{"id": "boot", "verdict": "PASS"},
                                      {"id": "base_isa", "verdict": "FAIL"}]


def test_the_executor_publishes_the_isa_row(tmp_path):
    """Through `testbench_gen.run_unit_tbs`: the scaffold's NOT_EXECUTED row
    for an ISA case is replaced by the suite's own verdict."""
    import testbench_gen as TB
    import _l10_execution as X
    p = _project(tmp_path)
    tb = p / "phase2" / "stage1" / "sim" / "tb"
    tb.mkdir(parents=True)
    (tb / "base_isa.v").write_text(f"// {TB.ORACLE_NONE_MARKER}\nmodule base_isa;\nendmodule\n")

    def _dispatch(argv, run_dir, container, tool, timeout):
        return 0, "ok"

    def _isa(project):
        return {"rows": [{"id": "base_isa", "verdict": "FAIL",
                          "sim_executed": True, "tb_file": "receipt",
                          "detail": "ISA suite: 0/1 primary programs pass"}],
                "cases": {}}
    rep: dict = {}
    TB.run_unit_tbs(p, report=rep, dispatch=_dispatch, isa_producer=_isa)
    rec = X.load_record(p)
    assert X.case_state("base_isa", rec)[0] == X.FAIL
    xml = (p / "phase2/stage1/sim_professional/l10_unit_tb/results.xml").read_text()
    assert 'name="base_isa"' in xml and "<failure" in xml
