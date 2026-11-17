"""Task-aware adapter at EvoMAS's native DAG/SmolagentsRunner boundary."""
from __future__ import annotations
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
import yaml
from task_visible import static_gate, ENTRY

MODELS={'openai:'+name.strip() for name in __import__('os').environ['MODEL_POOL'].split(',')}
TOOLS={'research_read_context','research_read_public','research_read_feedback',
       'research_read_trace','research_read_history','research_write_candidate',
       'research_preflight','research_probe_candidate'}

def validate_yaml(source, model_pool=None):
 from src.mas.spec import MasSpec
 models=MODELS if model_pool is None else set(model_pool)
 spec=MasSpec(**yaml.safe_load(source))
 if spec.backend!='smolagents':raise ValueError('Requires native smolagents backend')
 agents=spec.agents
 if not agents:raise ValueError('Empty agents')
 levels=spec.get_execution_levels()
 if sorted(a for level in levels for a in level)!=sorted(agents):raise ValueError('Cyclic/missing agent')
 graph=spec.topology.reports_to
 if any(a not in agents or any(t not in agents for t in ts) for a,ts in graph.items()):
  raise ValueError('Unknown agent in DAG')
 terminal=[a for a in agents if not graph.get(a)]
 if len(terminal)!=1 or levels[-1]!=terminal:raise ValueError('Need exactly one terminal agent')
 for aid,a in agents.items():
  if a.id!=aid or a.agent_type!='CodeAgent' or a.model_id not in models:
   raise ValueError(f'Invalid role/model for {aid}')
  if set(a.tools)!=TOOLS or len(a.tools)!=len(TOOLS):raise ValueError(f'Expected all eight research tools for {aid}')
  if not a.prompt.strip():raise ValueError(f'Empty prompt for {aid}')
 return spec,terminal[0]

def extract(source,task):
 blocks=re.findall(r'```(?:python|py)?\s*\n(.*?)```',source,re.I|re.S)
 for part in sorted(blocks,key=len,reverse=True)+[source]:
  try:static_gate(task,part.strip())
  except (SyntaxError,ValueError):
   try:tree=ast.parse(part)
   except SyntaxError:continue
   embedded=[n.value.value for n in tree.body if isinstance(n,ast.Assign)
             and len(n.targets)==1 and isinstance(n.targets[0],ast.Name)
             and n.targets[0].id.startswith('solver_code')
             and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)]
   valid=[]
   for candidate in embedded:
    try:static_gate(task,candidate);valid.append(candidate)
    except (SyntaxError,ValueError):pass
   if len(valid)==1:return valid[0].strip()+'\n'
   continue
  return part.strip()+'\n'
 raise ValueError('No complete candidate for this task in terminal report')

