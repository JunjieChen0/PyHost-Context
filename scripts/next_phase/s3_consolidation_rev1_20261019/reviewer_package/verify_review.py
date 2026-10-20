"""Verify the bounded consolidation review, with no candidate execution."""
from pathlib import Path
import hashlib,json
HERE=Path(__file__).resolve().parent
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def verify():
    m=json.loads((HERE/'MANIFEST.json').read_text());s=json.loads((HERE/'REVIEWER001_SIGNOFF.json').read_text());problems=[]
    paths={i['path'] for i in m['members']};actual={p.relative_to(HERE).as_posix() for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if paths|{'MANIFEST.json'}!=actual:problems.append('member_set_mismatch')
    for i in m['members']:
        p=HERE/i['path']
        if not p.is_file() or p.stat().st_size!=i['bytes'] or digest(p)!=i['sha256']:problems.append('member:'+i['path'])
    for i in s['bindings']:
        p=HERE/i['path']
        if not p.is_file() or digest(p)!=i['sha256']:problems.append('binding:'+i['path'])
    pre=hashlib.sha256(''.join(f"{i['path']}:{i['sha256']}\n" for i in sorted(s['bindings'],key=lambda i:i['path'])).encode()).hexdigest()
    if pre!=s['pre_signing_commitment_sha256']:problems.append('pre_signing_commitment')
    if s['signed_by']!='Reviewer 001' or s['decision']!='CONSOLIDATION_CLOSED_WITH_CORRECTED_ACCOUNTING':problems.append('scope')
    return {'ok':not problems,'payloads':len(m['members']),'bindings':len(s['bindings']),'problems':problems,'candidate_calls':0,'network_calls':0}
if __name__=='__main__':
    r=verify();print(json.dumps(r,ensure_ascii=False,indent=2));raise SystemExit(0 if r['ok'] else 1)
