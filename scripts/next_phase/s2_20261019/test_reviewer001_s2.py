"""Reviewer 001: synthetic S2 guards and complete fake 30-slot dispatch. No candidates."""
import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import s2_adapter as a
import s2_review_guards as g
import test_s2_adapter as author

HERE=Path(__file__).resolve().parent


def pure_runs():
    return dict(A1=author.detail(author.BASELINE),
                B=author.detail({**author.BASELINE,'t::a':'failed','t::b':'failed'},author.SIGS,author.PHASES),
                A2=author.detail(author.BASELINE))


def judgement(runs):
    return a.block_judgement(author.unit(),runs,dict(A1=True,B=True,A2=True))


class ScientificGuards(unittest.TestCase):
    def test_timeout_is_not_confirmed(self):
        r=pure_runs();r['B']['timed_out']=True
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_VALIDITY')
    def test_rc2_is_not_confirmed(self):
        r=pure_runs();r['B'].update(returncode=2,session_exitstatus=2)
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_VALIDITY')
    def test_session_must_match_process(self):
        r=pure_runs();r['B']['session_exitstatus']=0
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_VALIDITY')
    def test_malformed_is_ineligible(self):
        r=pure_runs();r['B']['malformed_jsonl_lines']=1
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_VALIDITY')
    def test_missing_session_is_ineligible(self):
        r=pure_runs();r['B'].pop('session_exitstatus')
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_VALIDITY')
    def test_incomplete_phase_is_ineligible(self):
        r=pure_runs();r['B']['per_test']['t::a']['raw_complete']=False
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_VALIDITY')
    def test_missing_source_hash_is_ineligible(self):
        r=pure_runs();r['B']['round_start_state']['source_tree_hash']=None
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_VALIDITY')
    def test_wrong_source_hash_is_ineligible(self):
        r=pure_runs();r['B']['round_start_state']['source_tree_hash']='b'*64
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_VALIDITY')
    def test_live_group_is_ineligible(self):
        r=pure_runs();r['B']['process_group_cleanup'].update(performed=True,group_alive_after_cleanup=True)
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_VALIDITY')
    def test_missing_observer_end_is_ineligible(self):
        r=pure_runs();r['B']['network']['processes']['10']['end_seen']=False
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_VALIDITY')
    def test_A2_background_signature_is_compared(self):
        r=pure_runs();r['A2']['per_test']['t::c']['failure_signature']='different'
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_BASELINE_INSTABILITY')
    def test_A2_target_stage_is_compared(self):
        r=pure_runs();r['A2']['summaries']['t::a']['phase_outcomes']={'setup':'failed'}
        self.assertEqual(judgement(r)['status'],'NOT_RECOVERED_A2_RESIDUAL')
    def test_B_identity_is_not_negative_evidence(self):
        r=pure_runs();r['B']['vector']['other']=r['B']['vector'].pop('t::c')
        r['B']['per_test']['other']=r['B']['per_test'].pop('t::c')
        r['B']['summaries']['other']=r['B']['summaries'].pop('t::c')
        self.assertEqual(judgement(r)['status'],'NOT_EVALUABLE_IDENTITY_DRIFT')
    def test_same_label_native_difference_is_separately_preserved(self):
        r=pure_runs();r['B']['per_test']['t::c']['failure_signature']='different native observation'
        result=judgement(r)
        self.assertEqual(result['details']['flip_nodes'],['t::a','t::b'])
        self.assertEqual(result['details']['native_differences_outside_registered_summary_flip_set'],['t::c'])
    def test_mixed_unknown_does_not_create_E1(self):
        j=a.unit_judgement(author.unit(),{1:{'status':'NOT_RECOVERED_NO_EFFECT'},2:{'status':'NOT_EVALUABLE_VALIDITY'}})
        self.assertEqual(j['proposed_level'],'E2');self.assertEqual(j['registered_level'],'E2')
    def test_E4_requires_all_ancillary_gates(self):
        blocks={i:{'status':'BLOCK_CONFIRMED'} for i in (1,2)}
        self.assertEqual(a.unit_judgement(author.unit(),blocks)['proposed_level'],'E3')
        self.assertEqual(a.unit_judgement(author.unit(),blocks,dict(schedule_gate=True,scope_identity=True,reading_chain=True))['proposed_level'],'E4')
    def test_old_E2_is_not_overwritten(self):
        j=a.unit_judgement(author.unit(),{i:{'status':'NOT_RECOVERED_NO_EFFECT'} for i in (1,2)})
        self.assertEqual(j['proposed_level'],'E1');self.assertEqual(j['registered_level'],'E2')
    def test_run_id_tamper_with_rehashed_schedule_is_rejected(self):
        inputs=a.load_inputs();inputs['schedule']['entries'][0]['run_id']='other'
        inputs['schedule']['schedule_sha256']=a.sha_text(json.dumps({k:v for k,v in inputs['schedule'].items() if k!='schedule_sha256'},sort_keys=True,ensure_ascii=False))
        self.assertIn('canonical_schedule_consumed_fields',a.validate_inputs(inputs))
    def test_undeclared_seed_is_rejected(self):
        inputs=a.load_inputs();inputs['schedule']['seed']='another'
        self.assertTrue(a.validate_inputs(inputs))


