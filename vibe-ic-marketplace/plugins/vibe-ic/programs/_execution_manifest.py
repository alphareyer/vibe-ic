"""Exact Controller authority location, shared by issuance and consumers."""
from pathlib import Path


ISSUED_MANIFEST_ENV = 'VIBEIC_ISSUED_MANIFEST_PATH'


def issued_manifest_path(input_root):
    """Only this root file is metadata; nested names remain ordinary inputs."""
    return Path(input_root) / 'issued_manifest.json'
