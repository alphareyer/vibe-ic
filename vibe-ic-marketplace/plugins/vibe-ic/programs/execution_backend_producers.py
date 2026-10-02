"""Explicit bounded backend producer sites. Never call main or step_pnr.

All native dispatch goes through F1's leased boundary in the worker. Existing
model/deck/RCX guards remain in the producer modules that own those semantics.
"""
from __future__ import annotations
import json
import os
import shutil
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_backend_snapshot as snap
import execution_modes as em

PNR = 'phase3/stage3/pnr/'
RPT = 'reports/phase3/'


def _require(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink() or not p.stat().st_size:
        raise em.Refusal('BACKEND_PRODUCER_INPUT_ABSENT', str(p))
    return p


def _copy(source, target):
    source = _require(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _parameters(project, params):
    import phase3_one_shot_runner as R
    import librelane_contract as ll
    missing = [name for name in ("pdk_name", "image_id", "pdk_root", "top")
               if not params.get(name)]
    if missing:
        raise em.Refusal('BACKEND_PRODUCER_PARAMS_UNBOUND', ','.join(missing))
    pdk = R._detect_pdk(project, params['pdk_name'])
    if pdk is None or str(pdk.name) != params['pdk_name']:
        raise em.Refusal('BACKEND_PDK_RESOLUTION_FAILED', params['pdk_name'])
    R.set_invocation_provenance_sink(project)
    # F1's native_boundary converts legacy dispatch into admitted Docker run.
    container = params.get('container', '')
    return R, ll, pdk, container


def _views(project, top, predecessor):
    import phase3_one_shot_runner as R
    nl, _, _ = R.pnr_input_netlist(project, top)
    routed = project / PNR / f'{top}_pnr.v'
    return {'def': _require(project / PNR / predecessor),
            'nl': _require(routed if routed.is_file() else nl),
            'sdc': _require(project / PNR / 'constraint.sdc')}


def _state_chain(project, params, ids, predecessor, overlay=None):
    import librelane_contract as ll
    import phase3_one_shot_runner as R
    image, pdk, root = params['image_id'], params['pdk_name'], Path(params['pdk_root'])
    lane = 'execution-backend-' + params['step_id'].replace('.', '-')
    configs = ll.resolve_step_configs(project, image, pdk, ids,
                    pdk_root=root, folder=lane + '-config', overlay=overlay)
    configs, _ = R._resolved_cell_policy(configs, root, pdk)
    mounts = [(root / pdk, '/pdk/' + pdk)]
    if params.get('state_in'):
        state = _require(Path(params['state_in']))
    else:
        state = ll.state_from_direct(project, image, configs[ids[0]],
                _views(project, params['top'], predecessor),
                project / 'phase3/librelane' / (lane + '-config/bridge'),
                chain=[configs[s] for s in ids[1:]], mounts=mounts)
    folders = ll.run_chain(project, image, [(s, configs[s], state) for s in ids],
                          mounts=mounts, lane=lane, pdk_root=ll.PDK_GUEST_ROOT)
    snap.dump(project / RPT / (lane + '.json'),
              {'ordered_steps': ids, 'initial_state': str(state),
               'initial_sha256': snap.sha(state),
               'states': [{'step': sid, 'state': str(f / 'state_out.json'),
                           'sha256': snap.sha(f / 'state_out.json')} for sid, f in zip(ids, folders)]})
    return ll, configs, dict(zip(ids, folders))


def _handoff(project, ll, folder, names):
    target = {view: project / name for view, name in names.items()}
    return ll.handoff_to_direct(folder / 'state_out.json', target,
               project / RPT / ('execution-backend-' + folder.parent.name + '-handoff.json'))


def _logs(project, folders, name='openroad.log'):
    target = project / PNR / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(''.join('# ' + str(f.relative_to(project)) + '/' + p.name + '\n' +
                     p.read_text(errors='replace') + '\n' for f in folders for p in sorted(f.glob('*.log'))))
    return target


def _step37_route(params):
    """Bind the worker's issued arm to the actual Step-37 runner route."""
    arm = os.environ.get('VIBEIC_ARM_ID')
    if arm is None:
        route = params.get('streamout_route')
        return route if route in ('direct', 'librelane') else None
    route = {'backend_37_librelane': 'librelane',
             'backend_37_direct': 'direct'}.get(arm)
    issued = os.environ.get('VIBEIC_STEP37_ROUTE')
    if (route is None or issued != route or
            params.get('route') != route or params.get('streamout_route') != route):
        raise em.Refusal('BACKEND_STEP37_ROUTE_UNBOUND', arm)
    return route


def _step37_streamout_result(project, route, row):
    """Persist and enforce the streamout engine emitted by the runner."""
    allowed = {'librelane': ('magic', 'klayout'), 'direct': ('magic',)}[route]
    observed = row.extras.get('streamout_engine')
    arm = os.environ.get('VIBEIC_ARM_ID') or 'backend_37_' + route
    receipt = project / RPT / 'step37_streamout' / 'vibeic_receipt.json'
    snap.dump(receipt, {
        'schema': 'vibeic/step37-streamout-engine/1',
        'step_id': '37', 'arm_id': arm, 'route': route,
        'allowed_streamout_engines': list(allowed),
        'streamout_engine': observed, 'status': row.status,
        'detail': row.detail,
    })
    if arm != 'backend_37_' + route or observed not in allowed:
        mismatch = (
            'BACKEND_STEP37_STREAMOUT_ENGINE_MISMATCH: '
            f'arm={arm} route={route} allowed={allowed} observed={observed!r}')
        return {'verdict': 'FAIL' if row.status == 'FAIL' else 'NOT_MEASURED',
                'detail': mismatch + '; ' + row.detail,
                'streamout_engine': observed, 'streamout_route': route}
    return {'verdict': row.status, 'detail': row.detail,
            'streamout_engine': observed, 'streamout_route': route}


def _cts_split(project, params, R, ll, pdk, overlay):
    """Selected predecessor -> CTS or resizer -> one measurement per corner.

    This preserves the original CTS/hold handoff reader's schema. Step 20
    inherits the actual step-19 state; it never re-runs CTS or floorplan.
    """
    import librelane_cts_hold as ch
    sid = params['step_id']
    root, image = Path(params['pdk_root']), params['image_id']
    lane = 'execution-backend-' + sid
    ids = (['OpenROAD.CTS', 'Vibeic.ClockPathDriveSizing', 'Vibeic.ExternalCaptureLaunchRetap']
           if sid == '19' else ['OpenROAD.ResizerTimingPostCTS'])
    scene = ch.signoff_scene_sdc(R, project / PNR / 'constraint.sdc',
                                project / 'phase3/librelane' / (lane + '-config'))
    overlay['PNR_SDC_FILE'] = (str(scene), 'selected SDC + accepted signoff OCV scene')
    configs = ll.resolve_step_configs(project, image, pdk.name, ids + ['OpenROAD.STAMidPNR'],
                    pdk_root=root, folder=lane + '-config', overlay=overlay)
    configs, policy_steps = R._resolved_cell_policy(configs, root, pdk.name,
                required=('OpenROAD.CTS',) if sid == '19' else ('OpenROAD.ResizerTimingPostCTS',))
    corners = json.loads(configs['OpenROAD.STAMidPNR'].read_text()).get('STA_CORNERS') or []
    if not corners:
        raise em.Refusal('BACKEND_CTS_HOLD_CORNERS_UNSTATED', sid)
    if sid == '19':
        instances = configs['OpenROAD.CTS'].parent / 'pre_cts.instances'
        components = R._parse_def_components(_require(project / PNR / 'placed.def'))
        if not components:
            raise em.Refusal('BACKEND_PRE_CTS_INSTANCE_POPULATION_EMPTY', sid)
        instances.write_text('\n'.join(name for name, _ in components) + '\n')
        sizing = instances.with_name('clock_path_drive_sizing.body.tcl')
        sizing.write_text(R._clock_path_drive_sizing_tcl())
        for step in ids[1:]:
            updates = {'PNR_CORNERS': (corners, 'resolved STA_CORNERS'),
                       'VIBEIC_CLKPATH_PRECTS_INSTANCES': (str(instances), 'selected placed DEF instance population')}
            if step == 'Vibeic.ClockPathDriveSizing':
                updates['VIBEIC_CLKPATH_SIZING_TCL'] = (str(sizing), 'accepted runner clock path sizing body')
            configs[step] = ll.derive_step_config(configs[step], configs[step], updates)
    chain = [(sid, configs[sid]) for sid in ids]
    for corner in corners:
        native = configs['OpenROAD.STAMidPNR']
        selected = native.with_name('OpenROAD.STAMidPNR@' + corner + '.json')
        ll.derive_step_config(native, selected, {'PNR_CORNERS': ([corner], 'resolved STA_CORNERS, one measurement per corner')})
        chain.append(('OpenROAD.STAMidPNR', selected))
    mounts = [(root / pdk.name, '/pdk/' + pdk.name)]
    views = _views(project, params['top'], 'placed.def' if sid == '19' else 'post_cts.def')
    state = (_require(Path(params['state_in'])) if params.get('state_in') else
             ll.state_from_direct(project, image, configs[ids[0]], views,
                     configs[ids[0]].parent / 'bridge', chain=[cfg for _, cfg in chain[1:]], mounts=mounts))
    folders = ll.run_chain(project, image, [(s, cfg, state) for s, cfg in chain],
                          mounts=mounts, lane=lane, pdk_root=ll.PDK_GUEST_ROOT)
    production = folders[len(ids) - 1]
    names = {'def': PNR + ('post_cts.def' if sid == '19' else 'post_hold.def'),
             'odb': PNR + ('post_cts.odb' if sid == '19' else 'post_hold.odb')}
    handoff = _handoff(project, ll, production, names)
    receipt_path = project / RPT / 'librelane_cts_hold_handoff.json'
    previous = json.loads(receipt_path.read_text()) if sid == '20' and receipt_path.is_file() else {}
    receipt = {**previous, 'program': 'execution_backend_producers._cts_split',
               'modes': {'19': 'librelane', '20': 'librelane'}, 'selected': 'librelane',
               'image': image, 'steps': [s for s, _ in chain], 'corners': corners,
               'chain': {**previous.get('chain', {}), **{s: str(f.relative_to(project)) for (s, _), f in zip(chain, folders)}},
               'measured_state': str((folders[-1] / 'state_out.json').relative_to(project)),
               'measured_state_sha256': snap.sha(folders[-1] / 'state_out.json'),
               'views': dict(previous.get('views', {}))}
    for view, info in handoff['views'].items():
        receipt['views']['post_' + ('cts' if sid == '19' else 'hold') + '_' + view] = {
            **info, 'dest': str(Path(info['dest']).relative_to(project))}
    if sid == '19':
        report = _require(folders[0] / 'cts.rpt')
        destination = project / 'phase3/stage3/cts/clock_tree.rpt'
        _copy(report, destination)
        receipt['views']['cts_rpt'] = {'source': str(report), 'source_sha256': snap.sha(report),
                                     'dest': str(destination.relative_to(project)), 'dest_sha256': snap.sha(destination)}
    else:
        before_area = json.loads(state.read_text()).get('metrics', {}).get('design__instance__area__stdcell')
        after_metrics = json.loads((production / 'state_out.json').read_text()).get('metrics', {})
        snap.dump(project / RPT / 'pnr/hold_area.json', {
            'program': 'execution_backend_producers._cts_split', 'before_total_area': before_area,
            'after_total_area': after_metrics.get('design__instance__area__stdcell'),
            'hold_buffer_count': after_metrics.get('design__instance__count__hold_buffer'),
            'setup_buffer_count': after_metrics.get('design__instance__count__setup_buffer'),
            'sources': {'before': str(state.relative_to(project)), 'after': str((production / 'state_out.json').relative_to(project))},
            'numerator_basis': 'accepted resizer stdcell-area delta; no pad-area dilution'})
    snap.dump(receipt_path, receipt)
    _logs(project, folders)
    return {'verdict': 'PASS', 'detail': 'native CTS/hold chain measured every resolved corner; gates judge its values'}


def produce_pnr(project, params):
    R, ll, pdk, container = _parameters(project, params)
    sid, top = params['step_id'], params['top']
    overlay = {k: tuple(v) for k, v in (params.get('overlay') or {}).items()}
    if sid == '15':
        # Keep the runner's input-derived die/core and pad geometry front door.
        if 'die_um' not in params or 'util' not in params:
            raise em.Refusal('BACKEND_FLOORPLAN_OBJECTIVES_UNSTATED', sid)
        prep = R.step_prepnr(project, top, pdk, container,
                            params['die_um'], params['util'])
        if prep.status != 'PASS':
            return {'verdict': prep.status, 'detail': prep.detail}
        if R._chip_path_requests_pad_ring(project):
            # The accepted ring-pinned producer retains its tap coverage,
            # master free-run exclusion, pad connection and PDN semantics.
            row, _ = R._prepare_librelane_floorplan_for_route(
                project, pdk, container, project / PNR, '',
                {'15': 'librelane', '15.5ic': 'librelane'})
            return {'verdict': row.status, 'detail': row.detail}
        ids = ['Yosys.JsonHeader'] + ll.flow_segment(params['image_id'],
                    'OpenROAD.Floorplan', 'Odb.RemovePDNObstructions', flow='Classic')
        # Floorplan starts from a netlist, not from a fabricated DEF bridge.
        nl, _, _ = R.pnr_input_netlist(project, top)
        configs = ll.resolve_step_configs(project, params['image_id'], pdk.name, ids,
                     pdk_root=Path(params['pdk_root']), folder='execution-backend-15-config', overlay=overlay)
        configs, _ = R._resolved_cell_policy(configs, Path(params['pdk_root']), pdk.name)
        mounts = [(Path(params['pdk_root']) / pdk.name, '/pdk/' + pdk.name)]
        state = ll.state_from_direct(project, params['image_id'], configs[ids[0]],
                     {'nl': _require(nl)}, project / 'phase3/librelane/execution-backend-15-config/bridge',
                     chain=[configs[s] for s in ids[1:]], mounts=mounts)
        folders = ll.run_chain(project, params['image_id'], [(s, configs[s], state) for s in ids],
                              mounts=mounts, lane='execution-backend-15', pdk_root=ll.PDK_GUEST_ROOT)
        by = dict(zip(ids, folders))
        _handoff(project, ll, folders[-1], {'def': PNR + 'floorplan.def',
                                           'odb': PNR + 'librelane_floorplan.odb'})
        pdn_step = by.get('OpenROAD.GeneratePDN')
        if pdn_step is None:
            raise em.Refusal('BACKEND_PDN_NOT_EXECUTED', sid)
        tap = by.get('OpenROAD.TapEndcapInsertion')
        if tap:
            tap_def = Path(json.loads((tap / 'state_out.json').read_text())['def'])
            rc, detail = R._librelane_tap_coverage(project, pdk, container, tap_def, 'lattice')
            if rc:
                return {'verdict': 'FAIL' if rc == 1 else 'NOT_MEASURED', 'detail': detail}
        # The done marker is a bound completed native PDN state, not rc0 alone.
        snap.dump(project / PNR / 'pdn.done', {'state': str(pdn_step / 'state_out.json'),
                  'sha256': snap.sha(pdn_step / 'state_out.json')})
    elif sid == '15.5ic':
        row = R.step_pad_ring_gen(project, container, pdk)
        return {'verdict': row.status, 'detail': row.detail}
    elif sid == '17':
        ids = ll.flow_segment(params['image_id'], 'OpenROAD.GlobalPlacement', 'OpenROAD.DetailedPlacement')
        at = ids.index('OpenROAD.RepairDesignPostGPL')
        ids.insert(at + 1, 'Vibeic.PostGPLFanoutClosure')
        ll, configs, by = _state_chain(project, params, ids, 'floorplan.def', overlay)
        _handoff(project, ll, by[ids[-1]], {'def': PNR + 'placed.def', 'odb': PNR + 'librelane_placed.odb'})
        _logs(project, by.values(), 'librelane_placement.log')
    elif sid == '18':
        ids = ['Vibeic.InsertSpareCells']
        ll, configs, by = _state_chain(project, params, ids, 'placed.def', overlay)
        _handoff(project, ll, by[ids[-1]], {'def': PNR + 'placed.def', 'odb': PNR + 'librelane_placed.odb'})
        _logs(project, by.values(), 'librelane_spare_cells.log')
        reports = list(by[ids[-1]].rglob('spare_cells.json'))
        if len(reports) != 1:
            raise em.Refusal('BACKEND_SPARE_NATIVE_RECORD_ABSENT', str(reports))
        _copy(reports[0], project / PNR / 'spare_cells.json')
        R._emit_spare_cell_coverage(project, project / PNR / 'spare_cells.json')
    elif sid in ('19', '20'):
        return _cts_split(project, params, R, ll, pdk, overlay)
    elif sid == '21':
        import librelane_route as route
        ids = route.chain_ids(params['image_id'], gated_off=ll.flow_gated_off(params['image_id']))
        ll, configs, by = _state_chain(project, params, ids, 'post_hold.def', overlay)
        _handoff(project, ll, by[ids[-1]], {'def': PNR + 'routed.def', 'odb': PNR + 'routed.odb',
                                         'nl': PNR + top + '_pnr.v'})
        log = _logs(project, by.values())
        reports = list(by['OpenROAD.DetailedRouting'].rglob('*.drc'))
        if len(reports) != 1:
            raise em.Refusal('BACKEND_ROUTER_NATIVE_DRC_ABSENT', str(reports))
        _copy(reports[0], project / PNR / R.ROUTER_DRC_REPORT_NAME)
        # Use the accepted receipt emitter; an incomplete routing transcript
        # refuses instead of creating a zero-error router certificate.
        if R._write_router_drc_receipt(project / PNR, project / PNR / 'routed.def', log.read_text()) is None:
            raise em.Refusal('BACKEND_ROUTER_TERMINAL_UNMEASURED', sid)
        R._emit_router_drc_report(project, project / PNR, project / RPT, [])
    else:
        raise em.Refusal('BACKEND_UNKNOWN_PNR_STEP', sid)
    return {'verdict': 'PASS', 'detail': 'native ordered chain complete; canonical gates still required'}


def produce_signoff(project, params):
    R, ll, pdk, container = _parameters(project, params)
    sid, top = params['step_id'], params['top']
    notes, written, failures = [], [], []
    pnr, rpt = project / PNR, project / RPT
    extracted = project / 'phase3/stage3/extracted'
    spef = extracted / (top + '.spef')
    root, image = Path(params['pdk_root']), params['image_id']
    if sid == '22':
        # Current fivegap binding/finite-C/non-alias guards are inherited here.
        R._librelane_rcx_publish(project, top, pdk, spef,
                    extracted / 'spef_corners', rpt / 'librelane_rcx_handoff.json', refresh=True)
    elif sid == '23':
        corners = R._librelane_handed_spefs(rpt / 'librelane_rcx_handoff.json')
        if not corners:
            raise em.Refusal('BACKEND_CURRENT_RCX_HANDOFF_ABSENT', sid)
        nominal = _require(spef)
        sta = project / 'phase3/stage3/sta'
        sta.mkdir(parents=True, exist_ok=True)
        if not R._emit_spef_sta(project, top, pdk, container, nominal, sta / 'sta_spef_based.rpt', notes):
            raise em.Refusal('BACKEND_STA_NATIVE_UNMEASURED', sid)
        _copy(sta / 'sta_spef_based.rpt', sta / 'post_route_timing.rpt')
        _copy(sta / 'sta_spef_based.rpt', rpt / 'sta_spef_based.rpt')
        R._librelane_signoff_record(project, top, pdk, 'librelane', 'librelane',
              extracted / 'spef_corners', sta / 'multi_corner_ocv_sta.rpt', notes, failures, written)
        if failures:
            return {'verdict': 'FAIL', 'detail': '\n'.join(failures)}
    elif sid == '24':
        R._librelane_step24_record(project, top, pdk, 'librelane', _require(spef), written)
        record = json.loads((rpt / R._LL_IR_RECORD).read_text())
        if record.get('record'):
            R._librelane_step24_publish(project, record)
        else:
            R._librelane_step24_refusal_publish(project, record)
    elif sid == '25':
        # Same SDC+SPEF/VSRC basis as the IR pipeline. Both outputs are retained;
        # the existing EM authority judges drawn width and foundry Jmax.
        ok = R._emit_ir_em_reports(project, top, pdk, container, rpt / 'ir_drop.rpt', rpt / 'em.rpt', notes)
        if not ok[1]:
            raise em.Refusal('BACKEND_EM_NATIVE_UNMEASURED', sid)
        R._emit_em_current_authority(project, pdk, container, notes)
    elif sid == '26':
        R._librelane_antenna_router(project, top, pdk, 'librelane', written)
        gds = pnr / (top + '.gds')
        if gds.is_file():
            R._librelane_antenna_gds(project, top, pdk, 'librelane', written)
    elif sid == '26.5ic':
        ok, detail = R._die_finishing(project, top, pdk, _require(pnr / (top + '.gds')), container)
        if not ok:
            return {'verdict': 'NOT_MEASURED', 'detail': detail}
    elif sid == '27':
        import si_mcf_sta as mcf
        import si_signoff_timing_aware as si
        timing = rpt / 'si_timing.json'
        nl = _require(pnr / (top + '_pnr.v'))
        sdc = _require(pnr / 'constraint.sdc')
        voltage = R._pdk_nominal_voltage(pdk, container)
        if voltage is None:
            raise em.Refusal('BACKEND_SI_VOLTAGE_UNSTATED', sid)
        if not R._emit_si_timing_json(project, top, pdk, container, _require(spef), sdc, nl, timing, notes, voltage):
            raise em.Refusal('BACKEND_SI_NATIVE_UNMEASURED', sid)
        si.run_si_signoff_timing_aware(spef, timing, vdd_v=voltage,
                        out_json=rpt / 'si_crosstalk.json', out_rpt=rpt / 'si_crosstalk.rpt')
        mcf.run(project, container=container, spef=str(spef), netlist=str(nl),
                sdc=str(sdc), liberty=pdk.liberty, top=top, macro_libs=pdk.macro_libs,
                vdd_v=voltage, out_json=rpt / 'si_mcf_sta.json', timeout=int(params['timeout_s']))
    elif sid == '28':
        if not R._emit_perc_equivalent(project, top, pdk, container, notes):
            raise em.Refusal('BACKEND_PERC_UNMEASURED', sid)
    elif sid == '29':
        sim = project / 'phase3/stage3/sim_postlayout'
        sim.mkdir(parents=True, exist_ok=True)
        sdf = sim / (top + '.sdf')
        if not R._emit_sdf(project, top, pdk, container, sdf, notes):
            raise em.Refusal('BACKEND_SDF_NATIVE_UNMEASURED', sid)
        R._step29_tool_arm(project, top, pdk, 'librelane', sim, sdf, written, notes)
    elif sid == '30':
        import path_spice_tool as spice
        from execution_adapters_backend import _step30_simulators
        result = spice.run_step30(project, image, root, pdk.name,
                          paths=params.get('spice_paths', 1), simulators=tuple(_step30_simulators(params)))
        # Tool refusal/model gaps remain explicit. Existing correlation gates
        # judge the record at its declared canonical output path.
        # Keep the native tool report in its own schema. The correlation
        # consumer reads it explicitly; never relabel it as a legacy report.
        import spice_correlation_check as correlation
        audited = correlation.run_audit(project, run_spice=False, container=container)
        from dataclasses import asdict
        snap.dump(rpt / 'spice_correlation.json', asdict(audited))
        if result.get('verdict') != 'PASS':
            return {'verdict': result.get('verdict', 'NOT_MEASURED'), 'detail': str(result.get('reason', ''))}
    elif sid == '32':
        row = R.step_postroute_repair_librelane(project, top, pdk, container)
        return {'verdict': row.status, 'detail': row.detail}
    elif sid == '33':
        # Run OpenSTA's native report_power on the selected current SPEFs.
        # The report reader below consumes the newly issued STA state.
        R._librelane_signoff_record(project, top, pdk, 'librelane', 'librelane',
              extracted / 'spef_corners', project / 'phase3/stage3/sta/multi_corner_ocv_sta.rpt',
              notes, failures, written)
        R._step33_tool_arm(project, top, pdk, 'librelane', rpt / 'power.rpt', False, written, notes)
    elif sid == '34':
        if not R._emit_metal_fill(project, top, pdk, container, pnr / 'filled.def', notes):
            raise em.Refusal('BACKEND_FILL_NATIVE_UNMEASURED', sid)
        if not R._emit_metal_density_report(project, top, pdk, container, rpt / 'density.json', notes):
            raise em.Refusal('BACKEND_DENSITY_NATIVE_UNMEASURED', sid)
    elif sid == '37':
        pre = R.step_prestream_gate(project, top, pdk, container)
        if pre.status != 'PASS':
            return {'verdict': pre.status, 'detail': pre.detail}
        route = _step37_route(params)
        row = R.step_gds(project, top, pdk, container, route=route)
        if row.status == 'PASS':
            _copy(pnr / (top + '.gds'), project / 'phase3/stage4/gds' / (top + '.gds'))
        return _step37_streamout_result(project, route, row)
    elif sid == '37.3':
        import gds_xor_check as xor
        rc = xor.main([str(project), '--json', str(rpt / 'gds_xor.json'), '--container', container])
        return {'verdict': 'PASS' if rc == 0 else 'FAIL' if rc == 1 else 'NOT_MEASURED', 'detail': f'gds_xor producer rc={rc}'}
    elif sid == '31':
        # Complementary axes ALL execute. No DRC winner can hide a failing LVS.
        drc = R.step_drc(project, top, pdk, container)
        lvs = R.step_lvs(project, top, pdk, container, upstream_pnr=None)
        R._emit_erc_report(project, top, pdk, container, rpt / 'erc.rpt', notes)
        import perc_corpus_sweep as sweep
        sweep.main([str(project), '--report', str(rpt / 'perc_sweep.json')])
        statuses = [drc.status, lvs.status]
        return {'verdict': 'FAIL' if 'FAIL' in statuses else 'PASS' if statuses == ['PASS', 'PASS'] else 'NOT_MEASURED',
                'detail': 'DRC: ' + drc.detail + '\nLVS: ' + lvs.detail}
    else:
        raise em.Refusal('BACKEND_UNKNOWN_SIGNOFF_STEP', sid)
    return {'verdict': 'PASS', 'detail': '\n'.join(notes)}


def produce(project, params):
    if params['step_id'] in ('15', '15.5ic', '17', '18', '19', '20', '21'):
        result = produce_pnr(project, params)
    else:
        result = produce_signoff(project, params)
    # These are the existing canonical report producers, scoped to this row.
    # They consume actual native outputs; they do not stand in for native work.
    import phase3_one_shot_runner as R
    sid = params['step_id']
    names = {'23': {'sta_signoff', 'sta_corner', 'sta_record', 'sta_architectural_residual'},
             '24': {'ir_drop'}, '25': {'em_signoff'}, '26': {'antenna'}}.get(sid, set())
    table = {row[0]: row for row in R._DECLARED_SIGNOFF_GATES}
    table.update({row[0]: row for row in R._PRESTREAM_GATES})
    rows = []
    for name in ('sta_signoff', 'sta_corner', 'sta_record', 'sta_architectural_residual', 'ir_drop', 'em_signoff', 'antenna'):
        if name not in names:
            continue
        _, program, output, extra = table[name]
        rows.append(R._run_declared_signoff_gate(project, name, program, output, extra))
    if any(row.status == 'FAIL' for row in rows):
        result['verdict'] = 'FAIL'
    elif result['verdict'] == 'PASS' and any(row.status != 'PASS' for row in rows):
        result['verdict'] = 'NOT_MEASURED'
    # This is the producer's own current-tree evidence, not a fabricated native
    # tool receipt.  Native receipts remain an empty, separately named field;
    # the worker can therefore distinguish source evidence from EDA execution.
    result['canonical_receipts'] = [{
        'schema': 'vibeic/backend-producer-receipt/1',
        'producer': 'execution_backend_producers.produce',
        'step_id': sid,
        'verdict': result.get('verdict', 'NOT_MEASURED'),
        'detail': result.get('detail', ''),
    }]
    result.setdefault('native_receipts', [])
    return result