def role_hint(agent,task):
 output={'bbo':'Implement the official 10D 120-query Optimizer in solver.py. Use only public visible evaluations and honor the official budget and interface.', 'inventory':'NumPy 2.2.6 has no np.erf or np.math; math.erf accepts only a scalar, not a NumPy array. For a vector of expected-improvement z values use a validated vectorized approximation or an explicit scalar loop. Check ask(n) with an array input in the public selfcheck. Run preflight and public selfcheck. Treat space-filling probes and a regime-diverse initial policy portfolio as hypotheses; compare independent progress across the four public regimes; target robust per-case improvements within 48 queries. Optimizer with __init__(dim, lower, upper, budget, seed, rng), ask(n), tell(X,y,metadata=None), 4D [0,1]^4, 48 noisy costs; stdlib + NumPy 2.2.6 only, self-contained solver.py.',
         'graph':'Consider incremental neighbor-color counts, adaptive tabu/ILS and robust restarts; verify them on the public graphs. Always return n_vertices color entries for EVERY graph; a fallback must be a valid coloring even when a search/helper times out or returns an empty list. Use monotonic per-instance timing; finish each graph in at most 35s including setup and formatting to avoid the 45s kill. solve(data) returning {"colorings": [[int,...], ...]}; fixed target_k, fewer monochromatic edges, each graph <45 seconds; stdlib + NumPy, optional official gc_lib.',
         'qec':'Investigate correlation-aware decoding or color-pair schemes using labeled development data as testable hypotheses, without memorizing public labels. Check all four visible settings, including d7 and d9; preserve shape and per-setting budget. A prior candidate crashed on Stim DemInstruction.args_copy used as a list; it is a method, so call args_copy() and test the actual result. Treat a failure in any one setting as a failed candidate to repair. decode(setting,dets) returning a boolean [n_shots,n_observables] array; per-setting decode limit 600/900s; stdlib + NumPy + SciPy + Stim + PyMatching only; dev labels allowed, eval truth withheld.',
         'cxr':'Implement triage(cases, resources, seed) in solver.py using standard library only. Return exactly one {study_uid,risk} per case, with finite risk in [0,1]. Compare sensitivity and calibration at both sites under a shared review budget.',
         'game2048':'Implement deterministic choose_move(board) in policy.py with standard library only. Return a legal direction within 5 seconds per move; track game-level mean score and CPU cost. No filesystem, clocks, environment, subprocesses or randomness in the submitted policy.',
         'dcf':'Implement solve(problem) in forecaster.py. Return valid driver records for exactly problem.holdout_ids, with terminal_growth < wacc. Evaluate leave-one-out median valuation error using the official public calibration; stay within 512 MiB.'}[task]
 artifact=ENTRY.get(task,'solver.py')
 prompt=agent.prompt if agent.prompt not in ('worker','smoa_aggregator','aggregator','judge','team_worker','processor','debater_initial','debater_round','reviewer_create','reviewer_review','reviewer_revise') else ''
 role=getattr(agent,'role','worker')
 if role=='judge':
  duty='Compare upstream proposals using public evidence and the official task contract; report one choice, defects, and a concrete repair plan. '
 elif role=='aggregator':
  duty=f'Synthesize upstream reports into one complete {artifact} for the current task; verify the interface before finishing. '
 else:
  duty=f'Propose a distinct, implementable {artifact} for the current task; state which public result would test your idea. '
 return ('This is the RSI-Exam '+task+' task. '+duty
         +output+' Preserve official interface and generalize to sealed cases. '
         'Research tools are available only when assigned in your YAML. '
         'research_read_context(item="instruction.md" or "candidate.py"); '
         'research_read_public(item="summary"); research_read_history(item="index" or label). '
         + ('Write the complete code with research_write_candidate(source=complete_code), '
            'then call research_preflight(item="candidate.py"); if it fails, repair the '
            'source and repeat before finishing. ' if
            {'research_write_candidate','research_preflight'}.issubset(set(agent.tools)) else
            'Return a complete solver proposal in your report; candidate writing is '
            'not assigned to this role; return a complete proposal in your report. ')
         + ('After writing a candidate, call '
            'research_probe_candidate(item="candidate.py") for a bounded public contract probe. '
            if 'research_probe_candidate' in agent.tools else
            'research_probe_candidate is not assigned to this role; do not call it. ')
         + 'The CodeAgent host interpreter is for small pure computations; do not import sys, os, importlib, or traceback and do not run filesystem operations there. Use the assigned research tools for file access. Do not run repeated full selfchecks inside an agent: the research evaluator runs the complete public selfcheck after the candidate is written. '
         'After a prior candidate exists in THIS task run, use research_read_history(item="index" or label) '
         'to inspect its public result and source, then make a specific solver improvement; '
         'do not use a solver from a previous task or previous run. '
         + ('Evolved prompt guidance: '+prompt if prompt else ''))

