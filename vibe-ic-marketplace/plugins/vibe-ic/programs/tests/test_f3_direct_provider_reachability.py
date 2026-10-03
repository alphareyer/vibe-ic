import execution_adapters_backend as backend
import execution_backend_producers as producers
import execution_modes as em
import pytest


def test_absent_facts_do_not_issue_direct_route(tmp_path):
    params = {}
    assert backend._issue_direct_inputs(tmp_path, params, "15", {}) is None
    with pytest.raises(em.Refusal, match="DIRECT_PROVIDER_INPUTS_UNISSUED"):
        producers._produce_direct_pnr(tmp_path, {"step_id": "15"}, object())
