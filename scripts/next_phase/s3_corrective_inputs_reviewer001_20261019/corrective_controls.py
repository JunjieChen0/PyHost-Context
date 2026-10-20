"""Original-control checks for the corrected B probe, with no candidate import."""
import re

WIDTHS={'B-C1':78,'B-C2':38,'B-C3':78,'B-C4':118}
SGR=re.compile(r'\x1b\[[0-9;]*m')

def expected_original(context):
    width=WIDTHS[context]
    return {'formatter_width_default':width,'formatter_width_explicit_70':70,
            'argparse_width_explicit_70':70,'max_help_line_width_default':width-1,
            'max_help_line_width_explicit_70':69,'argparse_max_line_width_explicit_70':69}

def validate_original(context,payload):
    problems=[];expected=expected_original(context)
    if not isinstance(payload,dict) or payload.get('case')!='B' or payload.get('errors')!={}: 
        return {'valid':False,'problems':['invalid_case_or_probe_errors'],'expected':expected}
    observed=(payload.get('observations') or {}).get('width')
    if not isinstance(observed,dict):return {'valid':False,'problems':['width_payload_missing'],'expected':expected}
    for key,value in expected.items():
        if type(observed.get(key)) is not int or observed[key]!=value:problems.append('field:'+key)
    for text_key,metric in [('default_visible_text','max_help_line_width_default'),
                            ('explicit_visible_text','max_help_line_width_explicit_70'),
                            ('argparse_visible_text','argparse_max_line_width_explicit_70')]:
        text=observed.get(text_key)
        if not isinstance(text,str) or not text:
            problems.append('raw_text_missing:'+text_key);continue
        actual=max(map(len,SGR.sub('',text).splitlines()),default=0)
        if observed.get(metric)!=actual:problems.append('reported_text_metric_mismatch:'+metric)
    if observed.get('probe_version')!='B-short-tokens-x-description-v2':problems.append('probe_version')
    return {'valid':not problems,'problems':problems,'expected':expected,
            'interpretation':'original control only; no mutant or repair outcome automatically certified'}
