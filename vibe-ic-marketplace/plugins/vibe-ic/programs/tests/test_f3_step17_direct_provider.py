import execution_adapters_backend as backend


def _routes(params):
    return backend._adapter_routes('17', params, True)



def test_step17_missing_direct_inputs_are_not_green():
    routes = {r['arm_id']: r for r in _routes({})}
    assert routes['backend_17_openroad']['applicability'] == 'inapplicable'
    assert routes['backend_17_openroad']['available'] is False
    routes = {r['arm_id']: r for r in _routes({'librelane_available': False})}
    assert all(not r['available'] for r in routes.values())



def test_step17_reverse_route_registration_is_detectable(monkeypatch):
    original = backend._adapter_routes
    monkeypatch.setattr(backend, '_adapter_routes',
                        lambda sid, params, available: original(sid, params, available)[:1])
    assert [r['arm_id'] for r in backend._adapter_routes('17', {
        'step15_state': '/issued/state.json', 'sta_config': '/issued/sta.json',
        'builder_input': '/issued/builder.json'}, True)] == ['backend_17_librelane']
    assert len(original('17', {'step15_state': '/issued/state.json',
                               'sta_config': '/issued/sta.json',
                               'builder_input': '/issued/builder.json'}, True)) == 2
