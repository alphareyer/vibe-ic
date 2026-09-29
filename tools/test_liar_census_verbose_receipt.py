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


def test_native_duration_diagnostic_is_not_a_completion_receipt():
    terminal = (Path(__file__).parent / 'fixtures/pytest_dead_diagnostic.txt').read_text()
    assert lc._PYTEST_DONE.search(terminal) is None


def test_verbose_failure_label_does_not_steal_the_next_line():
    terminal = (Path(__file__).parent / 'fixtures/pytest_verbose_mixed.txt').read_text()
    assert lc._PYTEST_DONE.search(terminal)
    assert set(lc._PYTEST_FAILED.findall(terminal)) == {'test_neutral.py::test_fail'}


def test_only_complete_summary_with_paired_decoration_is_accepted():
    for terminal in ['worker stopped in 0.01s ===', '1 passed in 0.01s ===', '=== 1 passed in 0.01s', '=== no tests ran in 0.01s']:
        assert lc._PYTEST_DONE.search(terminal + '\n') is None, terminal
