"""Reproduce consolidation checker blind spots on disposable COPIES only.

Runs the already inspected read-only accounting script; no candidate execution.
"""
from pathlib import Path
import argparse,json,subprocess,sys,tempfile
from recompute_reviewed_accounting import HERE,unpack,read,write

def main(out):
    checks=[];raw=(HERE/'reference/submission.zip').read_bytes()
    changes=[
        ('source_native_capture_changed', 'sources_snapshot/native_ledger.json',
         lambda d:next(r for r in d['records'] if r.get('capture'))['capture'].__setitem__('direct_calls',12)),
        ('stored_cohort_count_changed', 'results/evidence_accounting.json',
         lambda d:d['cohorts']['exploration'].__setitem__('runs',999)),
        ('stored_cost_changed', 'results/evidence_accounting.json',
         lambda d:d['cost'].__setitem__('process_invocations_total',-1)),
        ('endpoint_project_changed', 'results/evidence_accounting.json',
         lambda d:d['endpoints']['E4'].__setitem__('projects',['not-a-study-project']))]
    for name,relative,mutate in [('unmodified_positive_control',None,None)]+changes:
        with tempfile.TemporaryDirectory(prefix='reviewer001-accounting-checker-') as temp:
            root=Path(temp);unpack(raw,root)
            if relative:
                p=root/relative;data=read(p);mutate(data);write(p,data)
            proc=subprocess.run([sys.executable,str(root/'scripts/recompute_accounting.py')],cwd=root,capture_output=True,text=True)
            payload=json.loads(proc.stdout) if proc.stdout.strip().startswith('{') else {}
            checks.append({'id':name,'author_returncode':proc.returncode,'author_reported_ok':payload.get('ok'),
                'expected_source_or_output_change':relative,'gap_reproduced':bool(relative) and proc.returncode==0 and payload.get('ok') is True,
                'original_bytes_modified':False})
    result={'ok':checks[0]['author_reported_ok'] is True and all(c['gap_reproduced'] for c in checks[1:]),
            'positive_controls':1,'blind_spots_reproduced':sum(c['gap_reproduced'] for c in checks),'checks':checks,
            'meaning':'demonstrates old checker coverage gaps; independent corrected accounting and byte bindings supplied in this review; no historical experiment reopened',
            'candidate_calls':0,'installs':0,'network_calls':0}
    write(out,result);print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['ok'] else 1
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True,type=Path);a=ap.parse_args();raise SystemExit(main(a.out))
