#!/usr/bin/env python3
"""Persistent EvoMAS knowledge; all shared state writes occur after whole-task evaluation."""
from __future__ import annotations
import argparse,asyncio,hashlib,json,os,re,shutil,sys,tempfile
from pathlib import Path
import yaml
from skill_memory import add_run, render

TASKS={'bbo':'bbo_noisy_continuous','inventory':'bbo_simopt_inventory',
       'graph':'johnson1991_leighton_graph_coloring','qec':'qec_decoder_arena',
       'cxr':'cxr_ood_triage_policy','game2048':'game2048_policy_search','dcf':'finscope_dcf_valuation'}
COMMON=['research_read_context','research_read_public','research_read_feedback',
        'research_read_trace','research_read_history','research_write_candidate',
        'research_preflight','research_probe_candidate']
BBO=SUBSET=COMMON
# Older configurations have been kept in their old states, not silently migrated.
# New shared pool configurations use only the canonical research_* names.
LEGACY={'bbo_read_context': 'research_read_context', 'bbo_read_public_data': 'research_read_public', 'bbo_read_prior_feedback': 'research_read_feedback', 'bbo_read_prior_trace': 'research_read_trace', 'bbo_read_history': 'research_read_history', 'bbo_write_candidate': 'research_write_candidate', 'bbo_preflight_candidate': 'research_preflight', 'bbo_probe_candidate': 'research_probe_candidate', 'subset_read_context': 'research_read_context', 'subset_read_public': 'research_read_public', 'subset_read_history': 'research_read_history', 'subset_write_candidate': 'research_write_candidate', 'subset_preflight': 'research_preflight', 'subset_public_python': 'research_probe_candidate'}
TO_BBO=TO_SUBSET={**{name:name for name in COMMON},**LEGACY}


def atom(path,obj):
 path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
 tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8');os.replace(tmp,path)

