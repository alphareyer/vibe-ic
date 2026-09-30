"""Current physical obligations from producer State, without invoking EDA.

This reader deliberately refuses incomplete captured packets. A receipt hash
is not a substitute for its material inputs, and Step 31's old stream cannot
certify Step 37's finished stream. The operator's precheck is a separate caller.
No producer/default policy is changed here; root composes those contracts.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import librelane_pv_signoff as pv
from librelane_contract import Refusal, _walk_paths, digest, selected_mode

NM = pv.NOT_MEASURED
PROMOTION = 'phase3/librelane/37-promotion.json'
# Independent engines own independent counts. Netgen's carried metrics in a
# KLayout State are never a second engine's result (CUT31 captured contract).
COUNTS = {
    'drc': {'Magic.DRC': ('magic__drc_error__count',),
            'KLayout.DRC': ('klayout__drc_error__count',)},
    'lvs': {'Magic.SpiceExtraction': ('magic__illegal_overlap__count',),
            'Netgen.LVS': ('design__lvs_error__count',
                           'design__lvs_unmatched_device__count',
                           'design__lvs_unmatched_net__count',
                           'design__lvs_unmatched_pin__count'),
            'KLayout.LVS': ('klayout__lvs_error__count',)},
    'density': {'KLayout.Density': ('klayout__density_error__count',)},
    'antenna': {'OpenROAD.CheckAntennas': ('antenna__violating__nets', 'antenna__violating__pins'),
                'KLayout.Antenna': ('klayout__antenna_error__count',)},
}


def _read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f'not an object: {path}')
    return value


def _require(ok: Any, reason: str) -> None:
    if not ok:
        raise ValueError(reason)


def _member(doc: dict, key: str) -> dict:
    value = doc.get(key)
    if value is None:
        return {}
    _require(isinstance(value, dict), f'LL_MALFORMED_OBJECT: {key}')
    return value


def _path(project: Path, value: Any) -> Path:
    _require(isinstance(value, str) and value, 'missing path')
    path = Path(value)
    return path if path.is_absolute() else project / path


def tool_selected(project: Path, step: str = '31') -> bool:
    """Unreadable selection is a refusal, never authority for direct fallback.

    As in state_metric, dual retains the direct authority and its tool
    cross-check. This reader does not change that producer selection policy.
    """
    try:
        return selected_mode(project, step) == 'librelane'
    except (Refusal, OSError, ValueError, TypeError):
        return True


def finished_layout(project: Path, layout: Path | None = None) -> Path:
    """Resolve one declared finished stream; prove any tool promotion's bytes."""
    project = project.resolve()
    if tool_selected(project, '37'):
        promo = _read(project / PROMOTION)
        arm = promo.get('selection')
        _require(arm in ('magic', 'klayout'), 'LL_FINISHED_SELECTION_UNBOUND')
        canonical = _path(project, promo.get('canonical')).resolve()
        source = _path(project, promo.get('source')).resolve()
        final = _path(project, _member(promo, 'finished').get(arm)).resolve()
        _require(canonical.is_relative_to(project / 'phase3/stage4/gds'),
                 'LL_FINISHED_NOT_DECLARED_STREAMOUT')
        _require(source == final, 'LL_FINISHED_SELECTION_SOURCE_MISMATCH')
        _require(source.is_file() and canonical.is_file(), 'LL_FINISHED_MATERIAL_MISSING')
        sha = digest(canonical)
        _require(sha == promo.get('canonical_sha256') == promo.get('source_sha256')
                 == digest(source), 'LL_FINISHED_GDS_CHANGED')
        if layout is not None:
            _require(layout.is_file() and digest(layout) == sha,
                     'LL_PRECHECK_SELECTED_GDS_MISMATCH')
        return canonical
    paths = sorted(p for p in (project / 'phase3/stage4/gds').glob('*')
                   if p.is_file() and p.suffix.lower() in ('.gds', '.gds2', '.gdsii', '.oas'))
    _require(paths, 'LL_FINISHED_GDS_MISSING')
    _require(len({digest(p) for p in paths}) == 1, 'LL_FINISHED_GDS_AMBIGUOUS')
    if layout is not None:
        _require(layout.is_file() and digest(layout) == digest(paths[0]),
                 'LL_PRECHECK_SELECTED_GDS_MISMATCH')
    return paths[0]


