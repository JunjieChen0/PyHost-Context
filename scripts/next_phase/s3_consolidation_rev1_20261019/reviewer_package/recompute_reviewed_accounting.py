"""Reviewer 001: consolidate existing records without candidate execution or new inference.

No candidate imports, installers, test runs, network requests, or original-file writes.
Use closed signatures as scoped historical facts, not as fresh raw-data replays.
"""
from pathlib import Path
from collections import Counter
from decimal import Decimal
import argparse,hashlib,io,json,stat,tempfile,zipfile

HERE=Path(__file__).resolve().parent
SUBMISSION_SHA='7e53f5d1c60043b1781710f9a004e83c0388d27a232f5c426163bbb34723aec9'
PREVIOUS_SHA='6a0ae7fddc1432e3d7e2442e3165e3a6e3def483278d80473f2551604ec56e3c'
def sha(b):return hashlib.sha256(b).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def write(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
def zip_read(blob,path):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:return z.read(path)
def unpack(blob,d):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        seen=set()
        for i in z.infolist():
            p=Path(i.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in i.filename or stat.S_ISLNK(i.external_attr>>16) or i.filename in seen:raise ValueError('unsafe ZIP')
            seen.add(i.filename)
        z.extractall(d)
def duration_stat(records,key):
    vals=[Decimal(str(x[key])) for x in records if type(x.get(key)) in (int,float)]
    return {'covered_records':len(vals),'seconds':str(sum(vals,Decimal(0))),
            'meaning':'sum of this recorded duration field only; excludes unmeasured setup, other processes and human effort; not whole-project wall time or CPU'}

def main(out):
    checks=[]
    def ck(k,v):checks.append({'id':k,'ok':bool(v)})
    raw=(HERE/'reference/submission.zip').read_bytes();prev=(HERE/'reference/previous_s3_closure.zip').read_bytes()
    ck('submitted_archive',sha(raw)==SUBMISSION_SHA);ck('closed_S3_archive',sha(prev)==PREVIOUS_SHA)
    catalog=read(HERE/'reference/CLOSED_SOURCE_CATALOG.json');closed={}
    for name,pin in catalog.items():
        data=(HERE/pin['path']).read_bytes();ck('closed_source:'+name,len(data)==pin['bytes'] and sha(data)==pin['sha256']);closed[name]=json.loads(data)
    # Reference only prior accepted records; no original JSONL is rerun/reparsed.
    s3_close=json.loads(zip_read(prev,'REVIEWER001_S3_CORRECTIVE_CLOSURE_20261019.json'))
    earlier=zip_read(prev,'reference/prior_review.zip')
    old_s3_zip=zip_read(earlier,'reference/submission_results.zip')
    old_s3_blob=zip_read(old_s3_zip,'evidence/ledger.json');old_s3=json.loads(old_s3_blob)
    fix_bundle=zip_read(prev,'reference/submission_bundle.zip')
    fix_zip=zip_read(fix_bundle,'PyHost_S3_Corrective56_Results_20261019.zip')
    fix_s3_blob=zip_read(fix_zip,'evidence/ledger.json');fix_s3=json.loads(fix_s3_blob)
    old_qualified=json.loads(zip_read(earlier,'observations/review_qualified_adjudication.json'))
    with tempfile.TemporaryDirectory(prefix='reviewer001-accounting-') as temp:
        s=Path(temp);unpack(raw,s);m=read(s/'PACKAGE_MANIFEST.json');members=m['members']
        for item in members:
            p=s/item['path'];ck('submitted_member:'+item['path'],p.stat().st_size==item['bytes'] and sha(p.read_bytes())==item['sha256'])
        ck('exact_submitted_members',{p.relative_to(s).as_posix() for p in s.rglob('*') if p.is_file()}=={x['path'] for x in members}|{'PACKAGE_MANIFEST.json'})
        account=read(s/'results/evidence_accounting.json');src=s/'sources_snapshot'
        data={n:read(src/(n+'.json')) for n in ['exploration_ledger','exploration_analysis','validation_screening','validation_closure','s1a_ledger','d_ledger','s1b_ledger','native_ledger','s3_counts']}
        for n in data:
            if n!='s3_counts':ck('author_source_hash:'+n,sha((src/(n+'.json')).read_bytes())==account['sources'][n])
        for path,oldpath in [('REVIEWER001_RULING.md','REVIEWER001_RULING.md'),('REVIEWER001_S3_CORRECTIVE_CLOSURE_20261019.json','REVIEWER001_S3_CORRECTIVE_CLOSURE_20261019.json'),('REVIEWER001_FINDINGS.json','FINDINGS.json'),('audit_summary.json','observations/audit_summary.json'),('qualified_C_M2_cells.json','observations/qualified_C_M2_cells.json')]:
            ck('prior_closure_exact_bytes:'+path,(s/'provenance'/path).read_bytes()==zip_read(prev,oldpath))
        for name,ledger,blob in [('s3_original',old_s3,old_s3_blob),('s3_corrective',fix_s3,fix_s3_blob)]:
            snap=data['s3_counts'][name];kinds=Counter(r['call_kind'] for r in ledger['records'].values())
            ck('S3_snapshot_from_retained_ledger:'+name,snap['records']==len(ledger['records']) and snap['ledger_sha256']==sha(blob) and snap['suite']==kinds['suite'] and snap['witness']==kinds['witness'] and snap['status']==dict(Counter(x['status'] for x in ledger['records'].values())))
            ck('S3_hash_in_author_account:'+name,account['sources'][name+'_ledger']==sha(blob))
        e=data['exploration_ledger']['records'];ea=data['exploration_analysis'];v=data['validation_screening'];vc=data['validation_closure'];s1=data['s1a_ledger'];d=data['d_ledger'];sb=data['s1b_ledger'];native=data['native_ledger']
        rec=closed['exploration_40_closed_reconstruction.json'];exp_phase=Counter(x['phase'] for x in rec['runs'])
        ck('closed_exploration_recovery_12',exp_phase['recovery']==12 and len(rec['recovery_blocks'])==4)
        ck('closed_exploration_repair_28',exp_phase['repair']==28 and closed['exploration_repair_signoff.json']['completed_runs']==28)
        ck('S1a_calls_derived_from_records',sum(x['calls'] for x in s1['records'])==s1['summary']['calls_used'])
        ck('D_calls_derived_from_records',sum(x['calls'] for x in d['records'])==d['suite_call_count'])
        calls={
            'exploration_main_and_independent_B0':len(e),
            'exploration_recovery':exp_phase['recovery'],'exploration_repair':exp_phase['repair'],
            'validation_main_and_independent_B0':v['rounds_analyzed'],'validation_closure':vc['rounds_analyzed'],
            'S1b':len(sb['records']),'S3_original':len(old_s3['records']),'S3_corrective':len(fix_s3['records'])}
        research=sum(calls.values());admission=sum(x['calls'] for x in s1['records'])+sum(x['calls'] for x in d['records'])
        capture={k:sum(r.get('capture',{}).get(k,0) for r in native['records']) for k in ['direct_calls','discovery_calls','suite_calls','direct_successful_spawns']}
        ck('native_capture_split',capture['direct_calls']==capture['discovery_calls']+capture['suite_calls']==310 and capture['direct_successful_spawns']==310)
        ck('registered_total_arithmetic',research==3056 and admission==59 and research+admission+capture['direct_calls']==3425)
        primary=set(ea['projects'])|set(v['projects']);supp={r['project'] for r in sb['records']};admission_names={r['project'] for r in s1['records']+d['records'] if r['calls']>0}
        ck('project_strata_exact',len(primary)==25 and len(supp)==5 and not primary&supp and len(primary|supp)==30)
        levels=[]
        exp_confirmed=[u for u in ea['units'] if u['rq1_confirmed']]
        for u in exp_confirmed:levels.append({'project':u['project'],'condition':u['condition'],'highest_signed_level':'E4','stratum':'exploration'})
        val_confirmed=[u for u in v['units'] if u['rq1_confirmed']]
        for u in val_confirmed:levels.append({'project':u['project'],'condition':u['condition'],'highest_signed_level':'E3','stratum':'validation','prior_E2_registration_retained_not_additional_unit':True})
        for u in closed['s1b_E2_signoff.json']['E2_registrations']:
            project,condition=u['unit_id'].split('/');levels.append({'project':project,'condition':condition,'highest_signed_level':'E2','stratum':'S1b'})
        ck('signed_levels_distinct',len({(u['project'],u['condition']) for u in levels})==8 and Counter(u['highest_signed_level'] for u in levels)=={'E4':2,'E3':1,'E2':5})
        old_e2=closed['validation_prior_E2_signoff.json']['registered_units']
        ck('validation_E2_is_same_later_E3_unit',len(old_e2)==1 and old_e2[0]['project']=='rich-rst' and old_e2[0]['condition']=='K07_COLUMNS')
        def tier(units):
            counts=Counter()
            for u in units:
                if not u.get('all_execution_eligible') or u.get('identity_comparison_unresolved') or not u.get('identity_consistent'):key='unresolved_or_ineligible'
                elif u['rq1_confirmed']:key='operational_signal'
                elif u['flip_count']==0:key='no_observed_new_flip'
                else:key='other_unconfirmed_flip'
                counts[key]+=1
            return dict(counts)
        exp_kinds=Counter(r['kind'] for r in e);sb_arms=Counter(r['arm'] for r in sb['records'])
        ck('exploration_matrix_and_B0_split',exp_kinds=={'matrix':810,'b0_control':810})
        ck('S1b_matrix_and_B0_split',sb_arms=={'main':225,'control':225})
        stages={'exploration':{'projects':len(ea['projects']),'units':len(ea['units']),'unit_categories':tier(ea['units']),'matrix_calls':exp_kinds['matrix'],'independent_B0_calls':exp_kinds['b0_control']},
                'validation':{'projects':len(v['projects']),'units':len(v['units']),'unit_categories':tier(v['units']),'calls':v['rounds_analyzed'],'two_timeouts_already_inside_630':True},
                'S1b':{'projects':len(supp),'units':len(supp)*8,'calls':len(sb['records']),'matrix_calls':sb_arms['main'],'independent_B0_calls':sb_arms['control'],'E2_registered_signals':len(closed['s1b_E2_signoff.json']['E2_registrations']),'baseline_failures_retained':True,'toolchain_strata_not_pooled_with_primary':True}}
        ck('exploration_unit_retention',stages['exploration']['unit_categories']=={'no_observed_new_flip':134,'unresolved_or_ineligible':8,'operational_signal':2})
        ck('validation_unit_retention',stages['validation']['unit_categories']=={'no_observed_new_flip':47,'unresolved_or_ineligible':8,'operational_signal':1})
        old_cells=Counter(c['verdict'] for c in old_qualified['cells']);ck('old108_cells_retained',sum(old_cells.values())==108)
        native_executed=[r for r in native['records'] if r.get('capture')]
        research_types={'pytest_suite_session_calls':research-48-24,'standalone_witness_calls':48+24}
        ck('research_calls_split',research_types=={'pytest_suite_session_calls':2984,'standalone_witness_calls':72})
        normalized={'schema_version':'pyhost-context/reviewer001-qualified-accounting/1.1',
            'scope':'existing evidence consolidation; inherited prior findings, no new raw-test audit or candidate execution',
            'registered_calls':{'research_by_batch':calls,'research_total':research,'research_call_kinds':research_types,
                'admission_S1a_and_D_only':admission,'native_direct_pytest_calls':capture['direct_calls'],'covered_registration_total':research+admission+capture['direct_calls'],
                'statistical_sample_size':None,'OS_process_total':None,'whole_project_admission_total':None,
                'known_additional_unregistered_interruption_attempts':1,'extra_interruption_source':'validation position 87, previously disclosed; not in 630 ledger and not a new rerun by this review',
                'global_attempts_total':None,'meaning':'heterogeneous registered call entries; retain technical failures/timeouts and keep batches separated'},
            'sealed_feasibility_separate':{'core':270,'recovery':12,'functional_obligation_witness':54,'formal_calls':336,'successful_pilot':1,'successful_archived_calls':337,'has_ABC_repair_endpoint_in_this_stage':False,'scope':'historical closed stage counts carried forward, not a new raw replay or OS process count'},
            'projects':{'primary_exploration_and_validation':{'count':len(primary),'names':sorted(primary)},'supplementary_S1b':{'count':len(supp),'names':sorted(supp)},
                'research_registered_project_names':{'count':len(primary|supp),'names':sorted(primary|supp)},
                'with_at_least_one_A6_A7_or_research_registration_in_covered_snapshots':{'count':len(primary|supp|admission_names),'names':sorted(primary|supp|admission_names),'not_number_of_eligible_research_projects':True},
                'preparation_inclusive_project_names_in_covered_snapshots':len(primary|supp|{r['project'] for r in s1['records']+d['records']}),
                'original_26_mechanical_selection_claim_not_recertified':True},
            'RQ1_strata':stages,'highest_registered_unit_levels':levels,'same_rich_rst_unit_not_counted_twice_for_screening_E2_and_E3':True,
            'repair_endpoints':{'exploration':{'passed':6,'denominator':6,'projects':1,'units':2,'limited':True,'source':'reference/closed_records/exploration_repair_signoff.json'},
                'validation':{'passed':3,'denominator':3,'projects':1,'units':1,'limited_content_obligations':True},
                'not_combined_with_S3_cell_labels_as_one_success_rate':True},
            'S3':{'registered_attempts':296,'suite_calls':224,'witness_calls':72,'original_240_status':dict(Counter(r['status'] for r in old_s3['records'].values())),
                'original_108_repair_cell_labels':dict(old_cells),'corrective_12_repair_cell_labels':s3_close['C_M2_qualified_repair_cell_labels'],
                'retained_readings':old_cells['RETAINED'],'independent_original_suite_blindspots':s3_close['C_M2_independent_original_suite_blindspots'],
                'same_context_degradation_signed':s3_close['same_context_DEGRADED_signed'],'old_and_new_denominators_not_merged':True,
                '20_failed_calls_and12_technical_cells_are_overlapping_views_not_additive':True},
            'native_tool':{'prepared_cohort_projects':len(native['cohort_projects']),'projects_with_direct_capture':len(native_executed),
                'final_report_pairs_from_closed_reference':closed['native_final_closure_signoff.json']['summary']['paired_projects'],
                'capture_totals':capture,'worker_process_count_measured':False,'equal_intervention_or_strategy_superiority_claim':False},
            'denominator_rules':['Keep all 3056 registered research calls, including 20 old C-M2 technical failures and the two yaspin timeouts.',
                'Keep all 144 exploration and 56 validation units; the 8 isodate and 8 yaspin units are unresolved/ineligible, not negative or removed.',
                'Keep all 108 original S3 repair cells and all12 corrective repair cells in their separate accounting; report eligibility by endpoint.',
                'Do not add process attempts, repair cells, units and projects or subtract overlapping technical-failure views.',
                'Never use3056 or3425 as an independent statistical sample size or ecosystem prevalence denominator.'],
            'cost_scope':{'covered_registered_calls':3425,'whole_project_processes_or_total_cost_known':False,
                'measured_partial_duration_sums':{'exploration_suite_sessions':duration_stat(e,'duration_seconds'),'S1b_suite_sessions':duration_stat(sb['records'],'duration_seconds'),
                    'S3_original_suite_and_witness':duration_stat(list(old_s3['records'].values()),'duration_seconds'),'S3_corrective_suite_and_witness':duration_stat(list(fix_s3['records'].values()),'duration_seconds'),
                    'native_cli_wall_seconds':duration_stat([{'x':r['native_cli_timing']['native_cli_wall_seconds']} for r in native_executed],'x')},
                'author_reported_unverified_host_snapshot':{'extension_tree_bytes':account['cost']['extension_tree_bytes'],'review_directories':account['cost']['review_directories'],'global_inventory_or_human_effort_not_supplied':True},
                'unmeasured_or_outside_coverage':['earlier extension admissions and artifact-identity re-admissions','boot/interpreter/installer/dependency probes','worker and helper subprocesses','human review time','known interrupted attempt duration','other development and preparation work'],
                'claim_admission_or_review_is_dominant_cost_signed':False},
            'claim_limits':{'G1_satisfied':False,'G1_is_internal_rule_not_universal_CCF_B_requirement':True,'publication_tier_certified':False,
                'next_experiment_or20_to30_unit_threshold_created':False,'sample_rarity_or_ecosystem_prevalence_signed':False,
                'cross_project_observed_differences_can_be_reported_at_their_registered_levels':True,'independent_cross_project_mechanism_G1_not_established':True,
                'no_new_S2_or_repro_authorization':True,'no_new_candidate_or_tool_calls':True},
            'provenance_limits':['Some source snapshots are ledgers or signed derived analyses rather than raw per-test files; this review does not claim raw reanalysis of every call.',
                'No full environmental/interpreter/wheel reacquisition certification is added.',
                'CCF-B contribution sufficiency is not decided by completion counts, publication-tier labels, or the G1 rule alone.']}
        write(out/'reviewed_accounting.json',normalized)
        summary={'ok':all(c['ok'] for c in checks),'checks_run':len(checks),'failed':[c for c in checks if not c['ok']],
                 'research_calls':research,'suite_calls':2984,'witness_calls':72,'covered_registration_total':3425,
                 'primary_projects':25,'research_project_names':30,'covered_called_project_names':48,
                 'candidate_or_tool_executions':0,'installs':0,'network_calls':0,'historic_records_modified':0}
        write(out/'independent_checks.json',{'checks':checks,'ok':summary['ok']});write(out/'audit_summary.json',summary)
        print(json.dumps(summary,ensure_ascii=False,indent=2));return 0 if summary['ok'] else 1

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True,type=Path);a=ap.parse_args();raise SystemExit(main(a.out))
