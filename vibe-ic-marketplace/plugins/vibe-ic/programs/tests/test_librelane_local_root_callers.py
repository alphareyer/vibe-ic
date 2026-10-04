"""Bounded caller-to-run_chain LOCAL PDK-root integration controls."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import librelane_contract as ll
import librelane_fill_dfm as fill


def test_density_caller_maps_the_selected_pdk_root_to_the_run_chain_cli(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    pdk_root = tmp_path / "configured-pdk-root"
    selected = pdk_root / "pdkFixture"
    selected.mkdir(parents=True)
    gds = project / "fixture.gds"
    gds.write_text("neutral density fixture\n")
    runset = selected / "density.runset"
    runset.write_text("neutral deck fixture\n")
    config = project / "density-config.json"
    config.write_text(json.dumps({
        "meta": {"step": "KLayout.Density"},
        "KLAYOUT_DENSITY_RUNSET": str(runset),
    }) + "\n")

    monkeypatch.setattr(ll._ce, "no_container_route", lambda: True)
    monkeypatch.setattr(ll, "image_capability", lambda *_a, **_k: {})
    attestation = {
        "image": "fixture-image", "image_id": "sha256:" + "1" * 64,
        "repo_digests": [], "cid": "unit-local-cid", "network_mode": "bridge",
        "memory": 1024 ** 3, "memory_swap": 1024 ** 3, "auto_remove": True,
        "attestation_path": "unit-only", "attestation_sha256": "0" * 64,
    }
    monkeypatch.setattr(ll, "local_image_attestation", lambda _image: dict(attestation))
    monkeypatch.setattr(ll, "openroad_home", lambda *_a, **_k: None)
    monkeypatch.setattr(ll, "_check_state", lambda *_a, **_k: None)
    monkeypatch.setattr(fill, "measure_density_ratios", lambda *_a, **_k: {})

    captured = {}
    child_script = (
        "import json,sys; from pathlib import Path; a=sys.argv[1:]; "
        "root=a[a.index('--pdk-root')+1]; "
        "src=Path(a[a.index('--state-in')+1]); "
        "dst=Path(a[a.index('--state-out')+1]); "
        "state=json.loads(src.read_text()); "
        f"state.setdefault('metrics',{{}})[{fill.DENSITY_METRIC!r}]=0; "
        "state['cli_pdk_root']=root; dst.write_text(json.dumps(state)); "
        "print(root)"
    )

    def local_step(cmd, *, log=None, **_kwargs):
        captured["declared_cli_root"] = cmd[cmd.index("--pdk-root") + 1]
        captured["volume_args"] = [cmd[i + 1] for i, value in enumerate(cmd[:-1])
                                   if value == "-v"]
        image_index = cmd.index("fixture-image")
        state_in = cmd[cmd.index("-i") + 1]
        state_out = str(Path(cmd[cmd.index("-o") + 1]) / "state_out.json")
        safe = cmd[:image_index + 1] + [
            "-c", child_script,
            "--pdk-root", captured["declared_cli_root"],
            "--state-in", state_in,
            "--state-out", state_out,
        ]
        # Exercise the real LOCAL Docker-argv parser and real Python child;
        # replace only the EDA CLI body so this remains a software fixture.
        return ll._run_local(safe, probe_deadline_s=15, supervised=False, log=log)

    monkeypatch.setattr(ll, "run_container", local_step)
    initial = project / "initial-state.json"
    initial.write_text(json.dumps({"gds": str(gds.resolve())}) + "\n")
    monkeypatch.setattr(fill, "state_from_direct", lambda *_a, **_k: initial)

    result = fill.run_density(project, "fixture-image", pdk_root, "pdkFixture",
                              gds=gds, lane="root-caller", configs={
                                  "KLayout.Density": config,
                              }, steps=("KLayout.Density",))

    # run_chain localizes the guest namespace before calling the LOCAL
    # container adapter; the child therefore receives the configured host
    # root while the saved record retains the logical /pdk request.
    assert captured["declared_cli_root"] == str(pdk_root.resolve())
    assert captured["volume_args"] == [
        f"{project.resolve()}:{project.resolve()}",
        f"{pdk_root.resolve()}:{ll.PDK_GUEST_ROOT}:ro",
    ]
    state_path = Path(result["state"])
    state = json.loads(state_path.read_text())
    assert state["cli_pdk_root"] == str(pdk_root.resolve())
    root_record = json.loads((state_path.parent / "pdk_root.json").read_text())
    assert root_record["cli_pdk_root"] == str(pdk_root.resolve())
    assert root_record["requested_pdk_root"] == ll.PDK_GUEST_ROOT
    assert root_record["requested_mounts"] == [[str(pdk_root.resolve()), ll.PDK_GUEST_ROOT]]
    assert root_record["mounts_under_it"] == [[str(pdk_root.resolve()), str(pdk_root.resolve())]]


def test_density_caller_refuses_when_the_selected_pdk_is_missing(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    gds = project / "fixture.gds"
    gds.write_text("neutral density fixture\n")
    pdk_root = tmp_path / "configured-pdk-root"
    (pdk_root / "differentPdk").mkdir(parents=True)

    monkeypatch.setattr(ll, "image_capability", lambda *_a, **_k: {})
    monkeypatch.setattr(fill, "state_from_direct", lambda *_a, **_k: pytest.fail(
        "missing selected PDK must refuse before bridge construction"))
    with pytest.raises(ll.Refusal, match="LL_PDK_MISSING"):
        fill.run_density(project, "fixture-image", pdk_root, "pdkFixture",
                         gds=gds, lane="missing-selected-pdk",
                         steps=("KLayout.Density",))
