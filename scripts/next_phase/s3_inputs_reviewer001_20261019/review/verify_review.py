"""Reviewer 001 signed-package verification; byte reads and synthetic runners only."""
from __future__ import annotations
import hashlib,json,pathlib,subprocess,sys,tempfile,datetime
HERE=pathlib.Path(__file__).resolve().parent
def sha(data):return hashlib.sha256(data).hexdigest()
def read(path):return json.loads(path.read_text())
def main():
    problems=[];m=read(HERE/'MANIFEST.json');s=read(HERE/'REVIEWER001_SIGNOFF.json')
    for x in m['members']:
        path=pathlib.PurePosixPath(x['path'])
        if path.is_absolute() or '..' in path.parts:problems.append('unsafe_member');continue
        p=HERE/x['path']
        if not p.is_file() or sha(p.read_bytes())!=x['sha256'] or p.stat().st_size!=x['bytes']:
            problems.append('manifest:'+x['path'])
    for x in s['bindings']:
        p=HERE/x['path']
        if not p.is_file() or sha(p.read_bytes())!=x['sha256']:problems.append('binding:'+x['path'])
    pre=sha(''.join(f"{x['path']}:{x['sha256']}\n" for x in sorted(s['bindings'],key=lambda x:x['path'])).encode())
    if pre!=s['pre_signing_commitment_sha256']:problems.append('pre_signing_commitment')
    if s['signed_by']!='Reviewer 001':problems.append('signed_by')
    if not datetime.datetime.fromisoformat(s['signed_at_utc']).tzinfo:problems.append('timezone')
    for k in ['S3_preparation_authorized','S3_execution_authorized','S2_authorized','repro_authorized']:
        if s['authorization'][k] is not False:problems.append('scope:'+k)
    replays=[]
    if not problems:
        with tempfile.TemporaryDirectory(prefix='reviewer001-rev1-verify-') as t:
            for script,args,saved,code in [
                ('reference/verify_static_inputs.py',['--submission',str(HERE/'reference/submission_rev1.zip'),'--reference',str(HERE/'reference')],'observations/static_inputs.json',0),
                ('reference/reproduce_original_gaps.py',['--submission',str(HERE/'reference/submission_rev1.zip')],'observations/original_counterexample_replay.json',1),
                ('replay_rev1_remaining.py',[],'observations/remaining_counterexamples.json',0)]:
                out=pathlib.Path(t)/(pathlib.Path(script).name+'.json')
                p=subprocess.run([sys.executable,str(HERE/script),*args,'--out',str(out)],
                                 cwd=HERE,capture_output=True,text=True,timeout=60)
                same=out.is_file() and read(out)==read(HERE/saved)
                ok=p.returncode==code and same
                replays.append({'script':script,'exit_code':p.returncode,'expected_exit':code,'same_observation':same})
                if not ok:problems.append('replay:'+script)
    print(json.dumps({'ok':not problems,'problems':problems,'payloads':len(m['members']),
        'bindings':len(s['bindings']),'replays':replays,'S3_execution_authorized':False,
        'real_candidate_calls':0,'real_witness_calls':0,'installs':0,'network_calls':0},ensure_ascii=False,indent=2))
    return 0 if not problems else 1
if __name__=='__main__':raise SystemExit(main())