def execute(config_path,task_root,task,output_dir,history,dry_run=False,
            role_notes=None,shared_skills=''):
 from src.agents.spec import AgentResult
 from src.mas import runtime as runtime_mod
 if not dry_run:
  from src.agents.runners.smolagents import SmolagentsRunner
 if not dry_run:
  from src.tools.subset_generation import CATALOG
 else: CATALOG={}
 spec,terminal=validate_yaml(config_path.read_text(encoding='utf8'))
 output_dir.mkdir(parents=True,exist_ok=True)
 workspace=output_dir/'tool_workspace';workspace.mkdir(exist_ok=True)
 artifact=ENTRY.get(task,'solver.py')
 starter=(task_root/'environment/methods/main'/artifact).read_text(encoding='utf8')
 instruction=(task_root/'instruction.md').read_text(encoding='utf8')
 (workspace/'instruction.md').write_text(instruction)
 (workspace/'history.json').write_text(json.dumps(history,ensure_ascii=False))
 for aid in spec.agents:
  p=workspace/aid;p.mkdir(exist_ok=True)
  (p/'candidate.py').write_text(starter)
 # One official starter per new task/session; previous-run solver never appears here.
 prompt=(f'Official RSI-Exam {task} research instruction:\n{instruction}\n'
         f'The starter below is the current task baseline for {artifact}, not a preceding task output.\n'
         f'```python\n{starter}\n```\n'
         'All earlier candidate results listed next are from THIS session only:\n'
         +json.dumps([{k:v for k,v in h.items() if k!='source'} for h in history],ensure_ascii=False)[-16000:]
         +'\nShared conditional research procedures (public only):\n'+shared_skills[:2500])
 failures=[]
 class RoleRunner:
  def __init__(self):
   self.delegate=SmolagentsRunner() if not dry_run else None
   if self.delegate:
    self.delegate.subset_tool_root=output_dir
    self.delegate.subset_task_root=task_root
    self.delegate.subset_task_name=task
  def run(self,agent,text,context=None):
   if dry_run:
    content=('```python\n'+starter+'\n```') if agent.id==terminal else f'Mock report by {agent.id}'
    result=AgentResult(agent_id=agent.id,content=content,success=True)
   else:
    from skill_memory import role_context
    note=role_context(role_notes or [],agent.id)
    result=self.delegate.run(agent,role_hint(agent,task)+'\n'
                             +'Task-local notes for this agent (unverified):\n'+note+'\n'+text,context)
   (output_dir/f'agent_{agent.id}.json').write_text(json.dumps({
    'content':result.content,'success':result.success,'error':result.error,
    'metadata':result.metadata},ensure_ascii=False,default=str))
   if not result.success:failures.append({'agent':agent.id,'error':result.error})
   return result
 old=runtime_mod._get_runner_class
 runtime_mod._get_runner_class=lambda backend:RoleRunner if backend=='smolagents' else old(backend)
 try:final,metadata=runtime_mod.MasRuntime(spec).run(prompt)
 finally:runtime_mod._get_runner_class=old
 (output_dir/'aggregator_response.txt').write_text(final)
 if failures:
  (output_dir/'agent_failures.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2))
 if any(x['agent']==terminal for x in failures):
  raise RuntimeError(f'Terminal agent failed: {str(failures)[:700]}')
 try:source=extract(final,task);origin='terminal_report'
 except ValueError:
  # Accept only terminal's own file after an audited successful write.
  audit=output_dir/'tool_audit.jsonl'
  rows=[json.loads(x) for x in audit.read_text().splitlines()] if audit.is_file() else []
  if not any(x.get('agent')==terminal and x.get('tool')=='research_write_candidate'
             and x.get('success') for x in rows):raise
  source=extract((workspace/terminal/'candidate.py').read_text(),task)
  origin='terminal_writer'
 target=output_dir/'candidate_solver.py'
 target.write_text(source,encoding='utf8')
 result={'metadata':metadata,'candidate_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
         'source':origin,'terminal':terminal,
         'agent_failures':failures,
         'configured_tools':{k:list(v.tools) for k,v in spec.agents.items()}}
 (output_dir/'mas_summary.json').write_text(json.dumps(result,indent=2,default=str))
 return result
