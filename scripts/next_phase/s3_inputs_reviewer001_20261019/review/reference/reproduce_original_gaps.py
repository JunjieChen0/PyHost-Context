"""Reviewer 001: stdlib-only S3 audit; synthetic data and fake runners only.

No real candidate is imported or executed. The submission is read or extracted
to temporary storage. A passing check below means the reported gap reproduces,
not that the submitted implementation is correct.
"""
from __future__ import annotations
import argparse
import contextlib
import copy
import hashlib
import importlib
import io
import json
import pathlib
import sys
import tempfile
from collections import Counter
from unittest.mock import patch
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--submission', type=pathlib.Path,
                    default=pathlib.Path(__file__).parent / 'reference/submission.zip')
    ap.add_argument('--out', type=pathlib.Path)
    args = ap.parse_args()
    checks = []
    def check(name, condition, observed):
        checks.append({'id': name, 'reproduced_or_positive_control': bool(condition),
                       'observed': observed})
    with tempfile.TemporaryDirectory(prefix='reviewer001-s3-') as temporary:
        tmp = pathlib.Path(temporary)
        if args.submission.is_dir():
            source = args.submission.resolve()
        else:
            source = tmp / 'submission'
            with zipfile.ZipFile(args.submission) as z:
                for info in z.infolist():
                    p = pathlib.PurePosixPath(info.filename)
                    if p.is_absolute() or '..' in p.parts:
                        raise ValueError('unsafe submission member')
                z.extractall(source)
        sys.path.insert(0, str(source))
        for name in ('s3_adapter', 's3_adjudicate', 's3_common', 'witness_probes'):
            sys.modules.pop(name, None)
        a = importlib.import_module('s3_adapter')
        j = importlib.import_module('s3_adjudicate')
        c = importlib.import_module('s3_common')
        w = importlib.import_module('witness_probes')
        doc = json.loads((source / 'S3_ROWS_20261019.json').read_text())
        spec = json.loads((source / 'inputs/witness_spec.json').read_text())['spec']
        suite_rows = doc['suite_rows']
        def row(case='A', context='A-C1', product='M1', suite='R2_local_isolation'):
            return next(x for x in suite_rows if
                        (x['case'],x['context'],x['product_variant'],x['suite_variant']) ==
                        (case,context,product,suite))
        def result(nodes=None, rc=None, longrepr='AssertionError: cyan != magenta'):
            nodes = nodes or {}
            if rc is None:
                rc = 1 if 'failed' in nodes.values() else 0
            return {'status':'completed', 'returncode':rc, 'timed_out':False,
                    'tree_after_ok':True, 'process_group_cleanup':True,
                    'session':{'$report_type':'SessionFinished','exitstatus':rc},
                    'malformed_jsonl_lines':0, 'collected':list(nodes),
                    'node_outcomes':{n:{'phase_details':{
                        'setup':{'outcome':'passed'},
                        'call':{'outcome':v,'longrepr':longrepr if v=='failed' else None},
                        'teardown':{'outcome':'passed'}},
                        'longrepr':longrepr if v=='failed' else None} for n,v in nodes.items()}}
        r = row()
        n = r['expected']['target_nodes'][0]
        base = result({n:'passed'})
        broken = result({n:'failed'}, longrepr='RuntimeError: unrelated injected exception')
        actual = j.suite_cell(r, broken, base)
        check('J1_UNRELATED_EXCEPTION_COUNTED_AS_SIGNATURE',
              actual['detection'] and actual['failure_signature_matches'],
              {k:actual[k] for k in ('detection','failure_signature_matches','target_assertion_executed')})
        missing = result({n:'failed'})
        missing.update(returncode=-9, session=None, malformed_jsonl_lines=1)
        check('J2_INCOMPLETE_SESSION_ACCEPTED',j.row_is_usable(missing),
              {'row_is_usable':j.row_is_usable(missing),'rc':-9,'session':None,'malformed':1})
        skipped = j.suite_cell(r, result({n:'skipped'}), base)
        check('J3_SKIPPED_CALL_SAYS_ASSERTION_EXECUTED',
              skipped['target_assertion_executed']=='executed',
              {k:skipped[k] for k in ('target_behaviour_executed','target_assertion_executed')})
        no_baseline = j.suite_cell(r, result({n:'failed'}), None)
        check('J4_MISSING_HEALTH_REFERENCE_STILL_DETECTS',no_baseline['detection'],
              {k:no_baseline[k] for k in ('detection','s0_passes_in_context')})
        s0row = row(suite='S0_original')
        invalid_s0 = j.suite_cell(s0row, None, base)
        decision = j.classify(j.suite_cell(r,result({n:'passed'}),base),invalid_s0,
                              {'detection':True,'state':'detected'})
        check('J5_MISSING_S0_MISLABELLED_BLIND_SPOT',
              decision['verdict']=='PRE_EXISTING_BLIND_SPOT',decision['verdict'])
        s0 = j.suite_cell(s0row,result({n:'failed'}),base)
        inactive = j.classify(j.suite_cell(r,result({n:'failed'}),base),s0,
                             {'detection':None,'state':'unknown'})
        check('J6_UNKNOWN_ACTIVATION_STILL_RETAINED',inactive['verdict']=='RETAINED',inactive['verdict'])

        # Full 192-row synthetic path: original-product control fails, mutant
        # target also fails. No real pytest call occurs.
        results = {}
        for x in suite_rows:
            nodes = {t:'passed' for t in x['expected']['target_nodes']}
            if x['product_variant']=='M1':
                nodes = {t:'failed' for t in x['expected']['target_nodes']}
            if x['product_variant']=='original' and x['suite_variant']=='R2_local_isolation':
                nodes['tests/synthetic.py::test_non_target'] = 'failed'
            results[x['row_key']] = result(nodes)
        output = j.adjudicate(doc,results,spec)
        controls=[x for x in output['controls_original_product'] if
                  (x['case'],x['context'],x['suite_variant'])==('A','A-C1','R2_local_isolation')]
        cells=[x for x in output['cells'] if x['row_key']==r['row_key']]
        check('J7_REPAIR_CONTROL_FAILURE_IGNORED',
              controls[0]['verdict']=='CONTROL_FAILURE' and cells[0]['verdict']=='RETAINED',
              {'control':controls[0]['verdict'],'cell':cells[0]['verdict']})
        check('J8_REAL_ROW_EQUAL_PASS_PATH_EMPTY',output['equal_pass']==[],
              {'equal_pass':output['equal_pass'],
               'applicability_counts':dict(Counter(x['applicability'] for x in output['cells'])),
               'S0_present_in_comparison_cells':any(x['suite_variant']=='S0_original' for x in output['cells'])})
        parameterized=row(case='C',context='C-C1',product='M1',suite='R3_explicit_parameterization')
        old_target=parameterized['expected']['target_nodes'][0]
        child=old_target+'[columns_unset]'
        pc=j.suite_cell(parameterized,result({child:'failed'}),result({child:'passed'}))
        pc=j.classify(pc,{'detection':True},{'state':'detected','detection':True})
        check('J9_PARAMETRIZED_TARGET_EXECUTED_BUT_MISSED',
              pc['verdict']=='CHECK_NOT_EXECUTED' and not pc['detection'],
              {'registered_target':old_target,'observed_child':child,'verdict':pc['verdict']})
        # Positive control for author comparator: only a hand-built "full" R2
        # can reach it. Actual row table never labels R2 or R3 "full".
        cp=[{'case':'A','context':'A-C1','mutation_id':'A-M1','suite_variant':s,
             'applicability':'full','detection':s=='S0_original'}
            for s in ['S0_original','R2_local_isolation']]
        cpout=j.equal_pass_comparison(cp)
        check('PC_EQUAL_PASS_HAND_CONSTRUCTED_PATH_WORKS',
              bool(cpout) and cpout[0]['identical'] is False,cpout)

        for case,context,variant in [('A','A-C1','M1'),('B','B-C1','M1'),('C','C-C1','M1')]:
            verdict=j.witness_verdict(case,context,variant,
                                      {'case':case,'errors':{},'observations':{}},spec)
            check('W1_EMPTY_PAYLOAD_DETECTS_'+case,verdict['detection'] is True,verdict['state'])
        # Canonicalized SGR semantics are identical: bold + cyan is still cyan.
        v=j.witness_verdict('A','A-C1','M1',{'case':'A','errors':{},
            'observations':{'colour':{'colour_present':True,'argparse_args_sgr':'1;36'}}},spec)
        check('W2_BOLD_CYAN_FALSE_MUTATION_DETECTION',v['detection'] is True,v['state'])
        with patch.dict(sys.modules,{'synthetic_product':type('M',(),{'__file__':str(tmp/'product-elsewhere/mod.py')})()}):
            provenance=w.import_provenance(tmp/'product','synthetic_product')
        check('W3_SIBLING_PREFIX_IMPORT_ACCEPTED',bool(provenance),provenance)

        # Fake runner: these are byte extraction + synthetic outcome paths only.
        def fake_suite(argv,cwd,env,timeout):
            return {'returncode':2,'timed_out':False,'duration_seconds':0.01,
                    'stdout':'','stderr':'synthetic collection error','process_group_cleanup':True}
        run_dir=tmp/'bad-session'
        run_dir.mkdir()
        executed=a.execute_suite(s0row,run_dir,pathlib.Path('/synthetic/python'),fake_suite)
        check('E1_NO_JSONL_RC2_STILL_COMPLETED',
              executed['status']=='completed' and j.row_is_usable(executed),
              {'status':executed['status'],'rc':executed['returncode'],'session':executed['session'],
               'row_is_usable':j.row_is_usable(executed)})
        wr=doc['witness_rows'][0]
        def fake_witness(argv,cwd,env,timeout):
            pathlib.Path(argv[argv.index('--json-out')+1]).write_text(json.dumps(
                {'case':'WRONG','product_root':'/wrong','errors':{},'observations':{}}))
            return {'returncode':1,'timed_out':False,'duration_seconds':0.01,
                    'stdout':'','stderr':'synthetic error','process_group_cleanup':True}
        run_dir=tmp/'bad-witness';run_dir.mkdir()
        executed=a.execute_witness(wr,run_dir,pathlib.Path('/synthetic/python'),fake_witness)
        check('E2_FAILED_WRONG_WITNESS_STILL_COMPLETED',executed['status']=='completed',
              {'status':executed['status'],'rc':executed['returncode'],
               'actual_case':executed['witness']['case'],'requested_case':wr['case']})
        e=a.build_environment(s0row,tmp/'env')
        check('E3_INSTRUMENT_ENVIRONMENT_NOT_REUSED',
              e.get('LC_ALL')=='C.UTF-8' and 'TERM' not in e and
              'PYHOST_NETLOG' not in e and 'PYTEST_DISABLE_PLUGIN_AUTOLOAD' not in e,
              {'LC_ALL':e.get('LC_ALL'),'TERM':e.get('TERM'),
               'PYHOST_NETLOG_present':'PYHOST_NETLOG' in e,
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD_present':'PYTEST_DISABLE_PLUGIN_AUTOLOAD' in e})

        # No signed clearance is created in the submission or real workspace.
        # All positive-shaped documents below are explicitly synthetic and live
        # in the temporary directory; dispatch is mocked throughout.
        clear=tmp/'synthetic-clearance.json'
        clear_doc={'batch_id':doc['batch_id'],'authorized':True,'execution_authorized':True,
                   'signed_by':'synthetic-reviewer-control-not-a-signature',
                   'commitment_sha256':a.sha(a.COMMITMENT_PATH),
                   'rows_sha256':doc['rows_sha256'],'counts':doc['counts']}
        clear.write_text(json.dumps(clear_doc))
        check('PC_UNCHANGED_CLEARANCE_SHAPE_ACCEPTED',not a.clearance_problems(clear),
              a.clearance_problems(clear))
        tampered=copy.deepcopy(doc)
        tampered['suite_rows'][0]['context_environment']={'state':'set','value':'unexpected'}
        altered=tmp/'altered_rows.json';altered.write_text(json.dumps(tampered))
        with patch.object(a,'ROWS_PATH',altered):
            problems=a.clearance_problems(clear)
        check('G1_ACTUAL_ROWS_DRIFT_NOT_CHECKED',problems==[],problems)
        fake_rule=tmp/'changed_adjudicator.py';fake_rule.write_text('# synthetically changed rule\n')
        with patch.object(a,'HERE',tmp):
            problems=a.clearance_problems(clear)
        check('G2_PACKAGE_RULE_BINDINGS_NOT_CHECKED',problems==[],problems)
        with (patch.object(a,'prepare',return_value={'synthetic':'no-install'}) as prep,
             patch.object(sys,'argv',['s3_adapter.py','--prepare','--state-root',str(tmp/'prep')]),
             contextlib.redirect_stdout(io.StringIO())):
            code=a.main()
        check('G3_PREPARE_UNGATED',code==0 and prep.call_count==1,
              {'returncode':code,'prepare_dispatched':prep.call_count})

        tools=json.loads((source/'inputs/toolchains.json').read_text())['toolchains']
        state=tmp/'state'
        for project in tools:
            p=state/'venvs'/project/'venv/bin/python';p.parent.mkdir(parents=True,exist_ok=True);p.touch()
        rid=a.run_id_of(s0row)
        ledger=state/'ledger.json'
        def invoke(fake):
            with (patch.object(a,'load_rows',return_value=(doc,[s0row])),
                 patch.object(a,'dispatch',side_effect=fake) as dispatch,
                 patch.object(sys,'argv',['s3_adapter.py','--run','--state-root',str(state),
                                         '--clearance',str(clear)]),
                 contextlib.redirect_stdout(io.StringIO())):
                code=a.main()
            return code,dispatch.call_count
        a.write_json(ledger,{'batch_id':'FOREIGN_BATCH','commitment_sha256':'FOREIGN_COMMITMENT',
                            'rows_sha256':doc['rows_sha256'],
                            'records':{rid:{'status':'completed','row_key':'FOREIGN_ROW'}}})
        code,calls=invoke(lambda *args:result())
        check('G4_FOREIGN_LEDGER_ACCEPTED_AND_SKIPPED',code==0 and calls==0,
              {'returncode':code,'dispatch_calls':calls})
        ledger.unlink()
        def fail(*args):raise RuntimeError('synthetic instrument failure')
        code,calls=invoke(fail)
        saved=json.loads(ledger.read_text())
        check('G5_TECHNICAL_FAILURE_RETURNS_SUCCESS',code==0 and calls==1,
              {'returncode':code,'status':saved['records'][rid]['status']})
        saved['records'][rid]={'status':'timeout','timed_out':True,'row_key':s0row['row_key'],
                               'sentinel':'old_timeout_attempt'}
        a.write_json(ledger,saved)
        code,calls=invoke(lambda *args:{'status':'completed','row_key':s0row['row_key']})
        saved=json.loads(ledger.read_text())
        check('G6_TIMEOUT_REEXECUTED_AND_OVERWRITTEN',
              calls==1 and 'sentinel' not in saved['records'][rid],
              {'dispatch_calls':calls,'records':len(saved['records']),
               'prior_timeout_preserved':'sentinel' in saved['records'][rid]})
        code,calls=invoke(lambda *args:result())
        check('PC_SAME_BATCH_COMPLETED_SKIP',code==0 and calls==0,
              {'returncode':code,'dispatch_calls':calls})
        check('PC_TIMEOUT_EXPLICITLY_REJECTED_BY_USABILITY',
              not j.row_is_usable({'status':'completed','timed_out':True}),True)

    report={'schema_version':'pyhost-context/reviewer001-s3-counterexamples/1.0',
            'checks':checks,'checks_run':len(checks),
            'all_reproduced_or_positive_controls':all(x['reproduced_or_positive_control'] for x in checks),
            'real_candidate_imports':0,'real_candidate_or_witness_calls':0,
            'install_calls':0,'network_calls':0,
            'note':'Positive checks confirm defects or named positive controls, not S3 execution readiness.'}
    report=json.loads(json.dumps(report).replace(str(tmp),'${SYNTHETIC_ROOT}'))
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['all_reproduced_or_positive_controls'] else 1


if __name__=='__main__':
    raise SystemExit(main())