def adapt(text,task,source_task=None):
 c=yaml.safe_load(text)
 if not isinstance(c,dict) or not isinstance(c.get('agents'),dict):raise ValueError('Invalid MAS YAML')
 mapping=TO_BBO if task=='bbo' else TO_SUBSET
 allowed=set(BBO if task=='bbo' else SUBSET)
 for agent_id,a in c['agents'].items():
  if source_task is None and task in ('graph','qec'):
   # Explicit roles survive task adaptation; every seed agent has a distinct job.
   graph_roles={
    'worker1':'Propose one efficient TabuCol-style solver with incremental conflict counts and bounded per-graph search.',
    'worker2':'Propose a distinct restart or perturbation policy; compare per-graph conflicts on public instances.',
    'worker3':'Audit color range, return lengths, deadline guards, and fallbacks on the largest public graphs.',
    'proc_l1_1':'Propose a fast incremental conflict-minimizing search with a concrete bounded update rule.',
    'proc_l1_2':'Propose an alternative initialization and restart strategy with explicit large-graph complexity.',
    'judge_l1':'Compare both proposals by public conflicts, 45-second feasibility, and implementation risk; select a direction.',
    'proc_l2_1':'Refine the selected search implementation; address a measured conflict or throughput bottleneck.',
    'proc_l2_2':'Independently challenge the selected algorithm on deadlines, color validity, and relabelled graphs.',
    'judge_l2':'Compare the two revisions using public per-graph conflicts and correctness; state unresolved risks.',
    'aggregator':'Synthesize one complete solve(data) returning all requested colorings, integers in [0,target_k); selfcheck within 45 seconds per graph.'}
   qec_roles={
    'worker1':'Investigate correlation-aware decoding using the public DEM and labeled development data; test a concrete hypothesis.',
    'worker2':'Investigate dev-calibrated weights or color-pair decoding; avoid memorizing public evaluation labels.',
    'worker3':'Audit decode output shape and boolean dtype for d5, d5_Z, d7, d9 and per-setting runtime.',
    'proc_l1_1':'Propose a complete correlation-aware decoder using allowed libraries and public development labels.',
    'proc_l1_2':'Propose an independent calibration or color-pair method and its generalization risk at d7 and d9.',
    'judge_l1':'Compare both decoder proposals on four public setting scores, valid return dtype, and time budgets.',
    'proc_l2_1':'Revise the selected decoder, keeping the complete decode(setting,dets) interface.',
    'proc_l2_2':'Challenge d7/d9 behavior, boolean [n_shots,n_observables] shape, and unseen-setting robustness.',
    'judge_l2':'Select the revision with strongest four-setting public evidence; list failures explicitly.',
    'aggregator':'Produce one complete decode(setting,dets) returning a boolean [n_shots,n_observables] array for every setting; check all four public settings.'}
   focus=(graph_roles if task=='graph' else qec_roles).get(agent_id)
   if focus is None:
    focus=f'As {agent_id}, develop a distinct {task} solver proposal and validate it against the official task contract.'
   a['prompt']=focus+' Use only imports and public assets allowed by the current official task.'
  if source_task in TASKS and source_task!=task:
   # Carry the MAS roles/topology/model knowledge, but not another task's solver instructions.
   role=str(a.get('role') or a.get('id') or 'worker')[:80]
   a['prompt']=(f'Act as {role} on the CURRENT RSI-Exam task. Follow its official '
                'instruction, cooperate through this MAS topology, and produce a '
                'valid task-specific solver. Do not reuse a previous task solver.')
  tools=[]
  for t in a.get('tools',[]):
   mapped=mapping.get(t,t)
   if mapped in allowed and mapped not in tools:tools.append(mapped)
  a['tools']=tools
  models={'openai:'+x.strip() for x in os.environ.get('MODEL_POOL','deepseek-v4-flash-s1,deepseek-v4-flash-s2').split(',')}
  if a.get('model_id') not in models:a['model_id']='openai:'+os.environ.get('META_MODEL','deepseek-v4-flash-s1')
 c.pop('meta',None)
 return yaml.safe_dump(c,sort_keys=False,allow_unicode=True)

def knowledge_paths(root,task):
 return (root/'repro/memory'/task/'experiences.json',
         root/'repro/skill/skills.json',root/'repro/memory'/task/'roles.json')

def read_list(path):
 return json.loads(path.read_text()) if path.is_file() else []

def ensure_shared(root):
 import fcntl
 guard=root/'repro/skill/.lock';guard.parent.mkdir(parents=True,exist_ok=True)
 with guard.open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  paths=[root/'repro/skill/skills.json']
  for task in TASKS:
   memory,_,roles=knowledge_paths(root,task)
   paths.extend((memory,roles))
  for path in paths:
   path.parent.mkdir(parents=True,exist_ok=True)
   if not path.exists():atom(path,[])

def initialize(root,state):
 if state.exists():raise FileExistsError(state)
 ensure_shared(root)
 state.mkdir(parents=True)
 atom(state/'manifest.json',{'schema':'evomas-rsi-task-memory-v2','runs':[]})


