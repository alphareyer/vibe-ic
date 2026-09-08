"""A failed report write must preserve the previously published evidence.

Exercise both real CLI report writers with a partial-write OSError. Their
decision inputs are independent of the injected file failure; the success arms
must publish parseable evidence and retain the original refusal return codes.
"""
import builtins
from collections import Counter
import io
import json
import subprocess
from pathlib import Path

import pytest

import digital_rtl_subject_census as census
import landing_hygiene_ratchet_check as ratchet


@pytest.fixture(params=["census", "ratchet"])
def writer(request, tmp_path, monkeypatch):
    if request.param == "census":
        project = tmp_path / "project"
        project.mkdir()
        # Missing L9 is an unanswered question. A report must still be written.
        return lambda output: census.main([str(project), "--json", str(output)]), 2
    # The ratchet now reads its real commit range before archiving. A report
    # fixture needs readable revisions too, otherwise it only tests NORECORD.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    def commit(label):
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp_path),
                        "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", label], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "tag", label], check=True)
    subject = tmp_path / "subject.txt"
    subject.write_text("before\n")
    commit("base")
    subject.write_text("after\n")
    commit("candidate")
    monkeypatch.setattr(
        ratchet, "_findings",
        lambda entry, plugin, root, arm:
        Counter({("programs/new.py", "R1"): 1}) if arm == "candidate" else Counter())
    return lambda output: ratchet.main([
        "--repo", str(tmp_path), "--plugin-root", str(tmp_path),
        "--base", "base", "--head", "candidate", "--json", str(output)]), 1


def test_report_publishes_without_changing_the_decision(writer, tmp_path):
    run, expected_rc = writer
    output = tmp_path / "reports" / "result.json"
    output.parent.mkdir()
    assert run(output) == expected_rc
    record = json.loads(output.read_text())
    if expected_rc == 2:
        assert record["digital_rtl_subject_absent"] is False
        assert record["evidence"]["interface"]["readable"] is False
    else:
        assert record["gates"]
        assert all(gate["introduced"] for gate in record["gates"].values())
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize("prior_exists", [False, True])
def test_partial_write_never_publishes_partial_evidence(
        writer, tmp_path, monkeypatch, prior_exists):
    run, _ = writer
    output = tmp_path / "reports" / "result.json"
    output.parent.mkdir()
    prior = b'{"previous": "complete evidence"}\n'
    if prior_exists:
        output.write_bytes(prior)
    opened = []
    real_open = builtins.open
    real_io_open = io.open

    class PartialWrite:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, text):
            self.stream.write(text[:5])
            self.stream.flush()
            raise OSError("injected partial report write")

    def intercept(opener):
        def open_file(file, mode="r", *args, **kwargs):
            stream = opener(file, mode, *args, **kwargs)
            if "w" in mode and not isinstance(file, int) \
                    and Path(file).parent == output.parent:
                opened.append(str(file))
                return PartialWrite(stream)
            return stream
        return open_file

    monkeypatch.setattr(builtins, "open", intercept(real_open))
    monkeypatch.setattr(io, "open", intercept(real_io_open))
    with pytest.raises(OSError, match="injected partial report write"):
        run(output)
    assert opened, "the injected failure never reached a report writer"
    if prior_exists:
        assert output.read_bytes() == prior
    else:
        assert not output.exists()
    assert list(output.parent.iterdir()) == ([output] if prior_exists else [])