def load_pure(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module);return module


class FakeBatch:
    """Authorization records below exist only in a TemporaryDirectory with fake products."""
    def __enter__(self):
        self.temp=tempfile.TemporaryDirectory(prefix='reviewer001-S2-synthetic-')
        self.ws=Path(self.temp.name);self.dir=self.ws/g.PREFIX;self.dir.mkdir(parents=True)
        for name in ['s2_adapter.py','s2_review_guards.py']:
            shutil.copyfile(a.HERE/name,self.dir/name)
        self.original_inputs=a.load_inputs()
        self.inputs=copy.deepcopy(self.original_inputs)
        self.context=types.SimpleNamespace()
        ref=HERE/'reference_parser'
        self.parser=load_pure('synthetic_native_parser',ref/'pytest_jsonl.py')
        self.summary=load_pure('synthetic_summary',ref/'summary.py')
        self.names=['fake::a','fake::b','fake::c']
        self.native_base,self.native_summaries,self.basevector,self.base_records=self.native(False)
        self.native_B,self.B_summaries,self.Bvector,self.B_records=self.native(True)
        for unit in self.inputs['units']['units']:
            reg=unit['registered'];flips=self.names[:2]
            reg.update(flip_nodes=flips,flip_node_count=2,
                flip_nodes_sha256=a.sha_text('\n'.join(flips)),identity_nodes=self.names,
                identity_nodes_count=3,identity_nodes_sha256=a.sha_text('\n'.join(self.names)),
                baseline_vector_sha256=a.sha_text(a.canon(self.basevector)),source_tree_hash='a'*64,
                failure_signatures={n:self.native_B[n]['failure_signature'] for n in flips},
                failure_signature_sha256={n:a.sha_text(self.native_B[n]['failure_signature']) for n in flips},
                phase_outcomes={n:self.B_summaries[n]['phase_outcomes'] for n in flips})
            self.inputs['anchors']['units'][unit['unit_id']].update(source_tree_hash='a'*64,flip_nodes_sha256=reg['flip_nodes_sha256'])
        for row in self.inputs['roster']['units']:row['flip_node_count']=2
        self.inputs['anchors']['inherited_runtime_bindings']={}
        self.paths={}
        for key,p in a.INPUTS.items():
            dst=self.dir/p.name;self.paths[key]=dst
            if key!='commitment':a.write(dst,self.inputs[key])
        self.inputs['commitment']['files']={}
        for p in self.dir.iterdir():
            if p.name!='S2_PRE_RUN_COMMITMENT_20261019.json':self.inputs['commitment']['files'][p.relative_to(self.ws).as_posix()]=a.sha(p)
        self.inputs['commitment']['pre_run_commitment_hash']=a.aggregate(self.inputs['commitment']['files'])
        a.write(self.paths['commitment'],self.inputs['commitment'])
        self.state=self.ws/a.STATE_ROOT_REL
        self.clearance=self.ws/'synthetic_clearance.json'
        a.write(self.clearance,dict(schema_version=a.CLEARANCE_SCHEMA,decision=a.DECISION,
            signed_by='Reviewer 001',signed_at_utc='2026-10-19T00:00:00Z',
            pre_run_commitment_hash=self.inputs['commitment']['pre_run_commitment_hash'],
            commitment_document_sha256=a.sha(self.paths['commitment']),
            bindings=self.inputs['commitment']['files'],
            authorization=dict(S2_execution=True,registered_S2_analysis=True,S3=False,repro=False,
                S1b_replay=False,new_samples=False,new_contexts=False,semantic_mutations=False),
            scope=dict(batch_id=a.BATCH,total_runs=30,blocks=2,schedule_sha256=self.inputs['schedule']['schedule_sha256'],state_root=a.STATE_ROOT_REL,unit_ids=g.UNIT_IDS)))
        self.calls=[];self.prepare_calls=0;self.mode='normal'
        base=types.SimpleNamespace(prepare_environment=self.prepare,explicit_sources=lambda tools:contextlib.nullcontext(),
            condition_for=lambda p,c:None if c=='B0' else {'id':c},
            run_assessment=lambda *args:dict(valid=True,startup_transition_valid=True))
        rm=types.SimpleNamespace(run_context=self.run_context,run_one=self.runner)
        rx=types.SimpleNamespace(source_protocol=lambda refs: ({},{}))
        self.stack=contextlib.ExitStack()
        self.stack.enter_context(patch.multiple(a,HERE=self.dir,WS=self.ws,PROJECT=self.ws/'pyhost-context',
            S1B=self.ws/'pyhost-context/extension/next_phase/s1b_20261019',INPUTS=self.paths))
        self.stack.enter_context(patch.object(g,'__file__',str(self.dir/'s2_review_guards.py')))
        self.stack.enter_context(patch.object(g,'import_runtime',return_value=(base,rm,rx)))
        package=types.ModuleType('matrix_runner');package.summary=self.summary
        self.stack.enter_context(patch.dict(sys.modules,{'pytest_jsonl':self.parser,'matrix_runner':package}))
        return self
    def __exit__(self,*args):
        self.stack.close();self.temp.cleanup()
    def native(self,perturbed):
        records=[{'$report_type':'CollectionItem','nodeid':n} for n in self.names]
        for n in self.names:
            for phase in ['setup','call','teardown']:
                failed=perturbed and n in self.names[:2] and phase=='call'
                records.append(dict({'$report_type':'TestReport'},nodeid=n,when=phase,outcome='failed' if failed else 'passed',longrepr='AssertionError: synthetic '+n if failed else None))
        records.append({'$report_type':'SessionFinished','exitstatus':1 if perturbed else 0})
        native=self.parser.build_per_test(records,self.names)
        sums={n:self.summary.summary_of(v) for n,v in native.items()}
        return native,sums,{n:s['summary'] for n,s in sums.items()},records
    def prepare(self,roster,tools,state):
        self.prepare_calls+=1
        return {p:{'venv':{'venv_python':'NOT_A_REAL_INTERPRETER'}} for p in tools['projects']}
    def run_context(self,root,project,name):
        return dict(logs=Path(root)/name/'logs',run_root=Path(root)/name,source_path=Path(root)/'src'/name)
    def runner(self,**kwargs):
        self.calls.append(kwargs['run_id'])
        if self.mode=='raise':raise RuntimeError('synthetic instrument interruption')
        perturbed=kwargs['condition'] is not None
        per,sums,vector,records=copy.deepcopy((self.native_B,self.B_summaries,self.Bvector,self.B_records) if perturbed else (self.native_base,self.native_summaries,self.basevector,self.base_records))
        detail=author.detail(vector);detail.update(per_test=per,summaries=sums,
            run_id=kwargs['run_id'],project=next(e['project'] for e in self.inputs['schedule']['entries'] if e['run_id']==kwargs['run_id']),
            condition='B0' if kwargs['condition'] is None else kwargs['condition']['id'],
            repeat=kwargs['repeat'],position=kwargs['position'],duration_seconds=0.01)
        logs=kwargs['context']['logs'];logs.mkdir(parents=True,exist_ok=True)
        bodies={'.jsonl':'\n'.join(json.dumps(r) for r in records)+'\n','.meta':'{}',
            '.netlog':'\n'.join(json.dumps(dict(kind=k,pid=10)) for k in ('observer_init','observer_end'))+'\n',
            '.stdout.txt':'synthetic','.stderr.txt':'','_boot.stdout.txt':'{}','_boot.stderr.txt':''}
        refs={}
        for suffix,body in bodies.items():
            p=logs/(kwargs['run_id']+suffix);p.write_text(body)
            if suffix in ('.jsonl','.meta','.netlog'):refs[suffix[1:]]=dict(path=str(p),bytes=p.stat().st_size,sha256=a.sha(p))
        detail['archived_slots']=refs;detail['jsonl_sha256']=refs['jsonl']['sha256']
        if self.mode=='timeout':detail.update(timed_out=True,returncode=-9,session_exitstatus=None)
        return detail
    def run(self,**kw):
        return a.run_schedule(self.inputs,state_root=self.state,clearance=self.clearance,**kw)


