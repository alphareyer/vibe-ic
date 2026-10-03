import subprocess
import pytest
import execution_backend_producers as producers
import path_spice_tool as spice

IMAGE = "sha256:" + "a" * 64


def test_step30_docker_uses_f1_supervised_runner(monkeypatch, tmp_path):
    calls = []
    import librelane_contract as ll

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(ll, "run_container", run)
    monkeypatch.setattr(spice._dmem, "docker_memory_flags", lambda: [])
    result = spice._docker(IMAGE, tmp_path, [], ["ngspice", "-b", "deck.sp"], tmp_path)
    assert result.returncode == 0
    assert calls and calls[0][1] == {"supervised": True}
    assert result._vibeic_lifecycle["pid"]
    assert result._vibeic_lifecycle["start_ns"] <= result._vibeic_lifecycle["end_ns"]



def test_step30_exception_preserves_sibling_measured_fail(tmp_path):
    def run_one(simulator):
        if simulator == "xyce":
            raise RuntimeError("sibling timeout")
        return {"arms": {simulator: {"verdict": "FAIL"}},
                "detail": {simulator: {"base": {"paths": []}}}}

    results = producers._step30_parallel_instruments(("ngspice", "xyce"), run_one)
    merged = producers._step30_merge_parallel(tmp_path, results,
                                               ("ngspice", "xyce"), spice)
    assert merged["verdict"] == "FAIL"
    assert merged["arms"]["ngspice"]["verdict"] == "FAIL"
    assert merged["arms"]["xyce"]["verdict"] == "NOT_MEASURED"



def test_step30_retained_hash_is_checked_at_consumption(tmp_path):
    private = tmp_path / "private"
    project = tmp_path / "project"
    arm = private / "phase3/tool_arms/30/ngspice/base"
    arm.mkdir(parents=True)
    project.mkdir()
    deck = arm / "deck.sp"
    deck.write_text("original")
    document = producers._step30_retain_document(
        project, private, "ngspice", {"deck": str(deck)})
    deck_target = project / "phase3/tool_arms/30/ngspice/base/deck.sp"
    deck_target.write_text("mutated")
    with pytest.raises(producers.em.Refusal, match="STEP30_RETAINED_ARTIFACT_MISMATCH"):
        producers._step30_validate_retained_document(document)
    # Refusing changed retained bytes prevents PASS, but cannot erase FAIL.
    for verdict, expected in (("FAIL", "FAIL"), ("PASS", "NOT_MEASURED")):
        result = {**document, "verdict": verdict,
                  "arms": {"ngspice": {"verdict": verdict}}}
        merged = producers._step30_merge_parallel(
            project, {"ngspice": result}, ("ngspice",), spice)
        assert merged["verdict"] == expected
        assert merged["arms"]["ngspice"]["verdict"] == expected
