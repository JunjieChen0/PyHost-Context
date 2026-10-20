"""Reviewer 001: bounded S2 guards. Standard library only; no test imports at module load."""
import fcntl
import importlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

PREFIX = 'pyhost-context/extension/next_phase/s2_20261019'
ANCHOR_NAME = 'S2_REVIEW_ANCHORS_20261020.json'
SEED = 'pyhost-context-S2-v1-20261019'
UNIT_IDS = ['typer/K07_COLUMNS', 'typer/K05_NO_COLOR', 'typer/K06_TERM',
            'twine/K06_TERM', 'rich-toolkit/K07_COLUMNS']
IDENTITY_FIELDS = ('run_id', 'order', 'project', 'position', 'block', 'phase',
                   'condition', 'target_condition', 'unit_id')


def canonical_entries(a, units):
    entries = []
    ordered = sorted(units['units'], key=lambda u: a.sha_text(f"{SEED}|unit|{u['unit_id']}"))
    for block in (1, 2):
        for u in ordered if block == 1 else reversed(ordered):
            for phase in ('A1', 'B', 'A2'):
                label = f"s2|block={block}|{u['unit_id']}|{phase}"
                entries.append(dict(block=block, phase=phase, arm='recovery',
                    project=u['project'], position=u['position'], unit_id=u['unit_id'],
                    condition=u['condition'] if phase == 'B' else 'B0',
                    target_condition=u['condition'], label=label,
                    label_sha256=a.sha_text(f'{SEED}|{label}'),
                    run_id=f"s2-b{block}-p{u['position']:02d}-{u['condition']}-{phase}",
                    order=len(entries)+1))
    return entries


def validate_consumed_schedule(a, inputs):
    problems = []
    u, s = inputs['units'], inputs['schedule']
    if [x['unit_id'] for x in u['units']] != UNIT_IDS:
        problems.append('registered_unit_scope')
    if s.get('seed') != SEED or s.get('entries') != canonical_entries(a, u):
        problems.append('canonical_schedule_consumed_fields')
    if 'anchors' in inputs:
        anchors = inputs['anchors']
        for unit in u['units']:
            signed = anchors.get('units', {}).get(unit['unit_id'], {})
            if (signed.get('flip_nodes_sha256') != unit['registered']['flip_nodes_sha256']
                    or signed.get('project') != unit['project']
                    or signed.get('position') != unit['position']
                    or signed.get('profile') != unit['profile']
                    or signed.get('source_tree_hash') != unit['registered'].get('source_tree_hash')):
                problems.append('signed_E2_scope:' + unit['unit_id'])
    return problems


def anchors(a):
    return a.read(a.HERE / ANCHOR_NAME)


def verify_commitment(a, commitment):
    problems = []
    files = commitment.get('files') or {}
    if a.aggregate(files) != commitment.get('pre_run_commitment_hash'):
        problems.append('commitment_aggregate')
    if commitment.get('batch_id') != a.BATCH or commitment.get('total_runs') != 30:
        problems.append('commitment_scope')
    for rel, digest in files.items():
        pp = PurePosixPath(rel)
        if pp.is_absolute() or '..' in pp.parts or '\\' in rel:
            problems.append('unsafe_binding:' + rel)
            continue
        path = a.WS / rel
        if not path.is_file() or a.sha(path) != digest:
            problems.append('binding_drift:' + rel)
    try:
        anchor = anchors(a)
        required = dict(anchor['inherited_runtime_bindings'])
        for name, path in a.INPUTS.items():
            if name != 'commitment':
                required[f'{PREFIX}/{path.name}'] = a.sha(path)
        for path in (a.HERE/'s2_adapter.py', Path(__file__).resolve()):
            required[f'{PREFIX}/{path.name}'] = a.sha(path)
        for rel, digest in required.items():
            if files.get(rel) != digest:
                problems.append('consumed_binding_missing_or_drift:' + rel)
        if Path(__file__).resolve() != (a.WS/PREFIX/'s2_review_guards.py').resolve():
            problems.append('executed_guard_outside_bound_path')
        if a.HERE != (a.WS/PREFIX).resolve():
            problems.append('executed_adapter_outside_bound_path')
    except (KeyError, OSError, ValueError) as exc:
        problems.append('anchor_unavailable:' + str(exc))
    return problems


