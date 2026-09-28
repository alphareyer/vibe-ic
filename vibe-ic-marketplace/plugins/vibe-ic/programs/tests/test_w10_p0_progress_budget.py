"""Wave 10: a progressing front end survives its recorded wall budget."""

from pathlib import Path
import sys
import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import p0_tool_frontend_check as frontend  # noqa: E402


@pytest.mark.parametrize("hide_timeout", [False, True])
def test_progressing_frontend_outlives_recorded_budget(monkeypatch, tmp_path,
                                                        hide_timeout):
    monkeypatch.setenv(frontend.DEADLINE_ENV, "0.1")
    monkeypatch.setenv(frontend.STALL_ENV, "3")
    if hide_timeout:
        original_which = frontend.shutil.which
        monkeypatch.setattr(frontend.shutil, "which",
                            lambda name: None if name == "timeout"
                            else original_which(name))
    code = ("import time\n"
            "end = time.monotonic() + 1.2\n"
            "n = 0\n"
            "while time.monotonic() < end:\n"
            "    n += 1\n"
            "print('finished', flush=True)\n")
    result = frontend._invoke(sys.executable, ["-c", code], tmp_path, None)
    assert result.returncode == 0
    assert result.stdout.strip() == "finished"
    assert "P0_FRONTEND_BUDGET_EXCEEDED" in result.stderr