def _image(project: Path, switch: dict) -> str:
    image = switch.get('image') or os.environ.get('VIBEIC_LIBRELANE_IMAGE')
    if not image:
        rec = _read(project / 'reports/container_image.json')
        _require(rec.get('image_match') is True, 'LL_RUN_IMAGE_UNVERIFIED')
        image = rec.get('image_ref')
        if not re.search(r'@sha256:[0-9a-f]{64}$', str(image)):
            held = rec.get('image_repo_digests') or []
            _require(len(held) == 1, 'LL_RUN_IMAGE_DIGEST_UNBOUND')
            image = held[0]
    _require(isinstance(image, str) and re.search(r'@sha256:[0-9a-f]{64}$', image),
             'LL_IMAGE_NOT_IMMUTABLE')
    return image


def _pdk_mounts(project: Path, switch: dict, folder: Path, image: str) -> list:
    """Use the actual root producer schema; never materialize/re-resolve it."""
    root_rec = _read(project / 'phase3/librelane_pdk_root.provenance.json')
    derivation = _member(root_rec, 'derivation')
    from tapeout_precheck import resolve_pdk
    declared, _ = resolve_pdk(project)
    pdk = declared or switch.get('pdk')
    _require(isinstance(pdk, str) and pdk, 'LL_PDK_UNDECLARED')
    _require(not switch.get('pdk') or switch['pdk'] == pdk, 'LL_PDK_DECLARATION_MISMATCH')
    root = Path(switch.get('pdk_root_host') or os.environ.get('VIBEIC_LIBRELANE_PDK_ROOT')
                or root_rec.get('path') or '')
    _require(root.is_absolute() and root.is_dir(), 'LL_PDK_MATERIAL_MISSING')
    _require(root.resolve() == Path(root_rec.get('path') or '').resolve(), 'LL_PDK_ROOT_CHANGED')
    if root_rec.get('source') == 'resolved':
        _require(derivation.get('image') == image and derivation.get('pdk') == pdk,
                 'LL_PDK_IMAGE_OR_PROCESS_CHANGED')
        _require(Path(derivation.get('host_path') or '').resolve() == (root / pdk).resolve(),
                 'LL_PDK_TREE_CHANGED')
    else:
        _require(root_rec.get('source') == 'declared' and root_rec.get('pdk') == pdk,
                 'LL_PDK_ROOT_UNBOUND')
    rec = _read(folder / 'pdk_root.json')
    _require(rec.get('cli_pdk_root') == '/pdk'
             and rec.get('stated_by') == 'run_chain(pdk_root=...)', 'LL_PDK_ROOT_UNSTATED')
    mounts = rec.get('mounts_under_it')
    _require(isinstance(mounts, list) and mounts, 'LL_PDK_MOUNT_MISSING')
    expected = (root / pdk).resolve()
    matches = []
    for host, guest in mounts:
        host = Path(host).resolve()
        if guest == '/pdk':
            matches.append(host / pdk)
        elif guest == f'/pdk/{pdk}':
            matches.append(host)
        else:
            raise ValueError('LL_PDK_FOREIGN_MOUNT')
    _require(matches and all(p.resolve() == expected for p in matches)
             and expected.is_dir(), 'LL_PDK_MOUNT_CHANGED')
    return mounts


def _hashes(paths: dict) -> None:
    _require(isinstance(paths, dict), 'LL_INPUT_FINGERPRINT_MALFORMED')
    for name, sha in paths.items():
        path = Path(name)
        _require(path.is_absolute() and path.is_file(), f'LL_INPUT_MATERIAL_MISSING: {name}')
        _require(digest(path) == sha, f'LL_INPUT_CHANGED: {name}')