def preflight(a, inputs, clearance, state_root):
    problems = a.validate_inputs(inputs) + verify_commitment(a, inputs['commitment'])
    if inputs != a.load_inputs():
        problems.append('consumed_inputs_differ_from_bound_disk')
    if problems:
        raise a.Refused('input_invalid:' + ','.join(problems))
    actual = {rel: a.sha(a.WS/rel) for rel in inputs['commitment']['files']}
    approved = a.gate(clearance, inputs['commitment'], actual)
    if Path(state_root).resolve() != Path(approved['state_root']).resolve():
        raise a.Refused('state_root_outside_signed_batch')
    return approved


def execution_problems(detail, source_hash=None):
    problems = []
    if detail.get('timed_out') is not False:
        problems.append('timeout_or_unknown')
    if type(detail.get('returncode')) is not int or detail['returncode'] not in (0, 1):
        problems.append('abnormal_returncode')
    if detail.get('session_exitstatus') != detail.get('returncode') or detail.get('session_exitstatus') not in (0, 1):
        problems.append('session_exit_mismatch_or_missing')
    if detail.get('malformed_jsonl_lines') != 0:
        problems.append('malformed_or_unknown')
    vector = detail.get('vector') or {}
    per = detail.get('per_test') or {}
    summaries = detail.get('summaries') or {}
    if not vector or len(vector) != detail.get('collected') or set(vector) != set(per) or set(vector) != set(summaries):
        problems.append('collection_or_identity_incomplete')
    if any(v.get('raw_complete') is not True or v.get('stage_integrity') != [] for v in per.values()):
        problems.append('native_phase_incomplete')
    state = detail.get('round_start_state') or {}
    h = state.get('source_tree_hash')
    if not isinstance(h, str) or not re.fullmatch('[a-f0-9]{64}', h) or (source_hash is not None and h != source_hash):
        problems.append('source_hash_missing_or_not_E2')
    cleanup = detail.get('process_group_cleanup') or {}
    cleared = (cleanup.get('group_alive_after_cleanup') is False if cleanup.get('performed') is True
               else cleanup.get('performed') is False and cleanup.get('group_alive_at_child_exit') is False)
    if not cleared or cleanup.get('target_pgid') != detail.get('child_pgid'):
        problems.append('process_group_cleanup_unconfirmed')
    network = detail.get('network') or {}
    pid = detail.get('child_pid')
    process = (network.get('processes') or {}).get(str(pid), {})
    if not pid or network.get('pytest_process_covered') is not True or network.get('pytest_process_pid') != str(pid) or process.get('init_seen') is not True or process.get('end_seen') is not True:
        problems.append('main_process_observer_incomplete')
    return problems


def native_observation(detail, node):
    p = (detail.get('per_test') or {}).get(node, {})
    s = (detail.get('summaries') or {}).get(node, {})
    return dict(summary=(detail.get('vector') or {}).get(node),
                failure_signature=p.get('failure_signature'), phase_states=p.get('phases'),
                phase_outcomes=s.get('phase_outcomes'), raw_complete=p.get('raw_complete'),
                stage_integrity=p.get('stage_integrity'),
                native={phase: {k: v.get(k) for k in ('outcome', 'wasxfail')}
                        for phase, v in (p.get('phase_details') or {}).items()})


