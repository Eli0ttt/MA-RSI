#!/usr/bin/env python3
"""One sealed Harbor submission for a completed public run, with exact source SHA."""
from __future__ import annotations
import argparse,hashlib,json,os,subprocess,time
from pathlib import Path

TASKS={'bbo':'bbo_noisy_continuous','inventory':'bbo_simopt_inventory',
       'graph':'johnson1991_leighton_graph_coloring','qec':'qec_decoder_arena',
       'cxr':'cxr_ood_triage_policy','game2048':'game2048_policy_search','dcf':'finscope_dcf_valuation'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
 p.add_argument('--run',type=Path,required=True);p.add_argument('--task',choices=TASKS,required=True)
 p.add_argument('--retry-infrastructure',action='store_true',
                help='Retry the same public winner only after a zero-evaluation trusted grader failure')
 p.add_argument('--harbor-bin',type=Path,default=Path.home()/'.local/share/uv/tools/harbor/bin/harbor')
 a=p.parse_args();root=a.root.resolve();run=a.run.resolve();summary=run/'summary.json'
 s=json.loads(summary.read_text());winner=run/'best_visible_solver.py'
 if a.retry_infrastructure:
  old=Path(s.get('final_result',{}).get('diagnostic_path') or '')
  if not old.is_file():raise ValueError('No earlier grader diagnostic')
  detail=json.loads(old.read_text())
  if not (detail.get('correctness') is False and detail.get('num_evals')==0 and
          any('grader failed:' in str(error) for error in detail.get('errors') or [])):
   raise ValueError('Prior attempt was not a zero-evaluation grader failure')
  s.setdefault('hidden_attempts',[]).append({
      'job':s.get('hidden_job'),'status':s.get('hidden_status'),
      'score':s.get('hidden_score'),'error':detail.get('errors')})
 elif s.get('hidden_status')!='not_run' or s.get('hidden_job'):
  raise ValueError('Hidden already attempted; inspect prior job')
 expected=s.get('max_versions')
 count=s.get('completed_versions',0)
 complete=s.get('status')=='completed' and count==expected==6
 if (not isinstance(expected,int) or expected<1 or s.get('task')!=a.task or
     not complete or len(s.get('candidates',[]))!=count or
     s.get('final_public_review')!='final_generalization_review.json'):
  raise ValueError('Incomplete or mismatched research run')
 review=json.loads((run/s['final_public_review']).read_text())
 if review.get('candidate_count')!=6 or review.get('selected_solver_sha256')!=s['best_visible']['sha256']:
  raise ValueError('Final public generalization review missing or changed')
 seen=set()
 for candidate in s['candidates']:
  label=candidate.get('label')
  source=run/'evaluations'/str(label)/'candidate_solver.py'
  config=run/'evaluations'/(str(label)+'.yaml')
  check=run/'evaluations'/str(label)/'visible.json'
  digest=candidate.get('sha256')
  if (not candidate.get('valid') or not source.is_file() or not config.is_file() or not check.is_file() or
      hashlib.sha256(source.read_bytes()).hexdigest()!=digest or digest in seen):
   raise ValueError('Six distinct, complete, selfchecked solver versions are required')
  public=json.loads(check.read_text())
  if public!=candidate.get('visible') or public.get('valid') is not True:
   raise ValueError('Official public selfcheck evidence differs from candidate manifest')
  seen.add(digest)
 if {x.get('solver_sha256') for x in review.get('candidates',[])}!=seen:
  raise ValueError('Final review does not cover exactly the six solver versions')
 selected_label=s['best_visible']['label']
 if (selected_label not in {c['label'] for c in s['candidates']} or
     review.get('selected_label')!=selected_label):
  raise ValueError('Final review selected a different solver version')
 selected_config=run/'evaluations'/(selected_label+'.yaml')
 if (not selected_config.is_file() or
     hashlib.sha256(selected_config.read_text().encode()).hexdigest()!=review.get('selected_yaml_sha256')):
  raise ValueError('Frozen selected configuration differs from final public review')
 task_root=root/'rsi_exam_tasks'/TASKS[a.task]
 if not (task_root/'task.toml').is_file() or not a.harbor_bin.is_file():raise FileNotFoundError('Task or Harbor unavailable')
 digest=hashlib.sha256(winner.read_bytes()).hexdigest()
 if digest!=s['best_visible']['sha256']:raise ValueError('Selected source SHA mismatch')
 if (not s['best_visible'].get('valid') or
    not any(c['sha256']==digest and c['valid'] for c in s['candidates'])):
  raise ValueError('Winner not in valid public candidate manifest')
 artifact={'game2048':'policy.py','dcf':'forecaster.py'}.get(a.task,'solver.py')
 if a.task=='game2048' and len(winner.read_bytes())>10000000:raise ValueError('Policy exceeds official cap')
 name=f'research-{a.task}-{digest[:12]}-{time.time_ns()}'
 jobs=run/'harbor_jobs';jobs.mkdir(exist_ok=True)
 job=jobs/name
 mounts=json.dumps([{'type':'bind','source':str(winner),'target':'/opt/research-selected/'+artifact,
                     'read_only':True}])
 cmd=[str(a.harbor_bin),'run','--yes','--env','docker','--job-name',name,
      '--jobs-dir',str(jobs),'--n-concurrent','1','--n-attempts','1','--max-retries','0',
      '--path',str(task_root),'--agent','submission.agent:ResearchSubmissionAgent',
      '--model',os.environ['META_MODEL'],'--mounts',mounts,
      '--agent-kwarg','solver_sha256='+digest,'--agent-kwarg','artifact_name='+artifact]
 env=os.environ.copy();env['PYTHONPATH']=str(root)+os.pathsep+env.get('PYTHONPATH','')
 s['hidden_status']='running';s['hidden_job']=str(job);summary.write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n')
 log=run/(name+'.hidden.log');start=time.monotonic()
 with log.open('w') as stream:r=subprocess.run(cmd,env=env,stdout=stream,stderr=subprocess.STDOUT)
 s['hidden_seconds']=time.monotonic()-start;s['hidden_log']=str(log)
 if r.returncode:
  s['hidden_status']='failed';s['hidden_return_code']=r.returncode
  summary.write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n')
  raise RuntimeError('Harbor failed; inspect '+str(log))
 files=list(job.glob('*/result.json'))
 if len(files)!=1:raise RuntimeError('Harbor result count is not one; inspect '+str(job))
 result=json.loads(files[0].read_text());value=((result.get('verifier_result') or {}).get('rewards') or {}).get('reward')
 if not isinstance(value,(int,float)):raise RuntimeError('No numeric official reward; inspect '+str(files[0]))
 # The official metric and the platform's mapped reward are distinct for some tasks.
 metric_names={'bbo':('oracle_normalized_auc70_final30','higher'),
               'inventory':('mean_log_cost_progress_auc70_final30','higher'),
               'graph':('total_conflicts','lower'),
               'qec':('mean_setting_score','higher'),
               'cxr':('triage_utility_pct','higher'),
               'game2048':('mean_score','higher'),
               'dcf':('median_absolute_valuation_error','lower')}
 label,direction=metric_names[a.task]
 metric=None
 detail=None
 diagnostic=files[0].parent/'verifier'/('score_details.json' if a.task=='qec' else 'grade_debug.json')
 if diagnostic.is_file():
  try:
   detail=json.loads(diagnostic.read_text())
   metric=detail.get('metric') if a.task in ('qec','graph') else detail.get('reward')
  except (OSError,ValueError):pass
 # Harbor can report a numeric zero when the trusted grader itself failed
 # before running the submitted solver. This is not an experimental score.
 if isinstance(detail,dict):
  grader_errors=detail.get('errors') or []
  if (detail.get('correctness') is False and detail.get('num_evals')==0
      and any('grader failed:' in str(error) for error in grader_errors)):
   s['hidden_status']='infrastructure_error'
   s['hidden_score']=None
   s['hidden_error']='; '.join(map(str,grader_errors))[:1500]
   summary.write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n')
   raise RuntimeError('Trusted grader failed before evaluation: '+s['hidden_error'])
 s['hidden_status']='completed';s['hidden_score']=float(value)
 s['final_result']={'official_metric_name':label,'official_metric_direction':direction,
                    'official_metric':metric,'platform_reward':float(value),
                    'diagnostic_path':str(diagnostic) if diagnostic.is_file() else None}
 summary.write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps({'task':a.task,'final_result':s['final_result'],
                   'job':str(job),'source_sha256':digest}))
if __name__=='__main__':main()
