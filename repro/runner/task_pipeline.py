#!/usr/bin/env python3
"""One task/seed: select two parents, public-only evolution, sealed handoff later."""
from __future__ import annotations
import argparse,hashlib,json,math,os,shutil,sys,time,tomllib
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

def append_event(path, row):
    with path.open('a',encoding='utf8') as stream:
        stream.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')

def failure_kind(error, stage):
    if stage=='configuration':return 'configuration_invalid'
    if stage=='generation':
        return 'no_complete_candidate' if 'No complete candidate' in str(error) else 'agent_execution_failed'
    if stage=='interface':return 'solver_interface_invalid'
    return 'public_selfcheck_invalid'

def final_public_review(task, records, output_dir):
    """Freeze one solver after comparing all public selfcheck evidence.

    This is a risk assessment on visible cases, never an independent hidden test.
    """
    examined=[]
    for record in records:
        source=output_dir/'evaluations'/record['label']/'candidate_solver.py'
        config=output_dir/'evaluations'/(record['label']+'.yaml')
        if not source.is_file() or not config.is_file():
            raise RuntimeError('Final review lacks source or provenance for '+record['label'])
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        if digest!=record['sha256'] or not record['valid']:
            raise RuntimeError('Final review found a changed or invalid solver: '+record['label'])
        static_gate(task,source.read_text(encoding='utf8'))
        components=record['visible'].get('components') or {}
        values=[float(x) for x in components.values() if isinstance(x,(int,float)) and math.isfinite(x)]
        center=sum(values)/len(values) if values else None
        examined.append({'label':record['label'],'solver_sha256':digest,
            'configuration':str(config.relative_to(output_dir)),
            'public_metric':record['visible']['metric'],'research_reward':record['reward'],
            'elapsed_seconds':record['elapsed'],'public_components':components,
            'component_summary':({'count':len(values),'mean':center,'worst':min(values),
                'spread':math.sqrt(sum((x-center)**2 for x in values)/len(values))}
                if values else None),
            'public_settings':record['visible'].get('settings'),
            'source_bytes':source.stat().st_size,
            'uncertainty':('Fewer than four comparable public components; '
                           'no independent generalization test' if len(components)<4 else
                           'Public components only; no independent generalization test')})
    by_label={row['label']:row for row in records}
    shared=set.intersection(*(set(x['public_components']) for x in examined)) if examined else set()
    comparable=len(shared)>=4 and all(all(isinstance(item['public_components'][k],(int,float))
        and math.isfinite(item['public_components'][k]) for k in shared) for item in examined)
    sensitivity={'common_public_cases':sorted(shared),'leave_one_case_out':[],
                 'method':'mean of saved public case diagnostics, never an independent held-out test',
                 'comparable':comparable}
    if comparable:
        for omitted in sorted(shared):
            remaining=shared-{omitted}
            ranked=sorted(examined,key=lambda item:(
                sum(item['public_components'][k] for k in remaining)/len(remaining),
                item['research_reward']),reverse=True)
            sensitivity['leave_one_case_out'].append({'omitted_case':omitted,
                'diagnostic_ranking':[x['label'] for x in ranked]})
        for item in examined:
            ranks=[row['diagnostic_ranking'].index(item['label'])+1
                   for row in sensitivity['leave_one_case_out']]
            item['sensitivity']={'worst_rank':max(ranks),'mean_rank':sum(ranks)/len(ranks),
                'top_count':sum(rank==1 for rank in ranks)}
    highest=max(examined,key=lambda item:item['research_reward'])
    tie_band=max(1e-9,abs(highest['research_reward'])*0.005)
    near=[item for item in examined if highest['research_reward']-item['research_reward']<=tie_band]
    if comparable and len(near)>1:
        chosen=min(near,key=lambda item:(item['sensitivity']['worst_rank'],
            item['sensitivity']['mean_rank'],-item['research_reward']))
        selection_rule='Within 0.5% of the top public research reward, prefer better worst and mean public leave-one-case-out diagnostic rank; otherwise prefer higher reward'
    else:
        chosen=highest
        selection_rule='Highest valid public research reward; no comparable case evidence or no near-tie candidate'
    selected=by_label[chosen['label']]
    selected_components=chosen['public_components']
    risk_flags=[]
    for item in examined:
        other=item['public_components']
        if item['label']==selected['label'] or not comparable:continue
        diffs=[selected_components[k]-other[k] for k in sorted(shared)]
        wins=sum(x>1e-9 for x in diffs)
        losses=sum(x < -1e-9 for x in diffs)
        item['selected_solver_pairwise']={'wins':wins,'losses':losses,
                                           'ties':sum(abs(x)<=1e-9 for x in diffs),
                                           'worst_regression':min(diffs)}
        if losses>wins:
            risk_flags.append('Selected solver loses more matched public components than '+item['label'])
    if not comparable:
        risk_flags.append('Too few finite comparable public components for a distributional check')
    elif chosen['sensitivity']['worst_rank']>1:
        risk_flags.append('Selected solver loses diagnostic rank when one public case is omitted')
    if any(item.get('public_settings') and any(x.get('error') for x in item['public_settings'])
           for item in examined):
        risk_flags.append('At least one public candidate has a failed setting')
    review={'basis':'Official public selfchecks and saved public diagnostics only',
        'independent_holdout_test':False,'candidate_count':len(examined),
        'candidates':examined,'selected_label':selected['label'],
        'highest_reward_label':highest['label'],'near_tie_labels':[x['label'] for x in near],
        'public_reward_tie_band':tie_band,
        'selected_solver_sha256':selected['sha256'],
        'selected_yaml_sha256':hashlib.sha256(selected['config'].encode()).hexdigest(),
        'official_recheck_count':0,'recheck_limit':0,
        'recheck_note':'Saved deterministic selfchecks reused; no extra independent public instances claimed',
        'public_case_sensitivity':sensitivity,
        'memorization_review':'manual_review_required; static inspection cannot prove absence of public-answer shortcuts',
        'extra_public_instances':'not_generated',
        'risk_flags':risk_flags,'uncertainty':bool(risk_flags),
        'generalization_uncertainty':True,
        'generalization_uncertainty_reason':'No independent held-out public task instances were evaluated',
        'selection_rule':selection_rule,
        'hidden_used_for_selection':False}
    write_json(output_dir/'final_generalization_review.json',review)
    return selected

