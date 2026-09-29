"""A real verbose terminal is complete; a killed session remains incomplete."""
from pathlib import Path
import liar_census as lc


def test_actual_verbose_terminal_is_a_completed_measurement():
    terminal = (Path(__file__).parent / 'fixtures/pytest_verbose_terminal.txt').read_text()
    assert lc._PYTEST_DONE.search(terminal), terminal
    assert lc._PYTEST_PASSED.findall(terminal) == ['197']


def test_dead_verbose_session_has_no_completion_receipt():
    terminal = '============================= test session starts ==============================\n[gw0] PASSED test_case.py::test_one\n+++ Timeout +++\nStack of MainThread\n'
    assert lc._PYTEST_DONE.search(terminal) is None
