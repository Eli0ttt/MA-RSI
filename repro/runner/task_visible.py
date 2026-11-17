#!/usr/bin/env python3
"""Public-only evaluation of a task-specific candidate in its official image.

This script never opens tests/ or heldout/ and never modifies the task checkout.
"""
from __future__ import annotations
import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

NAMES = {'bbo':'bbo_noisy_continuous', 'inventory':'bbo_simopt_inventory',
         'graph':'johnson1991_leighton_graph_coloring',
         'qec':'qec_decoder_arena', 'cxr':'cxr_ood_triage_policy',
         'game2048':'game2048_policy_search', 'dcf':'finscope_dcf_valuation'}
ALLOWED = {'bbo':{'numpy'}, 'inventory':{'numpy'}, 'graph':{'numpy','gc_lib'},
           'qec':{'numpy','scipy','stim','pymatching'}, 'cxr':set(),
           'game2048':{'game2048'}, 'dcf':{'numpy'}}
CAPS = {'bbo':(1,'12g',None), 'inventory':(2,'12g',None), 'graph':(2,'12g',None),
        'qec':(8,'16g',1000), 'cxr':(2,'512m',1800),
        'game2048':(4,'12g',1950), 'dcf':(2,'512m',1800)}
ENTRY = {'game2048':'policy.py','dcf':'forecaster.py'}

def static_gate(task, source):
    tree=ast.parse(source)
    classes=[x for x in tree.body if isinstance(x,ast.ClassDef)]
    funcs=[x for x in tree.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))]
    if task in ('bbo','inventory'):
        optimizer=next((x for x in classes if x.name=='Optimizer'),None)
        if optimizer is None: raise ValueError('Optimizer class missing')
        found={x.name:x for x in optimizer.body if isinstance(x,ast.FunctionDef)}
        for fn,required in {'__init__':['self','dim','lower','upper','budget','seed','rng'],
                            'ask':['self','n'],'tell':['self','X','y']}.items():
            if fn not in found or [x.arg for x in found[fn].args.args][:len(required)] != required:
                raise ValueError(f'Optimizer.{fn} signature missing or wrong')
    else:
        name={'graph':'solve','qec':'decode','cxr':'triage',
              'game2048':'choose_move','dcf':'solve'}[task]
        fn=next((x for x in funcs if x.name==name),None)
        required={'graph':['data'],'qec':['setting','dets'],
                  'cxr':['cases','resources','seed'], 'game2048':['board'],
                  'dcf':['problem']}[task]
        if fn is None or [x.arg for x in fn.args.args][:len(required)] != required:
            raise ValueError(f'{name}{tuple(required)} entrypoint missing')
    if task=='inventory':
        for node in ast.walk(tree):
            if (isinstance(node,ast.Attribute) and node.attr in ('erf','math') and
                isinstance(node.value,ast.Name) and node.value.id in ('np','numpy')):
                raise ValueError('NumPy 2.2.6 has no np.erf/np.math; use stdlib math.erf or a tested approximation')
    for node in ast.walk(tree):
        names=[]
        if isinstance(node,ast.Import): names=[a.name for a in node.names]
        if isinstance(node,ast.ImportFrom): names=[node.module or '']
        for name in names:
            head=name.split('.')[0]
            if head and head not in sys.stdlib_module_names and head not in ALLOWED[task]:
                raise ValueError(f'forbidden import {name}')
    if task=='game2048':
        if len(source.encode())>10_000_000: raise ValueError('policy.py exceeds official 10 MB cap')
        forbidden={'os','subprocess','time','datetime','random','secrets','pathlib','socket','inspect','importlib','sys'}
        for node in ast.walk(tree):
            if isinstance(node,ast.Import) and any(a.name.split('.')[0] in forbidden for a in node.names):
                raise ValueError('2048 policy uses a forbidden runtime facility')
            if isinstance(node,ast.ImportFrom) and (node.module or '').split('.')[0] in forbidden:
                raise ValueError('2048 policy uses a forbidden runtime facility')

