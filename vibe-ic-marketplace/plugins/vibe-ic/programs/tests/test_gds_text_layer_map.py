"""The declared stream-out TEXT map and the strict 37.5ic consumer."""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import _gds_geometry as geometry
import general_precheck as precheck
from _hostpaths import require_repo


def _record(code: int, dtype: int = 0, payload: bytes = b"") -> bytes:
    assert len(payload) % 2 == 0
    return struct.pack(">HBB", len(payload) + 4, code, dtype) + payload


def _text(layer: int, purpose: int, value: str) -> bytes:
    raw = value.encode("ascii")
    raw += b"\0" if len(raw) % 2 else b""
    return b"".join((
        _record(0x0C), _record(0x0D, 2, struct.pack(">H", layer)),
        _record(0x16, 2, struct.pack(">H", purpose)),
        _record(0x10, 3, struct.pack(">ii", 0, 0)),
        _record(0x19, 6, raw), _record(0x11)))


def _polygon(layer: int, purpose: int) -> bytes:
    points = (0, 0, 100, 0, 100, 100, 0, 100, 0, 0)
    return b"".join((
        _record(0x08), _record(0x0D, 2, struct.pack(">H", layer)),
        _record(0x0E, 2, struct.pack(">H", purpose)),
        _record(0x10, 3, struct.pack(">10i", *points)), _record(0x11)))


def _fixture(tmp_path: Path, *elements: bytes) -> Path:
    # Start with a real checked-in GDS, then add neutral synthetic elements.
    original = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic",
                            "programs", "tests", "fixtures", "density_fill",
                            "filled.gds").read_bytes()
    endstr = original.rfind(_record(0x07))
    assert endstr > 0 and original[endstr + 4:endstr + 8] == _record(0x04)
    path = tmp_path / "layout.gds"
    path.write_bytes(original[:endstr] + b"".join(elements) + original[endstr:])
    return path


def _forbidden(gds: Path, allowed: set[tuple[int, int]]):
    ev = precheck.StepEvidence("General.ForbiddenLayers", "forbidden", 6,
                               "technology", precheck.NOT_DETERMINED, "fail")
    precheck._step_forbidden_layers(
        ev, geometry.layers_used(geometry.read_layout(gds)), [], allowed,
        authority="fixture technology layer table")
    return ev


def test_streamout_map_clears_only_the_foundry_annotation_for_precheck(tmp_path):
    import gds_text_layer_map as layer_map

    gds = _fixture(tmp_path, _text(58, 0, "DVDD"))
    before = gds.read_bytes()
    allowed = set(geometry.layers_used(geometry.read_layout(gds))) - {(58, 0)}
    raw = _forbidden(gds, allowed)
    assert raw.verdict == precheck.FAIL
    assert raw.measured["unmapped_layers"] == ["58/0"]

    receipt_path = layer_map.apply_declared(gds, "gf180mcuD")
    clean = _forbidden(gds, allowed)
    assert clean.verdict == precheck.PASS, clean.evidence
    assert receipt_path is not None
    receipt = json.loads(receipt_path.read_text())
    assert receipt["removed_text"] == {"58/0": 1}
    assert receipt["input_sha256"] == hashlib.sha256(before).hexdigest()
    assert receipt["output_sha256"] == hashlib.sha256(gds.read_bytes()).hexdigest()
    assert receipt["map_sha256"] == hashlib.sha256(
        layer_map.map_for_pdk("gf180mcuD").read_bytes()).hexdigest()
    assert gds.read_bytes() == before.replace(_text(58, 0, "DVDD"), b"")


def test_undefined_text_still_fails_and_mapped_geometry_is_refused(tmp_path):
    import gds_text_layer_map as layer_map

    gds = _fixture(tmp_path, _text(58, 0, "DVDD"), _text(59, 0, "UNKNOWN"))
    allowed = set(geometry.layers_used(geometry.read_layout(gds))) - {(58, 0), (59, 0)}
    layer_map.apply_declared(gds, "gf180mcuD")
    forbidden = _forbidden(gds, allowed)
    assert forbidden.verdict == precheck.FAIL
    assert forbidden.measured["unmapped_layers"] == ["59/0"]

    unsafe = _fixture(tmp_path, _text(58, 0, "DVDD"), _polygon(58, 0))
    original_sha = hashlib.sha256(unsafe.read_bytes()).hexdigest()
    try:
        layer_map.apply_declared(unsafe, "gf180mcuD")
    except layer_map.LayerMapError as exc:
        assert "non-TEXT geometry" in str(exc)
    else:
        raise AssertionError("the layer map removed manufacturing geometry")
    assert hashlib.sha256(unsafe.read_bytes()).hexdigest() == original_sha