class DispatchGuards(unittest.TestCase):
    def test_full_30_and_resume_zero(self):
        with FakeBatch() as h:
            self.assertEqual(h.run()['executed'],30)
            self.assertEqual(h.run()['executed'],0);self.assertEqual(len(h.calls),30)
            ledger=a.read(h.state/'ledger.json');self.assertEqual(ledger['not_run_count'],0)
            self.assertTrue(ledger['ledger_consistency']['ok'])
            ledger,records=a.load_ledger_records(h.state,h.inputs)
            self.assertEqual(len(records),30)
            self.assertTrue(all(r['valid'] for r in records.values()))
    def test_partial_then_finish_no_duplicates(self):
        with FakeBatch() as h:
            self.assertEqual(h.run(max_runs=2)['recorded'],2)
            self.assertEqual(h.run()['executed'],28)
            self.assertEqual(len(set(h.calls)),30)
    def test_recorded_timeout_not_replayed(self):
        with FakeBatch() as h:
            h.mode='timeout';self.assertEqual(h.run()['recorded'],30)
            ledger,records=a.load_ledger_records(h.state,h.inputs)
            self.assertTrue(all(not r['valid'] for r in records.values()))
            self.assertEqual(h.run()['executed'],0)
    def test_instrument_abort_is_preserved(self):
        with FakeBatch() as h:
            h.mode='raise'
            with self.assertRaisesRegex(RuntimeError,'synthetic'):h.run()
            ledger=a.read(h.state/'ledger.json');self.assertEqual(ledger['not_run_count'],30)
            self.assertEqual(ledger['attempts'][0]['status'],'instrument_error')
            with self.assertRaises(a.Refused):h.run()
            self.assertEqual(len(h.calls),1)
    def test_foreign_full_ledger_rejected_before_prepare(self):
        with FakeBatch() as h:
            a.write(h.state/'ledger.json',dict(batch_id='OTHER',commitment='OTHER',schedule_sha256='OTHER',
                plan_run_ids=[e['run_id'] for e in h.inputs['schedule']['entries']],records=[{'run_id':f'foreign-{i}'} for i in range(30)],attempts=[]))
            before=(h.state/'ledger.json').read_bytes()
            with self.assertRaises(a.Refused):h.run()
            self.assertEqual(h.prepare_calls,0);self.assertEqual((h.state/'ledger.json').read_bytes(),before)
    def test_wrong_resume_batch_commit_schedule_each_rejected(self):
        for key in ('batch_id','commitment','schedule_sha256'):
            with self.subTest(key=key),FakeBatch() as h:
                h.run(max_runs=1);ledger=a.read(h.state/'ledger.json');ledger[key]='OTHER';a.write(h.state/'ledger.json',ledger)
                prepared=h.prepare_calls
                with self.assertRaises(a.Refused):h.run()
                self.assertEqual(h.prepare_calls,prepared)
    def test_duplicate_records_rejected(self):
        with FakeBatch() as h:
            h.run(max_runs=1);ledger=a.read(h.state/'ledger.json');ledger['records']*=2;a.write(h.state/'ledger.json',ledger)
            with self.assertRaises(a.Refused):h.run()
    def test_raw_drift_rejected_before_resume(self):
        with FakeBatch() as h:
            h.run(max_runs=1);p=next((h.state/'rounds').rglob('*.jsonl'));p.write_text(p.read_text()+'{}\n')
            with self.assertRaises(a.Refused):h.run()
    def test_result_scalar_tamper_rejected(self):
        with FakeBatch() as h:
            h.run(max_runs=1);ledger=a.read(h.state/'ledger.json');ledger['records'][0]['returncode']=9;a.write(h.state/'ledger.json',ledger)
            with self.assertRaises(a.Refused):h.run()
    def test_missing_clearance_does_not_create_state(self):
        with FakeBatch() as h:
            h.clearance.unlink()
            with self.assertRaises(a.Refused):h.run()
            self.assertFalse(h.state.exists());self.assertEqual(h.prepare_calls,0)
    def test_input_dict_drift_rejected_before_prepare(self):
        with FakeBatch() as h:
            h.inputs['rules']['extra']='not bound'
            with self.assertRaises(a.Refused):h.run()
            self.assertEqual(h.prepare_calls,0)
    def test_adapter_byte_drift_rejected(self):
        with FakeBatch() as h:
            p=h.dir/'s2_adapter.py';p.write_text(p.read_text()+'\n# changed\n')
            with self.assertRaises(a.Refused):h.run()
    def test_new_guard_byte_drift_rejected(self):
        with FakeBatch() as h:
            p=h.dir/'s2_review_guards.py';p.write_text(p.read_text()+'\n# changed\n')
            with self.assertRaises(a.Refused):h.run()
    def test_scope_unit_drift_rejected(self):
        with FakeBatch() as h:
            d=a.read(h.clearance);d['scope']['unit_ids']=[];a.write(h.clearance,d)
            with self.assertRaises(a.Refused):h.run()
    def test_zero_limit_does_not_prepare(self):
        with FakeBatch() as h:
            with self.assertRaises(a.Refused):h.run(max_runs=0)
            self.assertEqual(h.prepare_calls,0)


if __name__=='__main__':unittest.main()