def command(task, root, solver, runner, setting=None):
    cpus,mem,_=CAPS[task]
    image=f'rsi-subset-{NAMES[task]}:public'
    cmd=['docker','run','--rm','--network','none','--read-only',
         '--tmpfs','/tmp:rw,nosuid,size=1g','--cpus',str(cpus),
         '--memory',mem,'--pids-limit','512','-e','PYTHONDONTWRITEBYTECODE=1',
         '--mount',f'type=bind,src={solver.resolve()},dst=/app/methods/main/{ENTRY.get(task,"solver.py")},readonly']
    if task=='game2048':
        # Its official selfcheck writes visible_result.json beside itself.
        cmd.remove('--read-only')
    if task=='qec':
        cmd += ['--mount',f'type=bind,src={runner.resolve()},dst=/runner/qec_public_runner.py,readonly']
    cmd += [image,'python']
    if task=='qec': cmd += ['/runner/qec_public_runner.py',setting]
    elif task=='dcf': cmd += ['/app/fit_calibration.py','/app/methods/main']
    elif task=='bbo': cmd += ['/app/selfcheck.py','--json']
    else: cmd += ['/app/selfcheck.py']
    return cmd

def evaluate(task,root,solver,output_dir=None):
    static_gate(task,solver.read_text(encoding='utf-8'))
    image=f'rsi-subset-{NAMES[task]}:public'
    subprocess.run(['docker','image','inspect',image],check=True,stdout=subprocess.DEVNULL)
    if task=='bbo':
        from continuous_visible import evaluate_solver, CandidateSelfcheckError
        result=evaluate_solver(solver.read_text(),output_dir,image)
        if not result.get('contract_valid'):raise CandidateSelfcheckError(result.get('contract_issue','contract invalid'))
        trace_path=output_dir/'public_trace.json'
        trace=json.loads(trace_path.read_text()) if trace_path.is_file() else []
        components={f"case_{r['instance']}_seed_{r['seed_index']}":-float(r['latent_best_20_60_120'][-1])
                    for r in trace if r.get('latent_best_20_60_120')}
        return {'valid':True,'task':task,'metric':float(result['score']),
                'metric_direction':'higher','components':components, 'stdout_tail':str(result)[-1600:]}
    _,_,limit=CAPS[task]
    runner=Path(__file__).with_name('qec_public_runner.py')
    if task=='qec':
        rows=[]
        for name in ('color_d5','color_d5_Z','color_d7','color_d9'):
            meta=json.loads((root/'environment'/'settings_visible'/name/'meta.json').read_text())
            budget=float(meta['decode_budget_sec'])
            try:
                proc=subprocess.run(command(task,root,solver,runner,name),capture_output=True,text=True,timeout=budget+90)
                if proc.returncode: raise ValueError(proc.stderr[-1800:])
                rows.append(json.loads(proc.stdout.strip().splitlines()[-1]))
            except (subprocess.TimeoutExpired,ValueError,IndexError,KeyError,json.JSONDecodeError) as exc:
                rows.append({'setting':name,'score':0.0,'error':str(exc)[-1000:]})
        return {'valid':len(rows)==4 and all(not x.get('error') for x in rows),
                'task':task,'metric':sum(x['score'] for x in rows)/len(rows),
                'metric_direction':'higher','settings':rows}
    proc=subprocess.run(command(task,root,solver,runner),capture_output=True,text=True,timeout=limit)
    if proc.returncode:
        raise ValueError(f'official visible selfcheck exit={proc.returncode}: {proc.stderr[-1800:]}')
    text=proc.stdout
    if task=='inventory':
        found=re.search(r'visible raw_metric\s*=\s*([\d.eE+-]+); mapped reward\s*=\s*([\d.eE+-]+)',text)
        if not found:raise ValueError(f'official score missing: {text[-1000:]}')
        return {'valid':True,'task':task,'raw_metric':float(found[1]),'metric':float(found[2]),
                'metric_direction':'higher','stdout_tail':text[-1600:]}
    if task=='cxr':
        probe=r'''import json,math,sys
sys.path.insert(0,'/app/methods/main')
from solver import triage
from pathlib import Path
root=Path('/app/data')
def rows(name):return [json.loads(x) for x in (root/name).read_text().splitlines() if x.strip()]
grade=rows('grade.jsonl');cases=[{k:v for k,v in c.items() if not k.startswith('gold_')} for c in grade]
resources={'budget':.20,'labels':['Effusion','Cardiomegaly','Edema','Pneumonia','Consolidation','Atelectasis','Pneumothorax','Mass'], 'calibration':rows('calibration.jsonl'),'sites':sorted({c['site_id'] for c in grade}),'n_bins':15}
found=triage(cases,resources,0)
assert isinstance(found,list) and len(found)==len(cases)
ids=[p['study_uid'] for p in found]
assert len(set(ids))==len(ids) and set(ids)=={c['study_uid'] for c in cases}
assert all(isinstance(p['risk'],(int,float)) and math.isfinite(p['risk']) and 0<=p['risk']<=1 for p in found)
print('CONTRACT_PASS')'''
        probe_cmd=command(task,root,solver,runner)[:-2]+['python','-c',probe]
        checked=subprocess.run(probe_cmd,capture_output=True,text=True,timeout=1800)
        if checked.returncode or 'CONTRACT_PASS' not in checked.stdout:
            raise ValueError('CXR risk/id contract invalid: '+checked.stderr[-1200:])
        found=re.search(r'triage_utility_pct \(visible\) = ([\d.]+)',text)
        sites=re.findall(r'^\s*([A-Za-z0-9_-]+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s*$',text,re.M)
        if not found or not sites: raise ValueError('Missing official CXR visible score or per-site output')
        return {'valid':True,'task':task,'metric':float(found[1]),'metric_direction':'higher',
                'components':{f'site_{s}':float(sens)-float(brier) for s,_,_,sens,brier in sites},
                'stdout_tail':text[-1600:]}
    if task=='game2048':
        start=text.find('{')
        data,_=json.JSONDecoder().raw_decode(text[start:])
        if data.get('valid_fraction')!=1 or not isinstance(data.get('mean_score'),(int,float)):
            raise ValueError('2048 public games incomplete or invalid')
        return {'valid':True,'task':task,'metric':data['mean_score'],'metric_direction':'higher',
                'components':{str(x['seed']):x['score'] for x in data['instances']},
                'cpu_seconds_per_game':data.get('cpu_seconds_per_game')}
    if task=='dcf':
        found=re.search(r'median abs valuation error \(out-of-sample, visible\):\s*([\d.]+)',text)
        if not found or 'WARNING:' in text or 'INVALID drivers' in text:
            raise ValueError('DCF public calibration incomplete or invalid: '+text[-800:])
        return {'valid':True,'task':task,'metric':-float(found[1]),
                'raw_mave':float(found[1]),'metric_direction':'research rank; lower raw MAVE is better',
                'stdout_tail':text[-1200:]}
    totals=re.search(r'total conflicts over\s+(\d+)/(\d+) solved graph\(s\)\s*=\s*(\d+)',text)
    if not totals:raise ValueError(f'official graph diagnostics missing: {text[-1000:]}')
    ok,all_count,conf=map(int,totals.groups())
    failed=all_count-ok
    # A killed or invalid graph forfeits that instance only under the official rule.
    # Rank completed cases ahead of missing cases; this is a research surrogate.
    effective=conf+1000000*failed
    return {'valid':failed==0,'task':task,'raw_conflicts':conf,
            'solved_graphs':ok,'total_graphs':all_count,'failed_graphs':failed,
            'effective_conflicts':effective,
            'metric':-effective,'metric_direction':'research surrogate; lower failed/conflicts is better',
            'stdout_tail':text[-2200:],
            'components':{m[0]:-float(m[1]) for m in re.findall(r'^\s*(\S+)\s+\d+\s+[\d.]+s\s+conflicts=\s*(\d+)',text,re.M)}}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('task',choices=NAMES)
    p.add_argument('task_root',type=Path)
    p.add_argument('solver',type=Path)
    p.add_argument('output',type=Path)
    args=p.parse_args()
    if args.output.exists():p.error('Refusing to overwrite output')
    if not (args.task_root/'environment'/'Dockerfile').is_file():p.error('official task root missing')
    if not args.solver.is_file():p.error('candidate solver missing')
    try: result=evaluate(args.task,args.task_root,args.solver)
    except Exception as exc:
        result={'valid':False,'task':args.task,'error_type':type(exc).__name__,
                'error':str(exc)[-2000:]}
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['valid'] else 1

if __name__=='__main__':raise SystemExit(main())
