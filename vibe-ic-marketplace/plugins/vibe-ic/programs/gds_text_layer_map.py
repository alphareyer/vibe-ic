#!/usr/bin/env python3
"""Apply an explicitly declared TEXT-only GDS layer drop at stream-out.

The map is PDK data, selected through pdk_registry.json.  A pair absent from
the map is never removed.  A non-TEXT element on a mapped pair refuses the
transform.  The output retains every other GDS record byte-for-byte and gets
an atomic receipt binding the map, input, and output SHA-256 digests.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json  # noqa: E402

_HERE = Path(__file__).resolve().parent
_ELEMENTS = {0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x15, 0x2D}
_TEXT = 0x0C
_LAYER = 0x0D
_PURPOSES = {0x0E, 0x16, 0x2A, 0x2E}
_ENDEL = 0x11


class LayerMapError(ValueError):
    """The declared transform cannot prove that only mapped TEXT is removed."""


def map_for_pdk(pdk: str) -> Optional[Path]:
    """Resolve one PDK's map from the registry, without a PDK literal in logic."""
    registry = json.loads((_HERE / "pdk_registry.json").read_text())
    matches = [row for row in registry["pdks"] if row.get("name") == pdk]
    if not matches:
        return None
    if len(matches) != 1:
        raise LayerMapError(f"PDK registry has duplicate entries for {pdk!r}")
    rel = matches[0].get("streamout_text_layer_map")
    if rel is None:
        return None
    path = (_HERE / rel).resolve()
    if not path.is_relative_to(_HERE) or not path.is_file():
        raise LayerMapError(f"declared streamout TEXT map unavailable: {rel}")
    return path


def _pairs(path: Path) -> set[tuple[int, int]]:
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise LayerMapError(f"unreadable TEXT map {path}: {exc}") from exc
    if doc.get("schema") != "vibeic.gds_text_layer_map/1":
        raise LayerMapError("unknown TEXT map schema")
    values = doc.get("drop_text_pairs")
    if not isinstance(values, list) or not values:
        raise LayerMapError("TEXT map must declare a nonempty drop_text_pairs list")
    pairs = set()
    for value in values:
        if not isinstance(value, str) or value.count("/") != 1:
            raise LayerMapError(f"invalid TEXT map pair: {value!r}")
        left, right = value.split("/")
        if not left.isdecimal() or not right.isdecimal():
            raise LayerMapError(f"invalid TEXT map pair: {value!r}")
        pair = int(left), int(right)
        if not (0 <= pair[0] <= 65535 and 0 <= pair[1] <= 65535):
            raise LayerMapError(f"out-of-range TEXT map pair: {value!r}")
        pairs.add(pair)
    if len(pairs) != len(values) or not doc.get("source") or not doc.get("reason"):
        raise LayerMapError("TEXT map has duplicate pairs or lacks source/reason")
    return pairs


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _transform(source: Path, output: Path, drop: set[tuple[int, int]]) -> dict[str, int]:
    """Copy GDS records, omitting only complete mapped TEXT elements."""
    removed = {f"{layer}/{purpose}": 0 for layer, purpose in sorted(drop)}
    with source.open("rb") as inp, output.open("wb") as out:
        element: Optional[bytearray] = None
        kind: Optional[int] = None
        layer: Optional[int] = None
        purpose: Optional[int] = None
        first = True
        ended = False
        while raw_len := inp.read(2):
            if len(raw_len) != 2:
                raise LayerMapError("truncated GDS record length")
            length = struct.unpack(">H", raw_len)[0]
            if length < 4 or length % 2:
                raise LayerMapError(f"invalid GDS record length {length}")
            rest = inp.read(length - 2)
            if len(rest) != length - 2:
                raise LayerMapError("truncated GDS record")
            record = raw_len + rest
            code = rest[0]
            if first and code != 0x00:
                raise LayerMapError("GDS does not begin with HEADER")
            first = False
            if ended:
                raise LayerMapError("GDS has records after ENDLIB")
            if code in _ELEMENTS:
                if element is not None:
                    raise LayerMapError("nested GDS element")
                element = bytearray(record)
                kind, layer, purpose = code, None, None
                continue
            if element is not None:
                element.extend(record)
                if code == _LAYER:
                    if len(rest) != 4 or layer is not None:
                        raise LayerMapError("invalid or duplicate GDS LAYER")
                    layer = struct.unpack(">H", rest[2:])[0]
                elif code in _PURPOSES:
                    if len(rest) != 4 or purpose is not None:
                        raise LayerMapError("invalid or duplicate GDS purpose")
                    purpose = struct.unpack(">H", rest[2:])[0]
                elif code == _ENDEL:
                    pair = (layer, purpose)
                    if pair in drop:
                        if kind != _TEXT:
                            raise LayerMapError(
                                f"mapped TEXT pair {layer}/{purpose} has non-TEXT geometry")
                        removed[f"{layer}/{purpose}"] += 1
                    else:
                        out.write(element)
                    element = None
                continue
            if code == _ENDEL:
                raise LayerMapError("ENDEL outside a GDS element")
            out.write(record)
            if code == 0x04:
                ended = True
        if element is not None or not ended:
            raise LayerMapError("GDS ends before ENDEL or ENDLIB")
    return removed


def apply_map(gds: Path, map_path: Path) -> Path:
    """Transform one stream-out output atomically and write its SHA receipt."""
    gds, map_path = Path(gds), Path(map_path)
    drop = _pairs(map_path)
    source_sha = _sha(gds)
    map_sha = _sha(map_path)
    fd, temp_name = tempfile.mkstemp(prefix=f".{gds.name}.textmap-", dir=gds.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        removed = _transform(gds, temp, drop)
        output_sha = _sha(temp)
        os.replace(temp, gds)
    finally:
        temp.unlink(missing_ok=True)
    receipt = gds.with_name(f"{gds.stem}.text_layer_map.json")
    write_json(receipt, {
        "schema": "vibeic.gds_text_layer_map_receipt/1",
        "verdict": "APPLIED", "gds": str(gds),
        "map": str(map_path), "map_sha256": map_sha,
        "input_sha256": source_sha, "output_sha256": output_sha,
        "removed_text": removed,
    })
    return receipt


def apply_declared(gds: Path, pdk: str) -> Optional[Path]:
    """Apply the PDK's declared map, or leave the GDS alone if none exists."""
    map_path = map_for_pdk(pdk)
    return apply_map(gds, map_path) if map_path is not None else None