def main():
    p=argparse.ArgumentParser();p.add_argument('task',choices=NAMES)
    for name in ('project-root','task-root','seed-pool','output-dir'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--seed',type=int,required=True);p.add_argument('--max-versions',type=int,required=True)
    p.add_argument('--dry-run',action='store_true');a=p.parse_args()
    if a.output_dir.exists():p.error('Run directory exists')
    if a.max_versions!=6:p.error('This experiment requires six distinct valid solver versions')
    if not (a.task_root/'environment/methods/main'/ENTRY.get(a.task,'solver.py')).is_file():p.error('Official starter missing')
    sys.path.insert(0,str(a.project_root/'vendor/agent_engine'))
    from task_runtime import validate_yaml,execute,require_research_backend
    if not a.dry_run:require_research_backend()
    a.output_dir.mkdir(parents=True);t0=time.monotonic()
    from src.meta_model.metamodel import MetaModel
    os.environ['EVOMAS_ALLOWED_MODELS']=' '.join(MODELS)
    seed_files=sorted(a.seed_pool.glob('*.yaml'))
    if len(seed_files)<2:raise ValueError('Need at least two YAML configurations')
    budget=tomllib.loads((a.task_root/'task.toml').read_text())['agent']['timeout_sec']
    pool=a.output_dir/'working_pool';pool.mkdir()
    selection_pool=a.output_dir/'input'/'selection_pool';selection_pool.mkdir(parents=True)
    snapshot=a.output_dir/'input'/'pool_snapshot';snapshot.mkdir(parents=True)
    for source in seed_files:shutil.copyfile(source,snapshot/source.name)
    write_json(a.output_dir/'input'/'provenance.json',{
        'task':a.task,'seed':a.seed,'max_valid_solver_versions':a.max_versions,
        'official_agent_timeout_sec':budget,'models':MODELS,'meta_model':os.environ['META_MODEL'],
        'instruction_sha256':hashlib.sha256((a.task_root/'instruction.md').read_bytes()).hexdigest(),
        'pool_snapshot':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in seed_files},
        'source_pool':str(a.seed_pool.resolve()),
        'parent_sources':{p.name:('task_evolved' if p.name.startswith('task__evolved_') else 'general')
                          for p in seed_files}})
    for source in seed_files:
        data=yaml.safe_load(source.read_text());
        for agent in data['agents'].values():
            if not source.stem.startswith('task__evolved_'):
                agent['model_id']='openai:deepseek-v4-flash-s2'
            agent['tools']=list(TOOLS)
        dest=selection_pool/source.name;dest.write_text(yaml.safe_dump(data,sort_keys=False,allow_unicode=True))
        validate_yaml(dest.read_text(),MODELS)
    meta=None if a.dry_run else MetaModel(model_id='openai:'+os.environ['META_MODEL'],memory_path=str(a.output_dir/'memory.json'),memory_evolution=True)
    if meta:meta.strict_selection=True
    instruction=(a.task_root/'instruction.md').read_text()
    skill_file=a.project_root/'repro/skill/skills.json'
    role_file=a.project_root/'repro/memory'/a.task/'roles.json'
    skills=json.loads(skill_file.read_text()) if skill_file.is_file() else []
    roles=json.loads(role_file.read_text()) if role_file.is_file() else []
    write_json(a.output_dir/'input'/'knowledge_snapshot.json',{'skills':skills,'roles':roles})
    query='RSI-Exam '+NAMES[a.task]+' '+instruction[:300]
    if a.dry_run: parents=[str(selection_pool/x.name) for x in seed_files[:2]]
    else:
        for selection_attempt in range(3):
            try:
                parents=meta.select(query,instruction[:9000],str(selection_pool),k=2)
                break
            except Exception:
                if selection_attempt==2:raise
    if (len(parents)!=2 or len(set(map(str,parents)))!=2 or
        any(Path(x).resolve().parent!=selection_pool.resolve() for x in parents) or
        len({hashlib.sha256(Path(x).read_bytes()).hexdigest() for x in parents})!=2):
        raise ValueError('Meta model must select two distinct configurations in the current pool')
    if meta:
        meta.research_tool_policy=('Use only these eight research tools for each agent: '+', '.join(TOOLS)+
          '. Keep exactly one terminal writer and the official artifact interface. New agents start on openai:deepseek-v4-flash-s2.')
        meta.mutation_decision_path=a.output_dir/'mutation_rationales.jsonl'
    records=[];versions=[];attempts=[];operations=[];history=[]
    pool_index={}
    def index_pool(entry,config):
        digest=hashlib.sha256(config.encode()).hexdigest()
        row={'label':entry['label'],'config_sha256':digest,'solver_sha256':entry['sha256'],
             'valid':entry['valid'],'can_parent':entry['valid'],'reward':entry['reward'] if entry['valid'] else None,
             'parents':entry['parents'],'elapsed_seconds':entry['elapsed']}
        if digest not in pool_index:pool_index[digest]=row
        elif entry['valid'] and (not pool_index[digest]['can_parent'] or
             entry['reward']>pool_index[digest]['reward']):pool_index[digest]=row
        if entry['valid']:
            target=pool/(digest[:16]+'.yaml')
            if not target.exists():target.write_text(config,encoding='utf8')
        write_json(pool/'index.json',list(pool_index.values()))
    def test(label,config,action,parent_labels=(),evidence=None):
        version=a.output_dir/'evaluations'/label;version.mkdir(parents=True,exist_ok=True)
        (a.output_dir/'evaluations'/f'{label}.yaml').write_text(config)
        started=time.monotonic();result=None;runtime={};stage='configuration'
        try:
            validate_yaml(config,MODELS)
            stage='generation'
            runtime=execute(a.output_dir/'evaluations'/f'{label}.yaml',a.task_root,a.task,version,history,
                            dry_run=a.dry_run,role_notes=roles,shared_skills=render(skills))
            stage='interface'
            solver=version/'candidate_solver.py';static_gate(a.task,solver.read_text())
            stage='selfcheck'
            result=({'valid':True,'metric':.01,'components':{}} if a.dry_run else evaluate(a.task,a.task_root,solver,version))
            if result.get('valid') and (not isinstance(result.get('metric'),(int,float)) or
                                        not math.isfinite(result['metric'])):
                raise ValueError('Official public selfcheck produced no finite complete metric')
            if result.get('valid') is not True:
                result['failure_stage']='selfcheck'
                result['failure_type']='public_selfcheck_incomplete'
        except Exception as ex:
            result={'valid':False,'failure_stage':stage,'failure_type':failure_kind(ex,stage),
                    'error_type':type(ex).__name__,'error':str(ex)[-2500:]}
        write_json(version/'visible.json',result)
        elapsed=time.monotonic()-started
        entry={'label':label,'config':config,'visible':result,'valid':bool(result.get('valid')),
               'elapsed':elapsed,'action':action,'parents':list(parent_labels),
               'sha256':hashlib.sha256((version/'candidate_solver.py').read_bytes()).hexdigest() if (version/'candidate_solver.py').is_file() else None,
               'evidence':evidence,'reward':score(a.task,result)-1e-6*elapsed if result.get('valid') else float('-inf')}
        attempts.append({k:v for k,v in entry.items() if k!='config'});write_json(a.output_dir/'attempts.json',attempts)
        index_pool(entry,config)
        append_event(a.output_dir/'trajectory_evidence.jsonl',{
            'task':a.task,'label':label,'action':action,'parents':list(parent_labels),
            'configuration_sha256':hashlib.sha256(config.encode()).hexdigest(),
            'solver_sha256':entry['sha256'],'public_valid':entry['valid'],
            'public_child_score':result.get('metric'),'components':result.get('components'),
            'settings':result.get('settings'),'elapsed_seconds':elapsed,
            'failure_stage':result.get('failure_stage'),'observed_failure':result.get('failure_type'),
            'trace':f'evaluations/{label}/','verdict':'unresolved' if entry['valid'] else 'invalid'})
        if entry['valid']:
            config_hash=hashlib.sha256(config.encode()).hexdigest()
            prior=next((i for i,r in enumerate(records)
                        if hashlib.sha256(r['config'].encode()).hexdigest()==config_hash),None)
            if prior is None:records.append(entry)
            elif entry['reward']>records[prior]['reward']:records[prior]=entry
            if not any(v['sha256']==entry['sha256'] for v in versions):
                versions.append(entry)
                history.append({'label':label,'valid':True,'metric':result['metric'],'visible':result,
                                'source':(version/'candidate_solver.py').read_text()[:16000]})
            else:
                attempts[-1]['duplicate_solver']=True
                write_json(a.output_dir/'attempts.json',attempts)
            write_json(a.output_dir/'candidate_manifest.json',
                       [{k:v for k,v in v.items() if k!='config'} for v in versions])
        return entry
    for i,path in enumerate(parents):
        config=Path(path).read_text()
        append_event(a.output_dir/'decision_evidence.jsonl',{'phase':'before','label':f'parent_{i:02d}',
            'action':'selection','parent_file':Path(path).name,
            'reason':'Meta model selected this YAML from the frozen public pool; specific rationale unavailable',
            'alternatives':[p.name for p in seed_files if p.name!=Path(path).name],
            'prediction':'unknown','evidence_ref':'input/pool_snapshot/' + Path(path).name})
        selected=test(f'parent_{i:02d}',config,'selection')
        append_event(a.output_dir/'decision_evidence.jsonl',{'phase':'after','label':selected['label'],
            'public_valid':selected['valid'],'public_metric':selected['visible'].get('metric'),
            'solver_sha256':selected['sha256'],'elapsed_seconds':selected['elapsed']})
    # Failed generation and failed public checks are logged, never counted as versions.
    # Read the official agent budget from this task's task.toml.
    deadline=t0+float(budget)
    while not a.dry_run and len(versions)<a.max_versions and time.monotonic()<deadline:
        excluded={(tuple(x.get('parents',[]))) for x in attempts if x.get('action')=='crossover'}
        pair=complement_pair(records,excluded)  # Search ALL valid configurations in W first.
        if pair:
            i,j,evidence=pair;left,right=records[i],records[j];action='crossover'
            # Avoid repeatedly attempting a pair whose hypothesis failed unchanged.
            if any(x['action']=='crossover' and x.get('parents')==[left['label'],right['label']] for x in attempts):
                pair=None
        if not pair:
            action='mutation'
            if not records: break
            left=max(records,key=lambda r:r['reward'])
            right=None
            evidence={'selection':'best_reward_single_parent','reward':left['reward'],
                      'parent':left['label']}
        parent_records=(left,) if action=='mutation' else (left,right)
        parent_labels=[r['label'] for r in parent_records]
        label=f'attempt_{len(attempts):03d}'
        append_event(a.output_dir/'decision_evidence.jsonl',{'phase':'before','label':label,
            'action':action,'parents':parent_labels,
            'evidence_refs':[f'evaluations/{r["label"]}/visible.json' for r in parent_records],
            'observed_evidence':evidence,'failure_hypothesis':'unverified',
            'alternative_actions':['mutation' if action=='crossover' else 'crossover','retest'],
            'predicted_metric_direction':'unknown','predicted_new_failure':'unknown',
            'estimated_cost_seconds':'unknown'})
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
            attempts.append({'label':label,'action':action,'parents':parent_labels,
                             'valid':False,'error':str(ex)[-1200:],'evidence':evidence})
            write_json(a.output_dir/'attempts.json',attempts)
            append_event(a.output_dir/'decision_evidence.jsonl',{'phase':'after','label':label,
                'failure_type':'configuration_invalid','error':str(ex)[-1200:]})
            continue
        rationale_path=a.output_dir/'mutation_rationales.jsonl'
        declared=None
        if action=='mutation' and rationale_path.is_file():
            try:declared=json.loads(rationale_path.read_text(encoding='utf8').splitlines()[-1]).get('decision')
            except (ValueError,IndexError):pass
        append_event(a.output_dir/'decision_evidence.jsonl',{'phase':'before_public_test','label':label,
            'action':action,'declared_rationale':declared,'planned_component':component,
            'parent_yaml_sha256':[hashlib.sha256(r['config'].encode()).hexdigest() for r in parent_records],
            'child_yaml_sha256':hashlib.sha256(proposed.encode()).hexdigest(),
            'prediction':(declared or {}).get('predicted_effect','unknown')})
        child=test(label,proposed,action,parent_labels,evidence)
        append_event(a.output_dir/'decision_evidence.jsonl',{'phase':'after','label':label,
            'changed_component':component,'public_valid':child['valid'],
            'public_metric':child['visible'].get('metric'),'parent_public_metrics':[
                r['visible'].get('metric') for r in parent_records],
            'solver_sha256':child['sha256'],'elapsed_seconds':child['elapsed'],
            'prediction_met':'unknown'})
        operations.append({'label':label,'action':action,'component':component,'parents':parent_labels,
                           'evidence':evidence,'public_metric':child['visible'].get('metric'),'valid':child['valid']})
        with (a.output_dir/'trajectory_evidence.jsonl').open('a') as stream:
            stream.write(json.dumps({'task':a.task,'label':label,'action':action,'component':component,
                'parent_public_score':left['visible']['metric'],'public_child_score':child['visible'].get('metric'),
                'public_valid':child['valid'],'verdict':('worked' if child['valid'] and child['reward']>left['reward'] else 'regressed' if child['valid'] else 'invalid'),
                'observed_evidence':evidence},ensure_ascii=False)+'\n')
    if not records:raise RuntimeError('No valid public solver; no sealed submission')
    complete=len(versions)==a.max_versions
    winner=(final_public_review(a.task,versions,a.output_dir) if complete else
            max(versions,key=lambda r:r['reward']))
    source=a.output_dir/'evaluations'/winner['label']/'candidate_solver.py'
    if complete:
        shutil.copyfile(source,a.output_dir/'best_visible_solver.py')
    image_id=None
    if not a.dry_run:
        import subprocess
        image_id=subprocess.run(['docker','image','inspect','--format','{{.Id}}',f'rsi-subset-{NAMES[a.task]}:public'],
            text=True,capture_output=True,check=True).stdout.strip()
    (a.output_dir/'best_reward_config.yaml').write_text(winner['config'])
    write_json(a.output_dir/'summary.json',{'status':'completed' if complete else 'incomplete_public',
      'task':a.task,'seed':a.seed,'max_versions':a.max_versions,'completed_versions':len(versions),
      'candidate_count':len(attempts),'candidates':[{k:v for k,v in r.items() if k!='config'} for r in versions],
      'valid_configurations_in_W':len(records),
      'operations':operations,'best_visible':{k:v for k,v in winner.items() if k!='config'},
      'hidden_status':'not_run','hidden_score':None,'public_image_id':image_id,
      'final_public_review':'final_generalization_review.json' if complete else None,
      'research_seconds':time.monotonic()-t0,'meta_model':os.environ.get('META_MODEL'), 'model_pool':MODELS,
      'entrypoint':ENTRY.get(a.task,'solver.py')})
    print(json.dumps({'run':str(a.output_dir),'valid_versions':len(versions),'best':winner['label']},ensure_ascii=False))
    if not complete and not a.dry_run:
        raise RuntimeError(f'Only {len(versions)}/{a.max_versions} distinct valid selfchecked solver versions; '
                           'final public review and hidden submission were not run')
if __name__=='__main__':main()