def count_row(project: Path, folder: Path, step: str, key: str,
              layout: Path, config_root: Path, *, subject_view: str = 'gds') -> dict:
    """Validate one real run_chain receipt and its producer-owned count.

    The runtime config and state_in are normalized by LibreLane; their hashes
    therefore differ from the original config/input fingerprint. Validate BOTH
    identities, not a guessed equality between those two serialization forms.
    """
    source = folder / 'state_out.json'
    out = {'value': NM, 'source': str(source), 'step': step, 'key': key}
    try:
        _require(folder.resolve().is_relative_to(project.resolve()), 'LL_FOREIGN_PRODUCER_FOLDER')
        receipt = _read(folder / 'vibeic_receipt.json')
        fp = receipt.get('input')
        _require(isinstance(fp, dict) and fp == _read(folder / 'input_fingerprint.json'),
                 'LL_RECEIPT_INPUT_MISMATCH')
        _require(fp.get('step') == step, 'LL_METRIC_WRONG_PRODUCER')
        hashes = receipt.get('sha256')
        _require(isinstance(hashes, dict), 'LL_RECEIPT_HASHES_MISSING')
        for name in ('state_out.json', 'state_in.json', 'config.json',
                     'input_fingerprint.json', 'pdk_root.json'):
            _require(name in hashes, f'LL_RECEIPT_MISSING: {name}')
        for name, sha in hashes.items():
            path = (folder / name).resolve()
            _require(path.is_relative_to(folder.resolve()) and path.is_file()
                     and digest(path) == sha, f'LL_RECEIPT_CHANGED_OR_MISSING: {name}')
        switch_path = project / 'phase3/librelane_switch.json'
        switch = _read(switch_path) if switch_path.is_file() else {}
        image = _image(project, switch)
        _require(fp.get('image') == image, 'LL_IMAGE_CHANGED')
        mounts = _pdk_mounts(project, switch, folder, image)
        config = config_root / f'{step}.json'
        _require(config.is_file() and digest(config) == fp.get('config'), 'LL_CONFIG_CHANGED_OR_MISSING')
        raw_config = _read(config)
        _require(_member(raw_config, 'meta').get('step') == step
                 and _member(_read(folder / 'config.json'), 'meta').get('step') == step,
                 'LL_CONFIG_WRONG_STEP')
        _hashes(fp.get('state_files'))
        _require(all(Path(p).resolve().is_relative_to(project.resolve())
                     for p in fp['state_files']), 'LL_FOREIGN_INPUT_SOURCE')
        _hashes(fp.get('config_files'))
        # Every PDK/config file actually named must have a producer hash, also
        # when its name is a container path. Older receipts lacking these
        # material bindings remain incomplete; root owns producer API repair.
        for path in _walk_paths(raw_config):
            translated = path
            for host, guest in mounts:
                if path.is_relative_to(guest):
                    translated = Path(host) / path.relative_to(guest)
                    break
            if translated.is_dir():
                # Resolver directory variables are not file inputs in
                # run_chain's fingerprint; the PDK mount is bound above.
                continue
            _require(translated.is_file(), f'LL_CONFIG_MATERIAL_MISSING: {path}')
            sha = (fp.get('config_files') or {}).get(str(path)) or \
                  (fp.get('config_files') or {}).get(str(translated))
            _require(sha == digest(translated), f'LL_CONFIG_INPUT_UNBOUND: {path}')
        before = _read(folder / 'state_in.json')
        after = _read(source)
        files = {str(p): digest(p) for p in _walk_paths(
            {k: v for k, v in before.items() if k != 'metrics'})}
        _require(files == fp.get('state_files'), 'LL_STATE_INPUT_FILES_MISMATCH')
        _require(after.get(subject_view) == before.get(subject_view), 'LL_MEASUREMENT_VIEW_CHANGED')
        # Original input bytes are retained in the chain / bridge. Do not
        # accept a fingerprint whose original State no longer exists.
        candidates = list((project / 'phase3/librelane').rglob('state_out.json'))
        candidates += list((project / 'phase3/librelane').rglob('state_in.json'))
        candidates += list((project / 'phase3/librelane').rglob('*state.json'))
        _require(any(p.is_file() and digest(p) == fp.get('state') for p in candidates),
                 'LL_ORIGINAL_INPUT_STATE_MISSING')
        gds = _path(project, before.get(subject_view))
        _require(gds.is_file() and digest(gds) == digest(layout), 'LL_SELECTED_GDS_MISMATCH')
        if step == 'Magic.SpiceExtraction':
            _require(raw_config.get('MAGIC_EXT_USE_GDS') is True
                     and raw_config.get('MAGIC_EXT_ABSTRACT') is False,
                     'LL_EXTRACTION_NOT_SHIPPED_GDS')
        metrics = after.get('metrics')
        _require(isinstance(metrics, dict), 'LL_STATE_METRICS_MALFORMED')
        value = metrics.get(key)
        _require(type(value) is int and value >= 0, 'LL_COUNT_NOT_TYPED_NONNEGATIVE')
        _require(key in pv.produced_metrics(folder), 'LL_METRIC_INHERITED_OR_ABSENT')
        _require(any(key in kinds.get(step, ()) for kinds in COUNTS.values()) or
                 key in pv.PRODUCED.get(step, ()), 'LL_METRIC_NOT_OWNED')
        out.update(value=value, sha256=digest(source), receipt=str(folder / 'vibeic_receipt.json'),
                   gds_sha256=digest(layout), image=image)
    except (OSError, ValueError, TypeError, KeyError, Refusal) as exc:
        out['reason'] = str(exc)
    return out