def block_judgement(a, unit, runs, validity):
    reg = unit['registered']; nodes = set(reg['flip_nodes'])
    vectors = {p: runs[p].get('vector') or {} for p in ('A1', 'B', 'A2')}
    a1, b, a2 = (vectors[p] for p in ('A1', 'B', 'A2'))
    identities = {p: sorted(v) for p, v in vectors.items()}
    expected_ids = reg['identity_nodes']
    problems = {p: execution_problems(runs[p], reg.get('source_tree_hash')) for p in vectors}
    flips = sorted(n for n in set(a1)|set(b) if a1.get(n) != b.get(n))
    native_diff = sorted(n for n in set(a1)|set(b)
                         if native_observation(runs['A1'], n) != native_observation(runs['B'], n))
    ref_drift = sorted(n for n in set(a1)|set(a2)
                       if native_observation(runs['A1'], n) != native_observation(runs['A2'], n))
    signature_drift = [n for n in sorted(nodes) if (runs['B'].get('per_test') or {}).get(n, {}).get('failure_signature') != reg['failure_signatures'].get(n)]
    stage_drift = [n for n in sorted(nodes) if (runs['B'].get('summaries') or {}).get(n, {}).get('phase_outcomes') != reg['phase_outcomes'].get(n)]
    c = dict(C1_validity=all(validity.get(p) is True and not problems[p] for p in vectors),
        C0_baseline_reproduction=a.sha_text(a.canon(a1)) == reg['baseline_vector_sha256'],
        C2_identity=all(ids == expected_ids for ids in identities.values()),
        C3_baseline_stability=not (set(ref_drift)-nodes),
        C4_flip_set=flips == sorted(nodes), C5_signature=not signature_drift,
        C6_stage=not stage_drift, C7_restoration=a1 == a2 and not ref_drift)
    tests = [('C1_validity','NOT_EVALUABLE_VALIDITY'),
             ('C2_identity','NOT_EVALUABLE_IDENTITY_DRIFT'),
             ('C0_baseline_reproduction','NOT_EVALUABLE_BASELINE_DRIFT'),
             ('C3_baseline_stability','NOT_EVALUABLE_BASELINE_INSTABILITY')]
    status = next((label for key,label in tests if not c[key]), None)
    if status is None:
        status = ('NOT_RECOVERED_NO_EFFECT' if not flips else
                  'NOT_RECOVERED_FLIP_SET_DRIFT' if not c['C4_flip_set'] else
                  'NOT_RECOVERED_SIGNATURE_DRIFT' if not c['C5_signature'] else
                  'NOT_RECOVERED_STAGE_DRIFT' if not c['C6_stage'] else
                  'NOT_RECOVERED_A2_RESIDUAL' if not c['C7_restoration'] else 'BLOCK_CONFIRMED')
    return dict(unit_id=unit['unit_id'], block=None, criteria=c, status=status,
        details=dict(flip_nodes=flips, flip_node_count=len(flips),
            registered_flip_node_count=len(nodes), flip_missing=sorted(nodes-set(flips)),
            flip_extra=sorted(set(flips)-nodes), signature_drift_nodes=signature_drift,
            stage_drift_nodes=stage_drift, reference_native_drift_nodes=ref_drift,
            full_native_difference_nodes=native_diff,
            native_differences_outside_registered_summary_flip_set=sorted(set(native_diff)-nodes),
            a1_a2_differing_nodes=sorted(n for n in set(a1)|set(a2) if a1.get(n)!=a2.get(n)),
            a1_a2_nonflip_differing_nodes=sorted(set(ref_drift)-nodes),
            execution_problems=problems, identity_counts={p:len(v) for p,v in identities.items()},
            a1_summary_counts=a.summary_counts(a1),a2_summary_counts=a.summary_counts(a2)))


def unit_judgement(unit, blocks, gates=None):
    gates = gates or {}
    statuses = [blocks[b]['status'] for b in (1, 2)]
    confirmed = statuses.count('BLOCK_CONFIRMED')
    negative = sum(s.startswith('NOT_RECOVERED') for s in statuses)
    unknown = sum(s.startswith('NOT_EVALUABLE') for s in statuses)
    if confirmed == 2:
        status = 'RECOVERED_TWO_BLOCKS'
        level = 'E4' if all(gates.get(k) is True for k in ('schedule_gate','scope_identity','reading_chain')) else 'E3'
    elif confirmed == 1 and unknown == 1:
        status, level = 'RECOVERED_ONE_BLOCK', 'E3'
    elif confirmed == 1 and negative == 1:
        status, level = 'RECOVERY_INCONSISTENT', 'E2'
    elif negative == 2:
        status, level = 'NOT_RECOVERED', 'E1'
    elif negative == 1 and unknown == 1:
        status, level = 'RECOVERY_PARTIALLY_EVALUATED', 'E2'
    else:
        status, level = 'NOT_EVALUABLE', 'E2'
    return dict(unit_id=unit['unit_id'],block_statuses={'1':statuses[0],'2':statuses[1]},
                unit_status=status, registered_level='E2', proposed_level=level,
                E4_gates=gates, registration_requires_reviewer_verification=True,
                level_note='Historical E2 is preserved. Proposed recovery level is provisional, not registration.')


