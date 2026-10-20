"""Recompute the corrected accounting in temporary storage without touching signed files."""
from pathlib import Path
import json,subprocess,sys,tempfile
from verify_review import HERE,verify
def main():
    bound=verify()
    if not bound['ok']:print(json.dumps(bound,indent=2));return 1
    with tempfile.TemporaryDirectory(prefix='reviewer001-consolidation-replay-') as temp:
        t=Path(temp);calls=[]
        for command in [[sys.executable,str(HERE/'recompute_reviewed_accounting.py'),'--out',str(t/'accounting')],
                        [sys.executable,str(HERE/'replay_author_checker_gaps.py'),'--out',str(t/'gaps.json')]]:
            p=subprocess.run(command,capture_output=True,text=True);calls.append({'script':Path(command[1]).name,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
        pairs=[(t/'accounting'/n,HERE/'reviewed'/n) for n in ['reviewed_accounting.json','independent_checks.json','audit_summary.json']]+[(t/'gaps.json',HERE/'observations/author_checker_gaps.json')]
        comparisons=[{'file':b.relative_to(HERE).as_posix(),'identical':a.is_file() and a.read_bytes()==b.read_bytes()} for a,b in pairs]
        result={'ok':all(p['returncode']==0 for p in calls) and all(p['identical'] for p in comparisons),'binding':bound,
                'deterministic_outputs':comparisons,'calls':calls,'candidate_or_tool_executions':0,'installs':0,'network_calls':0,'signed_outputs_overwritten':False}
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['ok'] else 1
if __name__=='__main__':raise SystemExit(main())
