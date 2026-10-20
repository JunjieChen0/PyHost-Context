"""Reviewer 001: S3 REV1 remaining checks, stdlib and mocked runners only.

Never imports or runs a real product. Positive checks confirm the remaining
defects or the explicitly named positive controls, not execution readiness.
"""
from __future__ import annotations
import argparse
import ast
import contextlib
import copy
import hashlib
import importlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from unittest.mock import patch
import zipfile

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--submission',type=pathlib.Path,default=pathlib.Path(__file__).parent/'reference/submission_rev1.zip')
    ap.add_argument('--out',type=pathlib.Path)
    args=ap.parse_args()
    checks=[]
    def check(name,ok,observed):
        checks.append({'id':name,'confirmed':bool(ok),'observed':observed})
    with tempfile.TemporaryDirectory(prefix='reviewer001-rev1-') as tmp_name:
        tmp=pathlib.Path(tmp_name)
        src=args.submission.resolve() if args.submission.is_dir() else tmp/'submitted'
        if not args.submission.is_dir():
            with zipfile.ZipFile(args.submission) as z:
                for info in z.infolist():
                    p=pathlib.PurePosixPath(info.filename)
                    if p.is_absolute() or '..' in p.parts:raise ValueError('unsafe zip')
                z.extractall(src)
        sys.path.insert(0,str(src))
        for name in ('s3_adapter','s3_adjudicate','s3_common'):
            sys.modules.pop(name,None)
        a=importlib.import_module('s3_adapter')
        j=importlib.import_module('s3_adjudicate')
        c=importlib.import_module('s3_common')
        doc=json.loads((src/'S3_ROWS_20261019.json').read_text())
        spec=json.loads((src/'inputs/witness_spec.json').read_text())['spec']
        rows=doc['suite_rows']+doc['witness_rows']
        def row(case='A',context='A-C1',product='M1',suite='R3_explicit_parameterization'):
            return next(r for r in doc['suite_rows'] if
                (r['case'],r['context'],r['product_variant'],r['suite_variant'])==
                (case,context,product,suite))
        locations=[]
        for case,rel,fn in [('A','tests/test_argparse.py','test_actions_spans_in_usage'),
                            ('B','tests/test_optparse.py','test_generated_usage'),
                            ('C','tests/test_main.py','test_cli_parse_error_handling')]:
            r=row(case=case,context=f'{case}-C1')
            with tarfile.open(src/r['tree']['path']) as t:
                member=next(m for m in t.getmembers() if m.name.endswith('/'+rel))
                source=t.extractfile(member).read().decode()
            func=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name==fn)
            assertions=[n for n in ast.walk(func) if isinstance(n,ast.Assert)]
            selected=next(n for n in assertions if
                ('format_help' in ast.unparse(n) if case in 'AB' else
                 'Error parsing reStructuredText' in ast.unparse(n)))
            record={'longrepr':f'{rel}:{selected.lineno}: AssertionError: registered mutation outcome'}
            sig=r['expected']['failure_signature']
            matched=j.signature_match(record,sig)
            evidence={'case':case,'function':fn,'file':rel,
                      'registered_locations':sig['assertion_locations'],
                      'actual_assertion_line':selected.lineno,'actual_assertion':ast.unparse(selected),
                      'native_location_accepted':matched}
            locations.append(evidence)
            check('R1_STALE_ASSERTION_LOCATION_'+case,not matched,evidence)
        for case,node in [
            ('A','tests/test_argparse.py::test_user_usage[no_color_absent-default]'),
            ('B','tests/test_optparse.py::test_generated_usage[columns_40-indented]')]:
            r=row(case=case,context=f'{case}-C1')
            matched=j.match_target(node,r['expected']['identity'])
            check('R1_NATIVE_PARAMETER_ORDER_'+case,matched is None,
                  {'nodeid_from_synthetic_pytest_collection':node,'matched':matched})
        r=row()
        # Positive control: a correctly formed location at the declared OLD
        # line passes. The gate exists; the binding to actual source is wrong.
        loc=r['expected']['failure_signature']['assertion_locations'][0]
        text=f"{loc['file']}:{loc['lines'][0]}: AssertionError: registered mutation outcome"
        check('PC_REGISTERED_SIGNATURE_GATE_EXISTS',
              j.signature_match({'longrepr':text},r['expected']['failure_signature']),True)

        empty=j.adjudicate(doc,{},spec)
        check('R2_ZERO_OBSERVATIONS_STILL_EQUAL_PASS',
              bool(empty['equal_pass']) and all(x['identical'] for x in empty['equal_pass']),
              {'input_result_count':0,'comparison_count':len(empty['equal_pass']),
               'all_comparisons_identical':all(x['identical'] for x in empty['equal_pass']),
               'cell_verdicts':dict(Counter(x['verdict'] for x in empty['cells']))})
        cells=[{'case':'A','context':'A-C1','mutation_id':'A-M1','suite_variant':s,
                'applicability':'full' if s=='S0_original' else 'local',
                'covers_requested_branch':True,'detection':s=='S0_original',
                'verdict':'RETAINED' if s!='S0_original' else 'S0_REFERENCE',
                'health_reference_usable':True,'s0_passes_in_context':True}
               for s in ['S0_original','R2_local_isolation']]
        bad_control=[{'case':'A','context':'A-C1','suite_variant':'R2_local_isolation',
                      'verdict':'CONTROL_FAILURE','returncode':1}]
        comparison=j.equal_pass_comparison(cells,bad_control)
        check('R2_FAILED_CONTROL_STILL_COMPARED',bool(comparison) and
              'R2_local_isolation' in comparison[0]['jointly_evaluable_suites'],comparison)
        different_mutants=copy.deepcopy(cells)
        different_mutants[1]['mutation_id']='A-M2'
        different_mutants[0]['detection']=different_mutants[1]['detection']=False
        comparison=j.equal_pass_comparison(different_mutants)
        check('R2_NO_COMMON_MUTANT_REPORTED_DIFFERENT',bool(comparison) and
              comparison[0]['identical'] is False,comparison)
        green_controls=[dict(bad_control[0],verdict='CONTROL_ORIGINAL_PRODUCT',returncode=0)]
        check('PC_EQUAL_PASS_DIFFERENT_KILLS_REACHABLE',
              j.equal_pass_comparison(cells,green_controls)[0]['identical'] is False,True)

        base_row=row(suite='S0_original')
        def fake_runner(kind,missing_netlog=False):
            def run(argv,cwd,env,timeout):
                if '-c' in argv:
                    return {'returncode':0,'stdout':json.dumps({'python':'3.11.9',
                        'implementation':'CPython','flags':{'utf8_mode':0,'ignore_environment':0,'isolated':0},
                        'env':dict(env)}),'stderr':'','timed_out':False,'duration_seconds':0.01,
                        'process_group_cleanup':True}
                rc=2 if kind=='collection_error' else 0
                meta=json.loads(pathlib.Path(env['PYHOST_ROUND_META']).read_text())
                records=[{'$report_type':'RuntimeSnapshot','environment':dict(env),'round_meta':meta}]
                if rc==2:
                    records.append({'$report_type':'CollectReport','nodeid':'tests/test_broken.py',
                                    'outcome':'failed','longrepr':'ModuleNotFoundError: synthetic_missing'})
                collected=[] if rc==2 else ['tests/synthetic.py::test_healthy']
                records.append({'$report_type':'CollectionFinished','collected':collected,
                                'collected_count':len(collected)})
                if rc==0:
                    records.extend({'$report_type':'TestReport',
                                    'nodeid':'tests/synthetic.py::test_healthy',
                                    'when':phase,'outcome':'passed'}
                                   for phase in ['setup','call','teardown'])
                records.append({'$report_type':'SessionFinished','exitstatus':rc})
                pathlib.Path(env['PYHOST_JSONL_PATH']).write_text(''.join(json.dumps(x)+'\n' for x in records))
                if not missing_netlog:
                    pathlib.Path(env['PYHOST_NETLOG']).write_text(
                        json.dumps({'event':'observer_init','pid':1001})+'\n'+
                        json.dumps({'event':'observer_end','pid':1001})+'\n')
                return {'returncode':rc,'stdout':'','stderr':'','timed_out':False,
                        'duration_seconds':0.01,'process_group_cleanup':True}
            return run
        for name,kind,no_net in [('R4_NATIVE_COLLECTION_ERROR_ACCEPTED','collection_error',False),
                                  ('R4_MISSING_NETWORK_EVIDENCE_ACCEPTED','ordinary',True)]:
            run_dir=tmp/name
            for sub in ('home','tmp','bin'):(run_dir/sub).mkdir(parents=True,exist_ok=True)
            result=a.execute_suite(base_row,run_dir,pathlib.Path('/synthetic/python'),fake_runner(kind,no_net))
            check(name,result['status']=='completed' and j.row_is_usable(result),
                  {k:result[k] for k in ['status','returncode','collection_reports','network','problems']})

        # Exercise prepare's actual inventory comparison with a mocked installer.
        # These files are synthetic bytes, not installed wheels or candidates.
        ws=tmp/'prepare_workspace';ws.mkdir()
        wheels=[]
        for filename in ['pytest-7.2.0-py3-none-any.whl','pluggy-1.0.0-py3-none-any.whl']:
            p=ws/filename;p.write_bytes(b'synthetic-wheel-no-install')
            wheels.append({'path':filename,'filename':filename,'sha256':a.sha(p),'bytes':p.stat().st_size})
        inputs=tmp/'prepare_inputs';inputs.mkdir()
        (inputs/'toolchains.json').write_text(json.dumps({'toolchains':{
            'synthetic-project':{'pytest':'7.2.0','wheels':wheels,'observer':{}}}}))
        state=tmp/'prepare_state';purelib=tmp/'purelib';purelib.mkdir()
        calls=[]
        def mock_subprocess(argv,**kwargs):
            calls.append(list(map(str,argv)))
            if 'venv' in argv:
                p=pathlib.Path(argv[-1])/'bin/python';p.parent.mkdir(parents=True,exist_ok=True);p.touch()
                return subprocess.CompletedProcess(argv,0,'','')
            code=argv[2] if len(argv)>2 and argv[1]=='-c' else ''
            out=str(purelib) if 'sysconfig' in code else (
                json.dumps({'pytest':'7.2.0','unexplained-extra':'99.0'}) if 'm.distributions()' in code else '')
            return subprocess.CompletedProcess(argv,0,out,'')
        with (patch.object(a,'WS',ws),patch.object(a,'INPUTS',inputs),
              patch.object(a,'interpreter_record',return_value={'path':'/synthetic/cpython3.11',
                  'version':'3.11.9','sha256':'synthetic-interpreter-binding'}),
              patch.object(a.subprocess,'run',side_effect=mock_subprocess)):
            prepared=a.prepare(state)
        installed=prepared['projects']['synthetic-project']['installed']
        check('R4_EXACT_PACKAGE_SET_NOT_ENFORCED',
              'unexplained-extra' in installed and 'pluggy' not in installed,
              {'accepted_installed_set':installed,'declared_wheels':[x['filename'] for x in wheels],
               'real_install_calls':0})
        with (patch.object(a,'WS',ws),patch.object(a,'INPUTS',inputs),
              patch.object(a,'interpreter_record',return_value={'path':'/synthetic/cpython3.11',
                  'version':'3.11.9','sha256':'synthetic-interpreter-binding'}),
              patch.object(a.subprocess,'run',side_effect=mock_subprocess)):
            try:a.prepare(state)
            except RuntimeError as exc:reuse=str(exc)
            else:reuse='accepted'
        check('R4_PROFILE_WRITE_READ_KEY_MISMATCH',reuse.startswith('venv_profile_mismatch:'),reuse)

        # Full 240-row positive consumer. Every process is replaced by the
        # runner below, while dispatch/extraction/ledger/identity code are real.
        state=tmp/'full240'
        tools=json.loads((src/'inputs/toolchains.json').read_text())['toolchains']
        for project in tools:
            p=state/'venvs'/project/'venv/bin/python';p.parent.mkdir(parents=True,exist_ok=True);p.touch()
        clearance=tmp/'synthetic-clearance.json'
        clearance.write_text(json.dumps({'batch_id':a.BATCH_ID,'authorized':True,
            'execution_authorized':True,'signed_by':'synthetic-only-not-a-signature',
            'commitment_sha256':a.sha(a.COMMITMENT_PATH),'rows_sha256':a.rows_hash(doc),'counts':doc['counts']}))
        fake_calls=[]
        def consume(argv,cwd,env,timeout):
            fake_calls.append(list(map(str,argv)))
            common={'returncode':0,'stdout':'','stderr':'','timed_out':False,
                    'duration_seconds':0.01,'process_group_cleanup':True}
            if '-c' in argv:
                return dict(common,stdout=json.dumps({'python':'3.11.9','implementation':'CPython',
                    'flags':{'utf8_mode':0,'ignore_environment':0,'isolated':0},'env':dict(env)}))
            if '--json-out' in argv:
                out=pathlib.Path(argv[argv.index('--json-out')+1])
                case=argv[argv.index('--case')+1]
                out.write_text(json.dumps({'case':case,'product_root':str(cwd),
                                         'errors':{},'observations':{'synthetic':True}}))
                return common
            meta=json.loads(pathlib.Path(env['PYHOST_ROUND_META']).read_text())
            records=[{'$report_type':'RuntimeSnapshot','environment':dict(env),'round_meta':meta},
                     {'$report_type':'CollectionFinished','collected':['tests/synthetic.py::test_ok'],
                      'collected_count':1}]
            records.extend({'$report_type':'TestReport','nodeid':'tests/synthetic.py::test_ok',
                            'when':phase,'outcome':'passed'} for phase in ['setup','call','teardown'])
            records.append({'$report_type':'SessionFinished','exitstatus':0})
            pathlib.Path(env['PYHOST_JSONL_PATH']).write_text(''.join(json.dumps(x)+'\n' for x in records))
            pathlib.Path(env['PYHOST_NETLOG']).write_text(
                json.dumps({'event':'observer_init','pid':1001})+'\n'+
                json.dumps({'event':'observer_end','pid':1001})+'\n')
            return common
        def invoke():
            with (patch.object(a,'_popen',side_effect=consume),
                  patch.object(sys,'argv',['s3_adapter.py','--run','--state-root',str(state),
                                          '--clearance',str(clearance)]),
                  contextlib.redirect_stdout(io.StringIO())):
                return a.main()
        first=invoke();count=len(fake_calls);second=invoke()
        ledger=json.loads((state/'ledger.json').read_text())
        summary=json.loads((state/'summary.json').read_text())
        check('PC_FULL_240_ACTUAL_DISPATCH_AND_RESUME',first==second==0 and
              len(ledger['records'])==240 and len(fake_calls)==count and summary['complete'],
              {'records':len(ledger['records']),'fake_process_calls':count,
               'resume_extra_calls':len(fake_calls)-count,'first_exit':first,'resume_exit':second})

        report={'schema_version':'pyhost-context/reviewer001-s3-rev1-remaining/1.0',
                'checks_run':len(checks),'all_confirmed':all(x['confirmed'] for x in checks),
                'checks':checks,'candidate_imports_or_calls':0,'real_witness_calls':0,
                'real_install_calls':0,'network_calls':0,
                'note':'All product trees read as bytes only. Installed sets and subprocess outcomes are synthetic.'}
        report=json.loads(json.dumps(report).replace(str(tmp),'${SYNTHETIC_ROOT}'))
    if args.out:args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['all_confirmed'] else 1

if __name__=='__main__':raise SystemExit(main())