def export(root,state,task,dest,pool_policy="seeds",shared_parent_pool=None):
 if dest.exists():raise FileExistsError(dest)
 if task not in TASKS:raise ValueError(task)
 memory_path,skills_path,roles_path=knowledge_paths(root,task)
 memory=read_list(memory_path)
 if os.environ.get('KNOWLEDGE_READ_MODE','shared')=='none':memory=[]
 manifest=json.loads((state/'manifest.json').read_text())
 dest.mkdir(parents=True);(dest/'working_pool').mkdir()
 if pool_policy not in ('seeds','task_top2'):
  raise ValueError('pool_policy must be seeds or task_top2')
 pool=list(sorted((state/'pool').glob('*.yaml')))
 evolved=[p for p in pool if p.stem.startswith(f'evolved_{task}_')]
 if pool_policy=='seeds':evolved=[]
 else:
  # Rank ONLY comparable public runs under the same task image and instruction.
  ranked={}
  records=memory
  signature=None
  for item,record in reversed(list(zip(manifest['runs'],records))):
   if item.get('task')!=task:continue
   candidate=(record.get('public_image_id'),record.get('instruction_sha256'))
   if signature is None:signature=candidate
   if candidate!=signature:continue
   value=record.get('new_accuracy')
   if isinstance(value,(int,float)) and not isinstance(value,bool) and record.get('success'):
    ranked[f'evolved_{task}_{item.get("config_sha256", "")[:12]}.yaml']=float(value)
  evolved=sorted(evolved,key=lambda p:(ranked.get(p.name,float('-inf')),p.name),reverse=True)[:2]
 if shared_parent_pool is not None:
  shared=Path(shared_parent_pool).resolve()
  for name in ('majority_vote.yaml','smoa.yaml'):
   source=shared/name
   if not source.is_file():raise FileNotFoundError(source)
   # Frozen shared parents are already adapted to this specific task.
   target=dest/'working_pool'/name
   target.write_bytes(source.read_bytes())
 else:
  for p in [state/'pool/majority_vote.yaml',state/'pool/smoa.yaml',*evolved]:
   source_task=task if p.name.startswith('evolved_') else None
   (dest/'working_pool'/p.name).write_text(adapt(p.read_text(),task,source_task),encoding='utf8')
 memory=[item for item in memory if item.get('task')==task or
         (not item.get('task') and TASKS[task] in item.get('query',''))]
 # The state keeps task routing metadata; EvoMAS ActionExperience accepts only
 # its seven original fields when loading the per-run memory snapshot.
 experience_fields=('query','action','config_changes','old_accuracy',
                    'new_accuracy','success','analysis')
 atom(dest/'memory.json',[{key:item[key] for key in experience_fields} for item in memory])
 skills=read_list(skills_path)
 roles=read_list(roles_path)
 if os.environ.get('KNOWLEDGE_READ_MODE','shared')=='none':skills=[];roles=[]
 atom(dest/'skills.json',skills)
 atom(dest/'role_memory.json',roles)
 (dest/'skills_context.txt').write_text(render(skills)+'\n',encoding='utf8')
 atom(dest/'manifest.json',{'schema':manifest['schema'],
  'runs':[{'task':x['task'],'config_sha256':x['config_sha256']} for x in manifest['runs']]})
 atom(dest/'provenance.json',{'state':str(state.resolve()),'task':task,
  'state_sha256':hashlib.sha256((state/'manifest.json').read_bytes()).hexdigest(),
  'shared_parent_pool':str(Path(shared_parent_pool).resolve()) if shared_parent_pool else None})

def summary_of(run):
 s=json.loads((run/'summary.json').read_text())
 task=s.get('task','bbo')
 if task not in TASKS:raise ValueError('Unknown task '+str(task))
 if task=='bbo':
  count=s.get('completed_solver_versions',0)
  if not (count==s.get('solver_limit') or bool(s.get('stopped_invalid_yaml')) and 2<=count<s.get('solver_limit',0)):
   raise ValueError('Incomplete BBO without validated partial winner')
 else:
  expected=s.get('max_versions')
  count=s.get('completed_versions',0)
  complete=s.get('status')=='completed' and count==expected
  partial=s.get('status')=='partial_public' and bool(s.get('stopped_invalid_yaml')) and 2<=count<expected
  if (expected not in (4,6) or not (complete or partial) or len(s.get('candidates',[]))!=count):
   raise ValueError('Incomplete subset run or unsupported version count')
 if s.get('hidden_status')!='completed' or not isinstance(s.get('hidden_score'),(int,float)):
  raise ValueError('Hidden score required before committing knowledge')
 if task=='bbo':
  diagnostic=Path(s.get('final_result',{}).get('diagnostic_path') or '')
  if diagnostic.is_file():
   detail=json.loads(diagnostic.read_text())
   if (detail.get('correctness') is False and detail.get('num_evals')==0
       and any('grader failed:' in str(error) for error in detail.get('errors') or [])):
    raise ValueError('Trusted BBO grader failed before solver evaluation; do not commit')
 winner=run/'best_visible_solver.py'
 if hashlib.sha256(winner.read_bytes()).hexdigest()!=s['best_visible']['sha256']:
  raise ValueError('Winner SHA mismatch')
 return task,s

