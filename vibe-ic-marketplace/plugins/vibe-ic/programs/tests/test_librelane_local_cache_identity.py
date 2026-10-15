"""Actual run_chain/live-attestation controls; native process writes substituted."""
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

import librelane_contract as contract
from test_librelane_contract import local_image, put  # existing launch fixture


@pytest.mark.parametrize("case", ["cid_rotation", "invalid_attestation", "wrong_image",
    "different_image", "def", "sdc", "spef", "cached_artifact", "plugin_source", "memory"])
def test_local_cache_rechecks_authority_and_reuses_only_same_inputs(local_image, monkeypatch, case):
    project = local_image.tmp / "project"
    source = project / "source"
    source.mkdir(parents=True)
    views = {}
    for view in ("odb", "def", "nl", "sdc", "spef"):
        path = source / ("neutral." + view)
        path.write_text("neutral " + view + " input\n")
        views[view] = str(path)
    initial = put(project / "initial.json", views)
    step = "Vibeic.IRDropChecker" if case == "plugin_source" else "OpenROAD.IRDropReport"
    config = put(project / "config.json", {"meta": {"step": step}})
    plugin = local_image.tmp / "source-plugin" / "librelane_plugins"
    plugin.mkdir(parents=True)
    plugin_source = plugin / "neutral.py"
    plugin_source.write_text("# neutral custom-step source\n")
    for name in contract.PLUGIN_HOST_MODULES:
        (plugin.parent / name).write_text("# neutral imported source\n")
    monkeypatch.setattr(contract, "PLUGIN_ROOT", plugin)
    monkeypatch.setattr(contract, "image_capability", lambda *_: None)
    calls, attestations = [], []
    original_attestation = contract.local_image_attestation

    def checked(image):
        result = original_attestation(image)
        attestations.append(result)
        return result

    monkeypatch.setattr(contract, "local_image_attestation", checked)

    def native_transport(argv, **_kwargs):
        calls.append(list(argv))
        folder = Path(argv[argv.index("-o") + 1])
        put(folder / "state_out.json", views)
        (folder / "result.rpt").write_text("neutral native process result\n")
        return SimpleNamespace(returncode=0, stdout="neutral native process executed", stderr="")

    monkeypatch.setattr(contract, "run_container", native_transport)
    image = local_image.image
    invoke = lambda: contract.run_chain(project, image, [(step, config, initial)], pdk_root="/pdk")[0]
    folder = invoke()
    assert len(calls) == 1
    receipt = folder / "vibeic_receipt.json"
    fingerprint = folder / "input_fingerprint.json"
    old_bytes, old_fp = receipt.read_bytes(), fingerprint.read_bytes()
    old = json.loads(old_bytes)
    assert old["input"]["local_image"] == attestations[0]
    assert {"cid", "attestation_path", "attestation_sha256"} <= set(old["input"]["local_image"])

    # Supply a complete new launch receipt to the real current verifier;
    # no cached attestation or placeholder trusted-image label is injected.
    launch = json.loads(local_image.receipt.read_text())
    cid = "12" * 32
    launch["cid"] = launch["container_inspect"]["Id"] = cid
    launch["container_inspect"]["Config"]["Hostname"] = cid[:12]
    monkeypatch.setattr(socket, "gethostname", lambda: cid[:12])
    current = local_image.tmp / "new-launch" / "launch_identity.json"
    if case == "invalid_attestation":
        launch["container_inspect"]["State"]["Running"] = False
    elif case == "wrong_image":
        launch["container_inspect"]["Config"]["Image"] = "registry.invalid/wrong@sha256:" + "34" * 32
    elif case == "different_image":
        image = "registry.invalid/tools@sha256:" + "34" * 32
        image_id = "sha256:" + "56" * 32
        launch["image"] = launch["container_inspect"]["Config"]["Image"] = image
        launch["container_inspect"]["Image"] = image_id
        launch["image_inspect"] = [{"Id": image_id, "RepoDigests": [image]}]
    elif case in ("def", "sdc", "spef"):
        Path(views[case]).write_text("changed scientific input\n")
    elif case == "cached_artifact":
        (folder / "result.rpt").write_text("changed native artifact\n")
    elif case == "plugin_source":
        plugin_source.write_text("# changed custom-step source\n")
    elif case == "memory":
        launch["container_inspect"]["HostConfig"]["Memory"] *= 2
    put(current, launch)
    monkeypatch.setenv(contract.LOCAL_ATTESTATION_ENV, str(current))
    refused = case in ("invalid_attestation", "wrong_image")
    if refused:
        with pytest.raises(contract.Refusal, match="LL_LOCAL_IMAGE_UNATTESTED"):
            invoke()
        assert len(calls) == 1, "a retained step must never bypass fresh current authority"
        assert receipt.read_bytes() == old_bytes
    else:
        assert invoke() == folder
        assert len(attestations) == 2, "each lookup must fully validate its current launch"
        if case == "cid_rotation":
            assert len(calls) == 1, "same scientific inputs and image need zero new native acquisitions"
            assert attestations[0]["cid"] != attestations[1]["cid"]
            assert attestations[0]["attestation_sha256"] != attestations[1]["attestation_sha256"]
            assert receipt.read_bytes() == old_bytes, "never rewrite the original execution receipt"
            assert fingerprint.read_bytes() == old_fp
            assert contract.validate_step_receipt(folder, step)["input"] == old["input"]
        else:
            assert len(calls) == 2, "a changed image/scientific/source/output/resource input must not reuse"
            assert json.loads(receipt.read_text())["input"]["local_image"] == attestations[1]
    put(project / "API_OBSERVATION.json", {"case": case, "native_acquisition_count": len(calls),
        "validated_attestations": attestations, "refused": refused,
        "original_receipt": old, "receipt_unchanged": receipt.read_bytes() == old_bytes,
        "source_control_only": True})
