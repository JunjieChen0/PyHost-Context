"""Reviewer 001: independent byte-only S3 input verification (stdlib only)."""
from __future__ import annotations
import argparse
import ast
import copy
import hashlib
import io
import json
import pathlib
import tarfile
import zipfile
from collections import Counter

def sha(x):return hashlib.sha256(x).hexdigest()
def canonical(x):return sha(json.dumps(x,sort_keys=True,ensure_ascii=False).encode())
def tree_sha(files):
    # Match the declared pathlib.Path component ordering, not raw slash-string ordering.
    return sha((''.join(f'{p}:{sha(files[p])}\n' for p in sorted(files,key=pathlib.PurePosixPath))).encode())
def files_from_tar(blob):
    files={}
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as t:
        for member in t.getmembers():
            if member.isdir():continue
            if not member.isfile():raise ValueError('non-regular tar member')
            path=pathlib.PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:raise ValueError('unsafe member')
            rel='/'.join(path.parts[1:])
            if not rel or rel in files:raise ValueError('duplicate/empty relative path')
            files[rel]=t.extractfile(member).read()
    return files

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--submission',type=pathlib.Path,default=pathlib.Path(__file__).parent/'reference/submission.zip')
    ap.add_argument('--reference',type=pathlib.Path,default=pathlib.Path(__file__).parent/'reference')
    ap.add_argument('--out',type=pathlib.Path)
    args=ap.parse_args()
    checks=[]
    def check(name,ok):checks.append({'id':name,'ok':bool(ok)})
    with zipfile.ZipFile(args.submission) as z:
        names=[i.filename for i in z.infolist() if not i.is_dir()]
        data={n:z.read(n) for n in names}
    def read(p):return json.loads(data[p])
    manifest=read('PACKAGE_MANIFEST.json')
    check('unique_zip_names',len(names)==len(set(names)))
    check('manifest_names',set(names)=={'PACKAGE_MANIFEST.json'}|{x['path'] for x in manifest['members']})
    for x in manifest['members']:
        b=data[x['path']]
        check('manifest:'+x['path'],len(b)==x['bytes'] and sha(b)==x['sha256'])
    rows=read('S3_ROWS_20261019.json'); commitment=read('S3_PRE_RUN_COMMITMENT_20261019.json')
    all_rows=rows['suite_rows']+rows['witness_rows']
    check('actual_rows_hash',canonical(all_rows)==rows['rows_sha256']==commitment['rows_sha256'])
    check('counts',len(rows['suite_rows'])==192 and len(rows['witness_rows'])==48)
    check('unique_row_keys',len({x['row_key'] for x in all_rows})==240)
    keep=('row_key','case','context','product_variant','suite_variant','repeat','label_sha256','call_kind','order')
    labels=[{k:r[k] for k in keep if k in r} for r in all_rows]
    check('frozen_labels_order',canonical(labels)=='1861bab19983fff880828f117bf777da7c84249f2efd36e855f8bb8ffdf4fc79')
    trees=read('inputs/trees.json')['trees']
    products=read('inputs/products.json')['products']; suites=read('inputs/suites.json')['suites']
    maps={}
    for key,x in trees.items():
        b=data[x['path']];maps[key]=files_from_tar(b)
        check('tar:'+key,sha(b)==x['tar_sha256'] and len(b)==x['bytes'])
        check('tree:'+key,tree_sha(maps[key])==x['tree_sha256'])
    table_bytes=(args.reference/'CASE_TABLES_20261018_REV1.json').read_bytes()
    check('frozen_case_table',sha(table_bytes)=='961f36cc4ae49861bc035197f132aea002795c94cbac138a3765deb65dbc3454')
    table=json.loads(table_bytes)
    views={}
    for case in table['cases']:
        cid=case['case_id'];project=case['project']
        basename={'rich-argparse':'rich_argparse-1.8.0.tar.gz','rich-rst':'rich_rst-2.2.0.tar.gz'}[project]
        blob=(args.reference/basename).read_bytes()
        check('base_sdist:'+cid,sha(blob)==products[cid]['sdist_sha256'])
        base=files_from_tar(blob)
        check('original_tree:'+cid,base==maps[f'{cid}/original__S0_original'])
        for mutation in case['mutations']:
            variant=mutation['id'].split('-',1)[1]
            derived=copy.deepcopy(base)
            for h in sorted(mutation['patch']['hunks'],key=lambda h:h['line'],reverse=True):
                lines=derived[h['file']].decode().splitlines(keepends=True)
                before=lines[h['line']-1].rstrip('\n')
                check('hunk_anchor:'+mutation['id']+':'+h['file'],before==h['before'])
                indent=before[:len(before)-len(before.lstrip())]
                lines[h['line']-1]=indent+h['after']+'\n'
                derived[h['file']]=''.join(lines).encode()
            for rel,declared in mutation['patch']['derived_files'].items():
                check('derived_after:'+mutation['id']+':'+rel,sha(derived[rel])==declared['after_sha256'])
            check('declared_mutant_full_tree:'+mutation['id'],derived==maps[f'{cid}/{variant}__S0_original'])
            views[mutation['id']]=tree_sha({k:v for k,v in derived.items() if not k.startswith('tests/')})
        for variant in ['original','M1','M2','M3']:
            product=maps[f'{cid}/{variant}__S0_original']
            for suite in ['S0_original','R1_global_fix','R2_local_isolation','R3_explicit_parameterization']:
                composed=maps[f'{cid}/{variant}__{suite}']
                suite_source=maps[f'{cid}/original__{suite}']
                expected={k:v for k,v in product.items() if not k.startswith('tests/')}
                expected.update({k:v for k,v in suite_source.items() if k.startswith('tests/')})
                check('separated_composition:'+cid+'/'+variant+'/'+suite,composed==expected)
        for suite,entry in suites[cid].items():
            fs=maps[f'{cid}/original__{suite}']
            actual={p for p in set(base)|set(fs) if base.get(p)!=fs.get(p)}
            declared={x['path'] for x in entry['changed_files']}
            check('suite_changed_files:'+cid+'/'+suite,actual==declared and all(p.startswith('tests/') for p in actual))
            for x in entry['changed_files']:
                check('suite_pre_post:'+cid+'/'+suite+'/'+x['path'],
                      (sha(base[x['path']]) if x['path'] in base else None)==x['before_sha256']
                      and sha(fs[x['path']])==x['after_sha256'])
    check('nine_distinct_product_views',len(views)==9 and len(set(views.values()))==9)
    parameter_evidence=[]
    for case,path,fun in [('A','tests/conftest.py','no_color_context'),
                          ('B','tests/test_optparse.py','test_generated_usage'),
                          ('C','tests/test_main.py','test_cli_parse_error_handling')]:
        text=maps[f'{case}/original__R3_explicit_parameterization'][path].decode()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node,ast.FunctionDef) and node.name==fun:
                parameter_evidence.append({'case':case,'path':path,'function':fun,'line':node.lineno,
                                           'decorators':[ast.unparse(d) for d in node.decorator_list]})
    report={'schema_version':'pyhost-context/reviewer001-s3-static/1.0',
            'checks_run':len(checks),'failed':[c for c in checks if not c['ok']],
            'ok':all(c['ok'] for c in checks),'checks':checks,
            'payloads':len(manifest['members']),'composed_trees':len(maps),
            'named_mutants':len(views),'product_view_hashes':views,
            'rows_sha256':canonical(all_rows),'R3_parameterization_evidence':parameter_evidence,
            'candidate_imports_or_calls':0,'installs':0,'network_calls':0}
    if args.out:args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('checks','R3_parameterization_evidence')},ensure_ascii=False,indent=2))
    return 0 if report['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