def consolidate(root, task, run, s):
 """Deterministic public-only task memory; hypotheses remain labelled as such."""
 config=(run/'best_visible_config.yaml' if task=='bbo' else
         run/'evaluations'/f"{s['best_visible']['label']}.yaml")
 if not config.is_file():raise FileNotFoundError(config)
 candidates=s.get('candidates',[])
 def score(item):
  return (item.get('visible_score') if task=='bbo' else
          item.get('visible',{}).get('metric'))
 parents={x['label']:score(x) for x in candidates if x.get('label') in ('parent_00','parent_01')}
 best=(s['best_visible'].get('visible_score') if task=='bbo' else
       s['best_visible'].get('visible',{}).get('metric'))
 decision_path=run/'decision_evidence.jsonl'
 rows=[json.loads(line) for line in decision_path.read_text().splitlines()] if decision_path.is_file() else []
 parent_best=max((value for value in parents.values() if isinstance(value,(int,float))),default=None)
 peak=parent_best
 evidence=[]
 for row in rows:
  visible=row.get('child_public') or {}
  score_now=(row.get('child_score') if task=='bbo' else visible.get('metric'))
  if not isinstance(score_now,(int,float)) or not visible.get('valid',True):
   outcome='invalid'
  elif not row.get('effective_change') or peak is None or score_now==peak:
   outcome='unresolved'
  elif score_now>peak:
   outcome='worked'
  else:
   outcome='regressed'
  setting_errors=[v.get('setting') for v in visible.get('settings',[]) if v.get('error')]
  decision=row.get('model_decision') or {}
  evidence.append({'step':row.get('label'),
    'component':(row.get('changed_components') or [None])[0],
    'public_score':score_now,'best_before':peak,'outcome':outcome,
    'per_setting_errors':setting_errors,
    'hypothesis_unverified':str(decision.get('root_cause_hypothesis') or '')[:250]})
  if isinstance(score_now,(int,float)) and visible.get('valid',True):
   peak=max(score_now,peak) if peak is not None else score_now
 analysis=json.dumps({'task':task,'basis':'public selfcheck only',
    'public_metric':'-conflicts, higher is better' if task=='graph' else 'higher is better',
    'parent_scores':parents,'selected_public_winner':s['best_visible']['label'],
    'winner_public_score':best,'iteration_evidence':evidence,
    'warning':'Hypotheses are agent claims, not established causes; ties are not improvements.'},
    ensure_ascii=False,default=str)
 return analysis,config