def _verdict(rows: dict) -> str:
    values = [row['value'] for row in rows.values()]
    return ('FAIL' if any(type(v) is int and v > 0 for v in values) else
            NM if not values or any(v == NM for v in values) else 'PASS')


def obligation(project: Path, kind: str, layout: Path | None = None) -> dict | None:
    """None means direct authority; NM never permits legacy fallback."""
    step = {'density': '34', 'antenna': '26'}.get(kind, '31')
    final_stream = kind in ('drc', 'density') and tool_selected(project, '37')
    if not final_stream and not tool_selected(project, step):
        return None
    rows = {}
    try:
        chosen = finished_layout(project, layout)
        # Final Step37 density is its own producer. Other metrics remain on
        # Step31; a stale Step31 record is not replaced by a legacy report.
        if kind == 'antenna':
            # Actual librelane_ir_antenna producer model schema. The models
            # keep their independent metric ownership and material subjects.
            path = project / 'reports/phase3/antenna_librelane.json'
            record = _read(path)
            for model_name, producer, view in (
                    ('gds', 'KLayout.Antenna', 'gds'),
                    ('router', 'OpenROAD.CheckAntennas', 'def')):
                try:
                    model = _member(record, model_name)
                    declared_counts = _member(model, 'counts') if model.get('state') else {}
                except (ValueError, Refusal) as exc:
                    rows[f'model:{model_name}'] = {'value': NM, 'reason': str(exc)}
                    continue
                for key in COUNTS['antenna'][producer]:
                    row = {'value': NM, 'reason': f'LL_REQUIRED_PRODUCER_MISSING: {producer}'}
                    if model.get('state'):
                        subject = chosen if view == 'gds' else _path(project, model.get('subject'))
                        if view == 'def':
                            _require(subject.resolve().is_relative_to(
                                (project / 'phase3/stage3/pnr').resolve()), 'LL_FOREIGN_ROUTED_DEF')
                        state = _path(project, model['state'])
                        row = count_row(project, state.parent, producer, key, subject,
                                        project / 'phase3/librelane/26-config', subject_view=view)
                        declared = declared_counts.get(key)
                        if type(row['value']) is int and (
                                model.get('step') != producer or
                                model.get('state_sha256') != row.get('sha256') or
                                model.get('subject_sha256') != digest(subject) or
                                type(declared) is not int or declared != row['value']):
                            row.update(value=NM, reason='LL_ANTENNA_MODEL_NOT_CURRENT_PRODUCER')
                        if view == 'def':
                            row['def_sha256'] = row.pop('gds_sha256', None)
                    rows[key] = dict(row, record=str(path))
            router = _member(record, 'router')
            subject = _path(project, router.get('subject')) if router.get('subject') else None
            for flag in ('routing_incomplete', 'route_modified_after_last_verification'):
                value = record.get(flag)
                bound = (subject is not None and subject.is_file() and
                         record.get('def_sha256') == digest(subject))
                rows[f'route:{flag}'] = {
                    'value': int(value) if type(value) is bool and bound else NM,
                    'reason': f'{flag}={value!r}; route source must be current and affirmative',
                    'record': str(path)}
        elif kind == 'drc' and tool_selected(project, '37'):
            arm = _read(project / PROMOTION)['selection']
            lane = project / f'phase3/librelane/37-{arm}-final-drc'
            path = lane / 'drc_judgment.json'
            for producer, keys in COUNTS['drc'].items():
                for key in keys:
                    folders = [p for p in lane.iterdir() if p.is_dir()
                               and (p / 'vibeic_receipt.json').is_file()
                               and _member(_read(p / 'vibeic_receipt.json'), 'input').get('step') == producer]
                    row = {'value': NM, 'reason': f'LL_REQUIRED_PRODUCER_MISSING: {producer}'}
                    if len(folders) == 1:
                        row = count_row(project, folders[0], producer, key, chosen,
                                        project / 'phase3/librelane/37-config')
                    rows[key] = dict(row, record=str(path))
            # Bind each producer before consulting its summary. A corrupt
            # summary cannot erase a current violation or credit a clean row.
            try:
                record = _read(path)
            except (OSError, ValueError) as exc:
                for row in rows.values():
                    if type(row['value']) is int and row['value'] == 0:
                        row.update(value=NM, reason=str(exc))
                raise
            for key, row in rows.items():
                try:
                    declared = _member(_member(record, 'metrics'), key)
                except (ValueError, Refusal) as exc:
                    if type(row['value']) is int and row['value'] > 0:
                        row['reason'] = str(exc)
                    else:
                        row.update(value=NM, reason=str(exc))
                    continue
                if type(row['value']) is int and (
                        type(declared.get('value')) is not int or
                        declared.get('value') != row['value'] or
                        # Step37 judge_step records availability as
                        # MEASURED; _measured_drc judges the count.
                        declared.get('status') not in ('MEASURED', 'FAIL')):
                    row['reason'] = 'LL_JUDGMENT_NOT_CURRENT_PRODUCER'
                    if row['value'] == 0:
                        row['value'] = NM
            # judge_step's aggregate source is the final KLayout State, which
            # carries Magic's metric. Credit Magic from its OWN folder above.
            final = _path(project, record.get('source'))
            if (not final.is_file() or digest(final) !=
                    _member(record, 'sha256').get('state_out.json')):
                for row in rows.values():
                    if type(row['value']) is int and row['value'] == 0:
                        row.update(value=NM, reason='LL_JUDGED_STATE_CHANGED')
        elif kind == 'density' and tool_selected(project, '37'):
            arm = _read(project / PROMOTION)['selection']
            path = project / f'phase3/librelane/37-{arm}-density.json'
            record = _read(path)
            folder = _path(project, record.get('source')).parent
            row = count_row(project, folder, 'KLayout.Density', 'klayout__density_error__count',
                            chosen, project / 'phase3/librelane/37-config')
            # The producer receipt binds this violation independently of the
            # summary. Keep its FAIL while refusing malformed summary credit.
            if type(row['value']) is int and row['value'] > 0:
                rows['klayout__density_error__count'] = dict(row, record=str(path))
            declared = _member(_member(record, 'metrics'), 'klayout__density_error__count')
            _require(_member(record, 'sha256').get('state_out.json') == row.get('sha256'),
                     row.get('reason') or 'LL_JUDGED_STATE_CHANGED')
            _require(type(declared.get('value')) is int and declared['value'] == row['value']
                     and declared.get('status') == ('FAIL' if row['value'] else 'MEASURED'),
                     'LL_JUDGMENT_NOT_CURRENT_PRODUCER')
            rows['klayout__density_error__count'] = dict(row, record=str(path))
        elif kind == 'density':
            # Step34's GDS-half record, emitted by run_density/update_record.
            # A Step31 density count is not evidence that Step34 ran.
            path = project / 'reports/phase3/fill_librelane.json'
            arms = _member(_read(path), 'gds')
            shipped = arms.get('shipped')
            arm = _member(arms, shipped)
            state = _path(project, arm.get('state'))
            row = count_row(project, state.parent, 'KLayout.Density',
                            'klayout__density_error__count', chosen,
                            project / 'phase3/librelane/34-config')
            if type(row['value']) is int and row['value'] > 0:
                rows['klayout__density_error__count'] = dict(row, record=str(path))
            _require(arm.get('state_sha256') == row.get('sha256') and
                     arm.get('subject_sha256') == digest(chosen),
                     row.get('reason') or 'LL_FILL_DENSITY_SCOPE_CHANGED')
            _require(type(arm.get('klayout__density_error__count')) is int and
                     arm['klayout__density_error__count'] == row['value'], 'LL_FILL_DENSITY_COUNT_CHANGED')
            rows['klayout__density_error__count'] = dict(row, record=str(path))
        else:
            half = kind
            path = project / pv.RECORD_REL.format(half=half)
            record = _read(path)
            scope = _member(record, 'scope')
            _require(scope.get('gds_sha256') == digest(chosen), 'LL_JUDGED_SELECTED_GDS_MISMATCH')
            if kind == 'lvs':
                _require(scope.get('magic_ext_use_gds') is True
                         and scope.get('extracted_gds_sha256') == digest(chosen),
                         'LL_LVS_EXTRACTION_SCOPE_UNBOUND')
            required = dict(COUNTS[kind])
            # Additional declared count producers are obligations too. Never
            # take a record's omission as permission to drop a built-in engine.
            if kind in ('drc', 'lvs'):
                for producer in pv.HALVES[kind]:
                    if producer in pv.PRODUCED:
                        required.setdefault(producer, pv.PRODUCED[producer])
                for producer in record.get('required_steps') or []:
                    _require(isinstance(producer, str), 'LL_DECLARED_PRODUCER_MALFORMED')
                    if producer not in pv.PRODUCED and producer not in required:
                        rows[f'producer:{producer}'] = {
                            'value': NM, 'reason': f'LL_UNSUPPORTED_DECLARED_PRODUCER: {producer}'}
                        continue
                    required.setdefault(producer, pv.PRODUCED.get(producer, ()))
            for producer, keys in required.items():
                for key in keys:
                    try:
                        declared = _member(_member(record, 'metrics'), key)
                    except (ValueError, Refusal) as exc:
                        rows[key] = {'value': NM, 'reason': str(exc), 'record': str(path)}
                        continue
                    row = {'value': NM, 'step': producer, 'key': key,
                           'reason': f'LL_REQUIRED_PRODUCER_MISSING: {producer}'}
                    if declared.get('step') == producer and declared.get('folder'):
                        row = count_row(project, _path(project, declared['folder']), producer, key,
                                        chosen, project / f'phase3/librelane/31-{half}-config')
                        if type(row['value']) is int and (
                                type(declared.get('value')) is not int or
                                declared.get('state_out_sha256') != row.get('sha256') or
                                declared.get('value') != row['value'] or
                                declared.get('status') != ('FAIL' if row['value'] else 'MEASURED')):
                            row.update(value=NM, reason='LL_JUDGMENT_NOT_CURRENT_PRODUCER')
                    rows[key] = dict(row, record=str(path))
        return {'verdict': _verdict(rows), 'rows': rows, 'gds': str(chosen),
                'gds_sha256': digest(chosen), 'reason': '; '.join(
                    f'{k}: {r.get("reason") or r["value"]}' for k, r in rows.items())}
    except (OSError, ValueError, TypeError, KeyError, Refusal) as exc:
        # An interrupted obligation is incomplete even when its first row
        # was clean. A current measured failure still dominates incompleteness.
        return {'verdict': 'FAIL' if _verdict(rows) == 'FAIL' else NM,
                'rows': rows, 'reason': str(exc)}
