"""Reviewer 001: bounded new-batch technical correction collection, exact 32+24 calls.

The reviewed S3 instrument is reused unchanged. This entry point binds a new
batch and preserves witness bytes/metadata; it never rewrites the old batch.
"""
from __future__ import annotations
import argparse,json,pathlib,sys
import s3_adapter as a

HERE=pathlib.Path(__file__).resolve().parent
BATCH='next-S3-technical-correction1-20261019'
a.BATCH_ID=BATCH
_native_gate=a.clearance_problems
_native_witness=a.execute_witness

def gate(clearance_path,mode='run'):
    problems=_native_gate(clearance_path,mode)
    if problems:return problems
    line=a.read_json(HERE/'LINEAGE.json')
    for relkey,hashkey in [('old_ledger_relative','old_ledger_sha256'),
                           ('old_commitment_relative','old_commitment_sha256')]:
        path=a.WS/line[relkey]
        if not path.is_file() or a.sha(path)!=line[hashkey]:problems.append('prior_history_drift:'+relkey)
    return problems

a.clearance_problems=gate

def witness(row,run_dir,interpreter,runner=None):
    run_dir=pathlib.Path(run_dir)
    a.write_json(run_dir/'round_meta.json',{
        'batch_id':BATCH,'run_id':a.run_id_of(row),'row_key':row['row_key'],
        'source_row_key':row['source_row_key'],'call_kind':'witness',
        'commitment_sha256':a.sha(a.COMMITMENT_PATH)})
    child={};native_run=runner or a._popen
    def capture(*args):
        result=native_run(*args);child.update(result);return result
    result=_native_witness(row,run_dir,interpreter,capture)
    output=run_dir/'witness.json'
    result['witness_json_sha256']=a.sha(output) if output.is_file() else None
    pid=child.get('child_pid');init=set();end=set();bad=0
    path=run_dir/'netlog.jsonl'
    if path.is_file():
        for text in path.read_text().splitlines():
            if not text.strip():continue
            try:event=json.loads(text)
            except json.JSONDecodeError:bad+=1;continue
            kind=event.get('kind') or event.get('event')
            if kind=='observer_init':init.add(event.get('pid'))
            elif kind=='observer_end':end.add(event.get('pid'))
    ok=type(pid) is int and pid>0 and pid in init and pid in end and bad==0
    result['network']={'pytest_pid':None,'witness_pid':pid,'netlog_present':path.is_file(),
                       'main_pid_pair':ok,'malformed_lines':bad,'ok':ok}
    if not ok:
        result['problems'].append('witness_network_evidence_incomplete')
        if result['status']=='completed':result['status']='failed'
    return result

a.execute_witness=witness

def main():
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--state-root',type=pathlib.Path);parser.add_argument('--clearance',type=pathlib.Path)
    parser.add_argument('--run',action='store_true');parser.add_argument('--prepare',action='store_true')
    args,_=parser.parse_known_args()
    if args.run or args.prepare:
        problems=gate(args.clearance,'run' if args.run else 'prepare')
        if args.state_root:
            prior=(a.WS/a.read_json(HERE/'LINEAGE.json')['old_ledger_relative']).parent.resolve()
            current=args.state_root.resolve()
            if current==prior or prior in current.parents:problems.append('state_root_must_not_modify_old_batch')
        if problems:
            print(json.dumps({'refused':True,'problems':problems},ensure_ascii=False,indent=2));return 2
        if args.run:
            if not args.state_root:print('state_root required');return 2
            # This is only metadata/offline preparation, never a candidate test.
            # Every run/resume rechecks the same exact inventories before dispatch.
            try:a.prepare(args.state_root)
            except Exception as err:
                print(json.dumps({'refused':True,'problems':['preparation_failed:'+repr(err)]},ensure_ascii=False));return 3
    return a.main()

if __name__=='__main__':raise SystemExit(main())