def adjudicate(a, units, records, gate_values=None):
    gate_values = gate_values or {}
    out = dict(units=[], batch=a.BATCH, classification_is_provisional=True)
    for unit in units['units']:
        blocks = {}
        for block in (1,2):
            runs, validity = {}, {}
            for phase in ('A1','B','A2'):
                rid=f"s2-b{block}-p{unit['position']:02d}-{unit['condition']}-{phase}"
                if rid not in records:
                    raise a.TechnicalFailure('missing_run:'+rid)
                runs[phase],validity[phase]=records[rid]['detail'],records[rid]['valid']
            judged=block_judgement(a,unit,runs,validity);judged['block']=block;blocks[block]=judged
        out['units'].append(dict(unit_id=unit['unit_id'],blocks=[blocks[1],blocks[2]],
            summary=unit_judgement(unit,blocks,gate_values.get(unit['unit_id']))))
    return out


def contained(root, relative):
    p=PurePosixPath(relative)
    if p.is_absolute() or '..' in p.parts or '\\' in relative:
        raise ValueError('unsafe relative evidence path')
    path=(Path(root)/relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('evidence outside state root')
    return path


def check_ledger(a, ledger, inputs, state_root, verify_results=True):
    problems=[];schedule=inputs['schedule'];commit=inputs['commitment']
    expected={e['run_id']:e for e in schedule['entries']}
    for key,value in (('batch_id',a.BATCH),('commitment',commit['pre_run_commitment_hash']),
                      ('schedule_sha256',schedule['schedule_sha256']),('plan_run_ids',list(expected))):
        if ledger.get(key)!=value:problems.append('ledger_'+key)
    rows=ledger.get('records',[]);attempts=ledger.get('attempts',[])
    ids=[r.get('run_id') for r in rows]
    if ids!=list(expected)[:len(rows)]:problems.append('ledger_records_not_unique_prefix')
    if len(rows)>30 or len(attempts)>30:problems.append('ledger_over_budget')
    aids=[r.get('run_id') for r in attempts]
    if aids!=list(expected)[:len(attempts)] or len(attempts)<len(rows):problems.append('ledger_attempts_not_unique_prefix')
    for index,attempt in enumerate(attempts):
        entry=expected.get(attempt.get('run_id'),{})
        if any(attempt.get(k)!=entry.get(k) for k in IDENTITY_FIELDS):problems.append('attempt_identity')
        if attempt.get('attempt_index')!=1:problems.append('attempt_repeated')
        want='recorded' if index<len(rows) else None
        if attempt.get('status')!=want:problems.append('unresolved_attempt_no_automatic_replay')
    for row in rows:
        rid=row.get('run_id');entry=expected.get(rid)
        if entry is None:problems.append('ledger_unknown_run');continue
        if any(row.get(k)!=entry[k] for k in IDENTITY_FIELDS):problems.append('record_identity:'+rid)
        if row.get('batch_id')!=a.BATCH or row.get('commitment')!=commit['pre_run_commitment_hash'] or row.get('schedule_sha256')!=schedule['schedule_sha256']:
            problems.append('record_batch:'+rid)
        if not verify_results:continue
        for kind in ('result','assessment'):
            ref=row.get(kind) or {}
            try:
                path=contained(state_root,ref.get('path',''))
                expected_rel=f'results/{rid}/attempt-001'+('.json' if kind=='result' else '.assessment.json')
                if ref.get('path')!=expected_rel or not path.is_file() or path.stat().st_size!=ref.get('bytes') or a.sha(path)!=ref.get('sha256'):
                    raise ValueError('missing or drifted '+kind)
                payload=a.read(path)
                if kind=='result':
                    for field,want in [('run_id',rid),('project',entry['project']),('condition',entry['condition']),('position',entry['position']),('repeat',entry['block'])]:
                        if payload.get(field)!=want:raise ValueError('payload identity')
                    for field in ('returncode','timed_out','collected','duration_seconds','jsonl_sha256'):
                        if row.get(field)!=payload.get(field):raise ValueError('ledger result scalar mismatch:'+field)
                else:
                    for evidence in payload.get('evidence_files',[]):
                        ep=contained(state_root,evidence['path'])
                        if not ep.is_file() or ep.stat().st_size!=evidence['bytes'] or a.sha(ep)!=evidence['sha256']:
                            raise ValueError('raw evidence drift')
            except (KeyError,OSError,ValueError) as exc:problems.append(kind+':'+rid+':'+str(exc))
    return problems


def snapshot(a, ledger, inputs, status):
    ids={r['run_id'] for r in ledger['records']}
    ledger.update(status=status,planned=30,recorded=len(ledger['records']),
                  not_run_ids=[e['run_id'] for e in inputs['schedule']['entries'] if e['run_id'] not in ids])
    ledger['not_run_count']=len(ledger['not_run_ids'])
    ledger['ledger_consistency']=dict(ok=not check_ledger(a,ledger,inputs,Path('.'),False),
        scope='plan and identity; byte references checked on resume and analysis')
    return ledger


def import_runtime(a):
    for p in (a.S1B,a.PROJECT/'extension/tooling',a.PROJECT/'matrix/tooling',
              a.PROJECT/'tooling',a.PROJECT/'tooling/admission_runner',a.PROJECT/'matrix/tooling/matrix_runner'):
        sys.path.insert(0,str(p))
    modules=[]
    for name,relative in [('s1b_adapter','extension/next_phase/s1b_20261019/s1b_adapter.py'),
                          ('run_matrix','matrix/tooling/run_matrix.py'),
                          ('run_extension','extension/tooling/run_extension.py')]:
        module=importlib.import_module(name)
        if Path(module.__file__).resolve()!=(a.PROJECT/relative).resolve():
            raise a.Refused('runtime_import_origin:'+name)
        modules.append(module)
    base,rm,rx=modules
    expected=anchors(a)['frozen_interpreter_sha256']
    if not Path(rm.BASE_PY).is_file() or a.sha(rm.BASE_PY)!=expected:
        raise a.Refused('frozen_interpreter_binary_drift')
    return tuple(modules)


def assess_raw(a, base, tool, entry, context, b0, condition, result, state_root):
    assessed=base.run_assessment(tool,entry,context,b0,condition,result)
    errors=execution_problems(result, anchors(a)['units'][entry['unit_id']]['source_tree_hash'])
    evidence=[]; raw={}; logs=Path(context['logs'])
    suffixes={'jsonl':'.jsonl','meta':'.meta','netlog':'.netlog',
              'stdout':'.stdout.txt','stderr':'.stderr.txt',
              'boot_stdout':'_boot.stdout.txt','boot_stderr':'_boot.stderr.txt'}
    for kind,suffix in suffixes.items():
        path=logs/(entry['run_id']+suffix)
        if not path.is_file():errors.append('missing_raw:'+kind);continue
        if not path.resolve().is_relative_to(Path(state_root).resolve()):
            raise a.TechnicalFailure('raw_evidence_outside_batch')
        raw[kind]=path.read_text(errors='replace')
        evidence.append(dict(path=path.relative_to(state_root).as_posix(),bytes=path.stat().st_size,sha256=a.sha(path)))
        if kind in ('jsonl','meta','netlog'):
            ref=(result.get('archived_slots') or {}).get(kind,{})
            if (Path(ref.get('path','')).resolve()!=path.resolve() or ref.get('sha256')!=a.sha(path)
                    or ref.get('bytes')!=path.stat().st_size):
                raise a.TechnicalFailure('archive_reference_drift:'+kind)
    if 'jsonl' in raw:
        import pytest_jsonl as parser
        from matrix_runner import summary
        records,malformed=parser.parse_jsonl(raw['jsonl']);info=parser.collection_info(records)
        sessions=[r for r in records if r.get('$report_type')=='SessionFinished']
        parsed=parser.build_per_test(records,info['collected'],
            run_dir_prefixes=(str(context['run_root']),str(context['source_path'])))
        summaries={n:summary.summary_of(p) for n,p in parsed.items()}
        if (malformed or info['errors'] or len(info['collected'])!=len(set(info['collected']))
                or len(sessions)!=1 or sessions[0].get('exitstatus')!=result.get('returncode')):
            errors.append('raw_session_or_collection_incomplete')
        if (parsed!=result.get('per_test') or summaries!=result.get('summaries')
                or {n:s['summary'] for n,s in summaries.items()}!=result.get('vector')):
            errors.append('raw_result_parse_mismatch')
    if 'netlog' in raw:
        try:
            net=[json.loads(line) for line in raw['netlog'].splitlines() if line.strip()]
            pid=result.get('child_pid')
            if any(sum(r.get('pid')==pid and r.get('kind')==kind for r in net)!=1
                   for kind in ('observer_init','observer_end')):
                errors.append('raw_main_observer_incomplete')
        except ValueError:errors.append('malformed_netlog')
    return dict(assessed,valid=assessed.get('valid') is True and not errors,
                S2_execution_problems=errors,evidence_files=evidence,
                scope='startup transition plus execution and existing raw evidence; rc=1 retained')


def run_schedule(a, inputs, state_root, clearance, runner=None, max_runs=None):
    if max_runs is not None and (type(max_runs) is not int or not 1<=max_runs<=30):
        raise a.Refused('max_runs_outside_plan')
    approved=preflight(a,inputs,clearance,state_root)
    state_root=Path(state_root).resolve()
    # Read and validate before creating files, importing the instrument, or preparing environments.
    ledger_path=state_root/'ledger.json'
    ledger=a.read(ledger_path) if ledger_path.is_file() else dict(batch_id=a.BATCH,
        commitment=inputs['commitment']['pre_run_commitment_hash'],
        schedule_sha256=inputs['schedule']['schedule_sha256'],
        plan_run_ids=[e['run_id'] for e in inputs['schedule']['entries']],records=[],attempts=[])
    problems=check_ledger(a,ledger,inputs,state_root)
    if problems:raise a.Refused('ledger_invalid:'+','.join(problems))
    if len(ledger['records'])==30:
        return dict(ok=True,recorded=30,executed=0,status='completed',ledger=str(ledger_path))
    state_root.mkdir(parents=True,exist_ok=True)
    with (state_root/'execution.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise a.Refused('S2_already_running')
        if ledger_path.is_file():
            latest=a.read(ledger_path)
            if latest!=ledger:raise a.Refused('ledger_changed_before_lock')
        a.write(ledger_path,snapshot(a,ledger,inputs,'preparing'))
        base,rm,rx=import_runtime(a)
        tools=inputs['toolchains'];schedule=inputs['schedule'];executed=0;current=None
        try:
            protocol,b0=rx.source_protocol([f"{p}-{t['version']}" for p,t in sorted(tools['projects'].items())])
            environments=base.prepare_environment({'projects':[{'project':p} for p in sorted(tools['projects'])]},tools,state_root)
            with base.explicit_sources(tools['projects']):
                for entry in schedule['entries'][len(ledger['records']):]:
                    if max_runs is not None and executed>=max_runs:break
                    tool=tools['projects'][entry['project']]
                    current=dict(entry,attempt_index=1,status='started')
                    ledger['attempts'].append(current)
                    a.write(ledger_path,snapshot(a,ledger,inputs,'running'))
                    context=rm.run_context(state_root/'rounds'/entry['project'],entry['project'],entry['project'])
                    condition=base.condition_for(protocol,entry['condition'])
                    result=(runner or rm.run_one)(protocol=protocol,b0=b0,context=context,condition=condition,
                        run_id=entry['run_id'],phase='s2',repeat=entry['block'],position=entry['position'],
                        commitment=inputs['commitment'],venvpy=environments[entry['project']]['venv']['venv_python'],
                        plugin_loads=tuple(tool['plugin_loads']),entry_argv=('-m','pytest',*tool['extra_pytest_args']),
                        extra_meta=dict(s2_block=entry['block'],s2_phase=entry['phase'],s2_order=entry['order'],
                            s2_unit_id=entry['unit_id'],profile=tool['profile'],s2_commitment=inputs['commitment']['pre_run_commitment_hash'],
                            clearance_sha256=approved['clearance_sha256']))
                    target=state_root/'results'/entry['run_id']/'attempt-001.json'
                    if target.exists():raise a.TechnicalFailure('result_would_overwrite')
                    a.write(target,result);current['status']='runner_returned'
                    current['raw_result']=dict(path=target.relative_to(state_root).as_posix(),sha256=a.sha(target),bytes=target.stat().st_size)
                    a.write(ledger_path,snapshot(a,ledger,inputs,'running'))
                    for key,want in [('run_id',entry['run_id']),('project',entry['project']),('condition',entry['condition']),('repeat',entry['block']),('position',entry['position'])]:
                        if result.get(key)!=want:raise a.TechnicalFailure('returned_identity:'+key)
                    assessment=assess_raw(a,base,tool,entry,context,b0,condition,result,state_root)
                    apath=target.with_suffix('.assessment.json');a.write(apath,assessment)
                    row={k:entry[k] for k in IDENTITY_FIELDS}
                    row.update(batch_id=a.BATCH,commitment=inputs['commitment']['pre_run_commitment_hash'],
                        schedule_sha256=schedule['schedule_sha256'],result=current['raw_result'],
                        assessment=dict(path=apath.relative_to(state_root).as_posix(),sha256=a.sha(apath),bytes=apath.stat().st_size),
                        returncode=result.get('returncode'),timed_out=result.get('timed_out'),
                        duration_seconds=result.get('duration_seconds'),collected=result.get('collected'),
                        jsonl_sha256=result.get('jsonl_sha256'),perturbation_valid=assessment['valid'])
                    ledger['records'].append(row);current['status']='recorded';current=None;executed+=1
                    a.write(ledger_path,snapshot(a,ledger,inputs,'running'))
        except BaseException as exc:
            if current is not None:current.update(status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'instrument_error',error_type=type(exc).__name__,error=str(exc))
            ledger['abort']=dict(type=type(exc).__name__,message=str(exc))
            a.write(ledger_path,snapshot(a,ledger,inputs,'aborted'));raise
        problems=check_ledger(a,ledger,inputs,state_root)
        if problems:raise a.TechnicalFailure('ledger_consistency:'+','.join(problems))
        status='completed' if len(ledger['records'])==30 else 'partial'
        a.write(ledger_path,snapshot(a,ledger,inputs,status))
        return dict(ok=True,recorded=len(ledger['records']),executed=executed,status=status,
                    not_run_count=30-len(ledger['records']),ledger=str(ledger_path))


def load_ledger_records(a, state_root, inputs):
    state_root=Path(state_root).resolve();ledger=a.read(state_root/'ledger.json')
    problems=check_ledger(a,ledger,inputs,state_root)
    if problems:raise a.TechnicalFailure('analysis_ledger_invalid:'+','.join(problems))
    if len(ledger['records'])!=30:raise a.TechnicalFailure('analysis_plan_incomplete')
    records={}
    base,rm,rx=import_runtime(a)
    protocol,b0=rx.source_protocol([f"{p}-{t['version']}" for p,t in sorted(inputs['toolchains']['projects'].items())])
    expected={e['run_id']:e for e in inputs['schedule']['entries']}
    for row in ledger['records']:
        entry=expected[row['run_id']];detail=a.read(contained(state_root,row['result']['path']))
        context=rm.run_context(state_root/'rounds'/entry['project'],entry['project'],entry['project'])
        assessment=assess_raw(a,base,inputs['toolchains']['projects'][entry['project']],entry,
                             context,b0,base.condition_for(protocol,entry['condition']),detail,state_root)
        saved=a.read(contained(state_root,row['assessment']['path']))
        if assessment!=saved:raise a.TechnicalFailure('recomputed_assessment_drift:'+row['run_id'])
        records[row['run_id']]=dict(detail=detail,valid=assessment['valid'],row=row)
    return ledger,records
