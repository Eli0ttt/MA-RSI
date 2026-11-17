#!/usr/bin/env python3
"""One task/seed: select two parents, public-only evolution, sealed handoff later."""
from __future__ import annotations
import argparse,hashlib,json,math,os,shutil,sys,time
from pathlib import Path
import yaml
from task_visible import NAMES,ENTRY,evaluate,static_gate
from trajectory_evidence import mutation_component,crossover_compatible,complement_pair
from skill_memory import render

TOOLS=('research_read_context','research_read_public','research_read_feedback','research_read_trace',
       'research_read_history','research_write_candidate','research_preflight','research_probe_candidate')
MODELS=['openai:'+x.strip() for x in os.environ['MODEL_POOL'].split(',')]

def score(task,result):
    v=float(result['metric'])
    if task=='graph':return 100/(1+max(0,-v)/100)
    if task=='dcf':return 100/(1+max(0,-v))
    if task in ('game2048','cxr'):return v
    return 100*max(0,min(1,v))

def write_json(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str)+'\n')

def main():
    p=argparse.ArgumentParser();p.add_argument('task',choices=NAMES)
    for name in ('project-root','task-root','seed-pool','output-dir'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--seed',type=int,required=True);p.add_argument('--max-versions',type=int,required=True)
    p.add_argument('--dry-run',action='store_true');a=p.parse_args()
    if a.output_dir.exists():p.error('Run directory exists')
    if a.max_versions<1:p.error('max-versions must be positive')
    if not (a.task_root/'environment/methods/main'/ENTRY.get(a.task,'solver.py')).is_file():p.error('Official starter missing')
    a.output_dir.mkdir(parents=True);t0=time.monotonic()
    sys.path.insert(0,str(a.project_root/'vendor/agent_engine'))
    from src.meta_model.metamodel import MetaModel
    from task_runtime import validate_yaml,execute
    os.environ['EVOMAS_ALLOWED_MODELS']=' '.join(MODELS)
    seed_files=sorted(a.seed_pool.glob('*.yaml'))
    if len(seed_files)<2:raise ValueError('Need at least two YAML configurations')
    pool=a.output_dir/'working_pool';pool.mkdir()
    for source in seed_files:
        data=yaml.safe_load(source.read_text());
        for agent in data['agents'].values():
            if not source.stem.startswith('evolved_'):
                agent['model_id']='openai:deepseek-v4-flash-s2'
            agent['tools']=list(TOOLS)
        dest=pool/source.name;dest.write_text(yaml.safe_dump(data,sort_keys=False,allow_unicode=True))
        validate_yaml(dest.read_text(),MODELS)
    meta=None if a.dry_run else MetaModel(model_id='openai:'+os.environ['META_MODEL'],memory_path=str(a.output_dir/'memory.json'),memory_evolution=True)
    if meta:meta.strict_selection=True
    instruction=(a.task_root/'instruction.md').read_text()
    query='RSI-Exam '+NAMES[a.task]+' '+instruction[:300]
    if a.dry_run: parents=[str(pool/x.name) for x in seed_files[:2]]
    else:
        for selection_attempt in range(3):
            try:
                parents=meta.select(query,instruction[:9000],str(pool),k=2)
                break
            except Exception:
                if selection_attempt==2:raise
    if len(parents)!=2 or len(set(map(str,parents)))!=2 or any(Path(x).resolve().parent!=pool.resolve() for x in parents):
        raise ValueError('Meta model must select two distinct configurations in the current pool')
    if meta:
        meta.research_tool_policy=('Use only these eight research tools for each agent: '+', '.join(TOOLS)+
          '. Keep exactly one terminal writer and the official artifact interface. New agents start on openai:deepseek-v4-flash-s2.')
        meta.mutation_decision_path=a.output_dir/'mutation_rationales.jsonl'
    records=[];attempts=[];operations=[];history=[]
    def test(label,config,action,parent_labels=(),evidence=None):
        validate_yaml(config,MODELS)
        version=a.output_dir/'evaluations'/label;version.mkdir(parents=True,exist_ok=True)
        (a.output_dir/'evaluations'/f'{label}.yaml').write_text(config)
        started=time.monotonic();result=None;runtime={}
        try:
            runtime=execute(a.output_dir/'evaluations'/f'{label}.yaml',a.task_root,a.task,version,history,dry_run=a.dry_run)
            solver=version/'candidate_solver.py';static_gate(a.task,solver.read_text())
            result=({'valid':True,'metric':.01,'components':{}} if a.dry_run else evaluate(a.task,a.task_root,solver,version))
        except Exception as ex:
            result={'valid':False,'error_type':type(ex).__name__,'error':str(ex)[-2500:]}
        write_json(version/'visible.json',result)
        elapsed=time.monotonic()-started
        entry={'label':label,'config':config,'visible':result,'valid':bool(result.get('valid')),
               'elapsed':elapsed,'action':action,'parents':list(parent_labels),
               'sha256':hashlib.sha256((version/'candidate_solver.py').read_bytes()).hexdigest() if (version/'candidate_solver.py').is_file() else None,
               'evidence':evidence,'reward':score(a.task,result)-1e-6*elapsed if result.get('valid') else float('-inf')}
        attempts.append({k:v for k,v in entry.items() if k!='config'});write_json(a.output_dir/'attempts.json',attempts)
        if entry['valid']:
            records.append(entry)
            history.append({'label':label,'valid':True,'metric':result['metric'],'visible':result,
                            'source':(version/'candidate_solver.py').read_text()[:16000]})
            write_json(a.output_dir/'candidate_manifest.json',[{k:v for k,v in r.items() if k!='config'} for r in records])
        return entry
    for i,path in enumerate(parents):
        config=Path(path).read_text();test(f'parent_{i:02d}',config,'selection')
    # Failed generation and failed public checks are logged, never counted as versions.
    # Read the official agent budget from this task's task.toml.
    import tomllib
    budget=tomllib.loads((a.task_root/'task.toml').read_text())['agent']['timeout_sec']
    deadline=t0+float(budget)
    while not a.dry_run and len(records)<a.max_versions and time.monotonic()<deadline:
        excluded={(tuple(x.get('parents',[]))) for x in attempts if x.get('action')=='crossover'}
        pair=complement_pair(records,excluded)  # Search ALL valid configurations in W first.
        if pair:
            i,j,evidence=pair;left,right=records[i],records[j];action='crossover'
            # Avoid repeatedly attempting a pair whose hypothesis failed unchanged.
            if any(x['action']=='crossover' and x.get('parents')==[left['label'],right['label']] for x in attempts):
                pair=None
        if not pair:
            action='mutation';best=sorted(records,key=lambda r:r['reward'],reverse=True)[:2]
            if len(best)<2:break
            left,right=best;evidence={'selection':'top_two_reward','rewards':[left['reward'],right['reward']]}
        label=f'attempt_{len(attempts):03d}'
        feedback=json.dumps({'task':a.task,'observations':history[-5:], 'selection_evidence':evidence},ensure_ascii=False,default=str)[-18000:]
        try:
            if a.dry_run:
                raise ValueError('Dry run cannot fabricate an effective edit')
            if action=='crossover':
                proposed=meta.crossover(left['config'],right['config'],
                    {'accuracy':left['reward'],'evidence':evidence},{'accuracy':right['reward'],'evidence':evidence},model_list=MODELS)
                crossover_compatible(left['config'],right['config'],proposed)
                component='inherited_topology_and_models'
            else:
                proposed=meta.mutate(left['config'],{'accuracy':left['reward'],'evidence':evidence},feedback,model_list=MODELS)
                component=mutation_component(left['config'],proposed)
            validate_yaml(proposed,MODELS)
        except Exception as ex:
            attempts.append({'label':label,'action':action,'parents':[left['label'],right['label']],
                             'valid':False,'error':str(ex)[-1200:],'evidence':evidence})
            write_json(a.output_dir/'attempts.json',attempts)
            continue
        child=test(label,proposed,action,(left['label'],right['label']),evidence)
        operations.append({'label':label,'action':action,'component':component,'parents':[left['label'],right['label']],
                           'evidence':evidence,'public_metric':child['visible'].get('metric'),'valid':child['valid']})
        with (a.output_dir/'trajectory_evidence.jsonl').open('a') as stream:
            stream.write(json.dumps({'task':a.task,'label':label,'action':action,'component':component,
                'parent_public_score':left['visible']['metric'],'public_child_score':child['visible'].get('metric'),
                'public_valid':child['valid'],'verdict':('worked' if child['valid'] and child['reward']>left['reward'] else 'regressed' if child['valid'] else 'invalid'),
                'observed_evidence':evidence},ensure_ascii=False)+'\n')
    if not records:raise RuntimeError('No valid public solver; no sealed submission')
    winner=max(records,key=lambda r:r['reward']);source=a.output_dir/'evaluations'/winner['label']/'candidate_solver.py'
    shutil.copyfile(source,a.output_dir/'best_visible_solver.py')
    image_id=None
    if not a.dry_run:
        import subprocess
        image_id=subprocess.run(['docker','image','inspect','--format','{{.Id}}',f'rsi-subset-{NAMES[a.task]}:public'],
            text=True,capture_output=True,check=True).stdout.strip()
    (a.output_dir/'best_reward_config.yaml').write_text(winner['config'])
    write_json(a.output_dir/'summary.json',{'status':'completed' if len(records)==a.max_versions else 'partial_public',
      'task':a.task,'seed':a.seed,'max_versions':a.max_versions,'completed_versions':len(records),
      'candidate_count':len(attempts),'candidates':[{k:v for k,v in r.items() if k!='config'} for r in records],
      'operations':operations,'best_visible':{k:v for k,v in winner.items() if k!='config'},
      'hidden_status':'not_run','hidden_score':None,'public_image_id':image_id,
      'research_seconds':time.monotonic()-t0,'meta_model':os.environ.get('META_MODEL'), 'model_pool':MODELS,
      'entrypoint':ENTRY.get(a.task,'solver.py')})
    print(json.dumps({'run':str(a.output_dir),'valid_versions':len(records),'best':winner['label']},ensure_ascii=False))
if __name__=='__main__':main()