def commit(root,state,run):
 import fcntl
 task,s=summary_of(run)
 shared_lock=root/'repro/skill/.lock'
 shared_lock.parent.mkdir(parents=True,exist_ok=True)
 with shared_lock.open('a') as shared, (state/'lock').open('a') as lock:
  fcntl.flock(shared,fcntl.LOCK_EX)
  fcntl.flock(lock,fcntl.LOCK_EX)
  manifest=json.loads((state/'manifest.json').read_text())
  if str(run.resolve()) in {x['run'] for x in manifest['runs']}:raise ValueError('Run already committed')
  analysis,config=consolidate(root,task,run,s)
  raw=config.read_text(encoding='utf8');digest=hashlib.sha256(raw.encode()).hexdigest()
  cfg=yaml.safe_load(raw)
  cfg['name']=f'evolved_{task}_{digest[:12]}'
  cfg['description']=f'Public-score best MAS from RSI-Exam {TASKS[task]}; use for this task only'
  cfg['successful_tasks']=[{'q':'RSI-Exam '+TASKS[task],
     'notes':'Best configuration on public cases; do not compare raw metrics across tasks'}]
  raw=yaml.safe_dump(cfg,sort_keys=False,allow_unicode=True)
  out=state/'pool'/f'evolved_{task}_{digest[:12]}.yaml'
  if out.exists() and out.read_text()!=raw:raise RuntimeError('Pool hash collision')
  # Persist only a distinct, validated improvement over the parents and the
  # historical public best under the same task image and instruction.
  parent_texts=[(run/'evaluations'/f'parent_{i:02d}.yaml').read_text(encoding='utf8')
                for i in range(2) if (run/'evaluations'/f'parent_{i:02d}.yaml').is_file()]
  if task=='bbo':
   # The BBO pipeline validates the executable MAS before writing best_config.
   if not isinstance(yaml.safe_load(raw),dict):raise ValueError('Invalid BBO MAS')
  else:
   sys.path.insert(0,str(root/'vendor/agent_engine'))
   sys.path.insert(0,str(root/'repro/runner'))
   from task_runtime import validate_yaml
   # A standalone commit may run without sourcing env; validate against the
   # model pool recorded when this exact run was generated.
   validate_yaml(raw,model_pool=s.get('model_pool'))
  candidates=s.get('candidates',[])
  def score(c):return c.get('visible',{}).get('metric') if task!='bbo' else c.get('visible_score')
  parent_scores=[score(c) for c in candidates if c.get('label') in ('parent_00','parent_01')
                 and isinstance(score(c),(int,float)) and
                 (c.get('valid',True) if task!='bbo' else c.get('contract_valid',True))]
  best=(s['best_visible'].get('visible',{}).get('metric') if task!='bbo'
        else s['best_visible'].get('visible_score'))
  improved=(s['best_visible'].get('label','').startswith('step_') and
            isinstance(best,(int,float)) and bool(parent_scores) and
            best>max(parent_scores))
  sys.path.insert(0,str(root/'repro/runner'))
  from trajectory_evidence import crossover_change
  distinct=all(crossover_change(x,config.read_text(encoding='utf8'))
               for x in parent_texts)
  # A partial QEC selfcheck can improve its mean while two settings still crash.
  # Preserve that observation in memory, but do not promote its YAML to the pool.
  qec_settings=s['best_visible'].get('visible',{}).get('settings',[]) if task=='qec' else []
  complete=(task!='qec' or len(qec_settings)==4 and
            all(not item.get('error') for item in qec_settings))
  memory_path,skills_path,roles_path=knowledge_paths(root,task)
  memory=read_list(memory_path)
  if any(x.get('run')==str(run.resolve()) for x in memory):
   raise ValueError('Run already present in shared knowledge')
  historical=[]
  for record in memory:
   if record.get('task')!=task:continue
   previous=Path(record.get('run') or '')/'summary.json'
   if previous.is_file():
    old=json.loads(previous.read_text())
    if any(old.get(key)!=s.get(key) for key in ('public_image_id','instruction_sha256')):
     continue
   elif any(record.get(key)!=s.get(key) for key in ('public_image_id','instruction_sha256')):
    continue
   value=record.get('new_accuracy')
   if isinstance(value,(int,float)) and not isinstance(value,bool):
    historical.append(float(value))
  historical_best=max(historical,default=None)
  historical_improved=(isinstance(best,(int,float)) and
                       (historical_best is None or best>historical_best+1e-9))
  accepted=bool(improved and distinct and complete and historical_improved)
  # Historical state.pool is read-only for new runs; the shared pool owns the
  # two bounded lineage slots and is promoted after this run commits.
  # All per-version YAMLs remain in run/evaluations.
  analysis_data=json.loads(analysis)
  analysis_data['historical_public_best']=historical_best
  analysis_data['archived_yaml']=False
  analysis_data['shared_pool_candidate']=accepted
  analysis=json.dumps(analysis_data,ensure_ascii=False,default=str)
  first=float(max(parent_scores)) if parent_scores else 0.0
  memory_query=('RSI-Exam Query-Budgeted Noisy Black-Box Optimization bbo_noisy_continuous'
                if task=='bbo' else 'RSI-Exam '+TASKS[task])
  memory.append({'run':str(run.resolve()),'query':memory_query, 'action':'consolidate',
    'config_changes':f'Best MAS {digest[:12]}; {len(operations_of(s))} evolution operations',
    'old_accuracy':first,'new_accuracy':float(best or 0),
    'success':accepted,'task':task,
    'public_image_id':s.get('public_image_id'),
    'instruction_sha256':s.get('instruction_sha256'),
    'analysis':analysis})
  from skill_memory import add_run
  skills=read_list(skills_path)
  roles=read_list(roles_path)
  skills,roles=add_run(skills,roles,task,run)
  skills_path.parent.mkdir(parents=True,exist_ok=True)
  memory_path.parent.mkdir(parents=True,exist_ok=True)
  atom(skills_path,skills)
  atom(roles_path,roles)
  atom(memory_path,memory)
  manifest['runs'].append({'task':task,'run':str(run.resolve()),'config_sha256':digest,
                           'public_winner_sha256':s['best_visible']['sha256'],
                           'hidden_score':s['hidden_score']})
  atom(state/'manifest.json',manifest)
  # Updating the shared two-lineage pool is idempotent and independent of the
  # run's task-local memory transaction. A failed promotion remains retryable.
  import subprocess
  command=[sys.executable,str(root/'repro/pool_manager.py'),'promote',
           '--root',str(root),'--run',str(run)]
  result=subprocess.run(command,text=True,capture_output=True,timeout=30) if os.environ.get('POOL_PROMOTION_MODE','deferred')=='auto' else None
  if result is None:print('SHARED_POOL_PROMOTION_DEFERRED: '+str(run))
  elif result.returncode:print('SHARED_POOL_PROMOTION_FAILED: '+result.stderr[-900:],file=sys.stderr)
  else:print('SHARED_POOL_PROMOTION: '+result.stdout.strip())
  print(json.dumps(manifest['runs'][-1],ensure_ascii=False))

