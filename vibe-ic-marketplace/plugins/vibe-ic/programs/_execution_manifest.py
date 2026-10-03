"""Exact Controller authority location, shared by issuance and consumers."""
from pathlib import Path
import stat


ISSUED_MANIFEST_ENV = 'VIBEIC_ISSUED_MANIFEST_PATH'


def issued_manifest_path(input_root):
    """Only this root file is metadata; nested names remain ordinary inputs."""
    return Path(input_root) / 'issued_manifest.json'


def is_exclusive_regular(path):
    """Return true only for a Controller-owned, single-link regular file.

    ``Path.is_file`` follows links and cannot see an alias outside the frozen
    root.  The frozen authority/input contract therefore uses lstat so an
    outside hardlink is observable without inspecting the caller's filesystem.
    Ordinary caller inputs are intentionally checked by their existing
    source-binding rules; this predicate is for copied Controller-owned files.
    """
    try:
        info = Path(path).lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1
