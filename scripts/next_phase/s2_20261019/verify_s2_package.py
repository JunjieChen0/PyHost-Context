"""Reviewer 001 reviewed S2 status verifier; no candidate, interpreter or installer is run."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',type=Path)
    args=parser.parse_args();here=Path(__file__).resolve().parent
    ws=args.workspace or Path(os.environ.get('PYHOST_PROJECT') or here.parents[3])
    result=subprocess.run([sys.executable,'-B',str(here/'s2_adapter.py'),'--status'],
        env={**os.environ,'PYHOST_PROJECT':str(ws),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
    try:obs=json.loads(result.stdout)
    except ValueError:obs=dict(ok=False,problems=['invalid_status_output'],stderr=result.stderr)
    print(json.dumps(dict(ok=result.returncode==0 and obs.get('ok') is True,status=obs,
        scope='reviewed runtime binding and frozen design; distribution package uses top-level verify_review.py',
        candidate_or_tool_calls=0,installs=0,network_calls=0),ensure_ascii=False,indent=2))
    return 0 if result.returncode==0 and obs.get('ok') is True else 1


if __name__=='__main__':raise SystemExit(main())