def operations_of(s):return s.get('operations',[])


def experiment_commit(root,state,run):
 """Atomic public-only memory and six-task CSV update across concurrent terminals."""
 import csv,fcntl
 run=run.resolve();summary=json.loads((run/'summary.json').read_text())
 task=summary['task']; seed=summary['seed']
 if summary['hidden_status']!='completed':raise ValueError('Official sealed result required')
 lock=state/'lock';state.mkdir(parents=True,exist_ok=True)
 with lock.open('a') as guard:
  fcntl.flock(guard,fcntl.LOCK_EX)
  manifest_path=state/'manifest.json'
  manifest=json.loads(manifest_path.read_text()) if manifest_path.is_file() else {'schema':'ma-rsi-six-tasks','runs':[]}
  if any(x['run']==str(run) for x in manifest['runs']):return
  # Save a bounded, revisable public evidence record. No sealed labels enter model prompts.
  memory,skills_path,roles=knowledge_paths(root,task)
  memory.parent.mkdir(parents=True,exist_ok=True);roles.parent.mkdir(parents=True,exist_ok=True)
  previous=read_list(memory)
  previous.append({'task':task,'run':str(run),'seed':seed,'public_metric':summary['best_visible']['visible']['metric'],
    'public_image_id':summary.get('public_image_id'),'winner_sha256':summary['best_visible']['sha256'],
    'evidence':summary['operations'],'status':'provisional_public'})
  atom(memory,previous)
  skills,notes=add_run(read_list(skills_path),read_list(roles),task,run)
  atom(skills_path,skills);atom(roles,notes)
  initial=[x['visible']['metric'] for x in summary['candidates'] if x['label'].startswith('parent_') and x['valid']]
  winner=summary['best_visible'];current=winner['visible']['metric']
  review_file=run/'final_generalization_review.json'
  review=json.loads(review_file.read_text()) if review_file.is_file() else {}
  review_ok=(summary.get('status')=='completed' and summary.get('completed_versions')==6
             and review.get('selected_solver_sha256')==winner['sha256']
             and not review.get('risk_flags') and not review.get('uncertainty'))
  if review_ok and winner['label'].startswith('attempt_') and initial and current>max(initial)+1e-9:
   source=run/'evaluations'/(winner['label']+'.yaml')
   dest=root/'repro/pool'/task;dest.mkdir(parents=True,exist_ok=True)
   target=dest/('evolved_'+task+'_'+hashlib.sha256(source.read_bytes()).hexdigest()[:12]+'.yaml')
   if not target.exists():shutil.copyfile(source,target)
   index_path=dest/'index.json'
   index=json.loads(index_path.read_text()) if index_path.is_file() else {}
   index[target.name]={'source_run':str(run),'label':winner['label'],
     'solver_sha256':winner['sha256'],'yaml_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
     'public_metric':current,'public_image_id':summary.get('public_image_id'),
     'review':str(review_file),'status':'public_provisional'}
   atom(index_path,index)
  manifest['runs'].append({'task':task,'seed':seed,'run':str(run),'hidden_score':summary['hidden_score']})
  atom(manifest_path,manifest)
  report=state/'results.csv';tmp=report.with_suffix('.csv.tmp')
  fields=['task','seed','official_score','official_metric','research_seconds','hidden_seconds','total_seconds','model_tokens_json','run']
  runs=[]
  for row in manifest['runs']:
   data=json.loads((Path(row['run'])/'summary.json').read_text())
   if data['hidden_status']!='completed':continue
   usage={}
   for file in (Path(row['run'])/'evaluations').glob('*/agent_*.json'):
    if file.name=='agent_failures.json':continue
    meta=json.loads(file.read_text()).get('metadata') or {}
    name=meta.get('model_id')
    if name:
     u=usage.setdefault(name,{'input':0,'output':0,'total':0})
     for key,field in (('input_tokens','input'),('output_tokens','output'),('total_tokens','total')):
      u[field]+=int(meta.get(key) or 0)
   for u in usage.values():
    if not u['total']:u['total']=u['input']+u['output']
   final=data.get('final_result') or {}
   runs.append({'task':row['task'],'seed':row['seed'],'official_score':data['hidden_score'],
    'official_metric':final.get('official_metric'),'research_seconds':data['research_seconds'],
    'hidden_seconds':data.get('hidden_seconds',0),'total_seconds':data['research_seconds']+data.get('hidden_seconds',0),
    'model_tokens_json':json.dumps(usage,ensure_ascii=False,sort_keys=True),'run':row['run']})
  with tmp.open('w',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
   for r in sorted(runs,key=lambda x:(x['task'],str(x['seed']))):writer.writerow(r)
   for group in sorted(set(r['task'] for r in runs)):
    items=[r for r in runs if r['task']==group]
    writer.writerow({'task':group,'seed':'MEAN_'+str(len(items)),'official_score':sum(r['official_score'] for r in items)/len(items),
     'official_metric':'','research_seconds':sum(r['research_seconds'] for r in items)/len(items),
     'hidden_seconds':sum(r['hidden_seconds'] for r in items)/len(items),
     'total_seconds':sum(r['total_seconds'] for r in items)/len(items),'model_tokens_json':'','run':''})
  os.replace(tmp,report)
  print(json.dumps({'task':task,'seed':seed,'results':str(report)},ensure_ascii=False))

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 for cmd in ('init','export','commit','merge','replay','reconcile','rebuild-knowledge','ensure-shared','experiment-commit'):
  sp=sub.add_parser(cmd);sp.add_argument('--root',type=Path,required=True);sp.add_argument('--state',type=Path,required=True)
  if cmd=='export':sp.add_argument('--task',choices=TASKS,required=True);sp.add_argument('--dest',type=Path,required=True);sp.add_argument('--pool-policy',choices=('seeds','task_top2'),default='seeds');sp.add_argument('--shared-parent-pool',type=Path)
  if cmd in ('commit','reconcile','experiment-commit'):sp.add_argument('--run',type=Path,required=True)
  if cmd=='replay':sp.add_argument('--source-state',type=Path,required=True)
  if cmd=='merge':
   sp.add_argument('--mode',choices=('original',),required=True)
   sp.add_argument('--seed',type=int,required=True)
 a=p.parse_args();root=a.root.resolve();state=a.state.resolve()
 if a.command=='init':initialize(root,state)
 elif a.command=='ensure-shared':ensure_shared(root)
 elif a.command=='export':export(root,state,a.task,a.dest,a.pool_policy,a.shared_parent_pool)
 elif a.command=='commit':commit(root,state,a.run.resolve())
 elif a.command=='experiment-commit':experiment_commit(root,state,a.run)
 elif a.command=='rebuild-knowledge':
  import fcntl
  shared_lock=root/'repro/skill/.lock';shared_lock.parent.mkdir(parents=True,exist_ok=True)
  with shared_lock.open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   skills=[];counts={}
   for task in TASKS:
    memory_path,_,roles_path=knowledge_paths(root,task)
    roles=[]
    for item in read_list(memory_path):
     run=Path(item.get('run') or '')
     if run.is_dir():skills,roles=add_run(skills,roles,task,run)
    roles_path.parent.mkdir(parents=True,exist_ok=True)
    atom(roles_path,roles);counts[task]=len(read_list(memory_path))
   atom(root/'repro/skill/skills.json',skills)
   print(json.dumps({'runs_by_task':counts,'skills':len(skills)},ensure_ascii=False))
 elif a.command=='reconcile':
  import fcntl
  run=a.run.resolve();task,s=summary_of(run)
  with (state/'lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   manifest=json.loads((state/'manifest.json').read_text())
   hits=[item for item in manifest['runs'] if item['run']==str(run)]
   if len(hits)!=1 or hits[0]['task']!=task:
    raise ValueError('Run must already be committed exactly once to this state')
   hits[0]['hidden_score']=s['hidden_score']
   hits[0]['hidden_status']='completed'
   atom(state/'manifest.json',manifest)
   print(json.dumps({'task':task,'run':str(run),'hidden_score':s['hidden_score']}))
 elif a.command=='replay':
  source=a.source_state.resolve()
  if source==state:raise ValueError('Replay source and destination must be distinct')
  runs=json.loads((source/'manifest.json').read_text())['runs']
  for item in runs:
   run=Path(item['run']).resolve()
   if not (run/'summary.json').is_file():raise FileNotFoundError(run)
   current=json.loads((state/'manifest.json').read_text())['runs']
   if str(run) not in {x['run'] for x in current}:commit(root,state,run)
 else:
  ready=[]
  for task in TASKS:
   matches=[path for path in (root/'repro/runner/outputs').glob(
       f'{task}_{a.mode}_seed{a.seed}_*') if path.is_dir() and (path/'summary.json').is_file()]
   if len(matches)!=1:raise ValueError(f'{task}: expected one completed run, found {len(matches)}')
   run=matches[0].resolve()
   summary_of(run)
   ready.append(run)
  for run in ready:
   manifest=json.loads((state/'manifest.json').read_text())
   if str(run) not in {item['run'] for item in manifest['runs']}:
    commit(root,state,run)
if __name__=='__main__':main()
