"""Research-only, task-scoped tools. Never imported by a submitted solver."""
from __future__ import annotations
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from smolagents import Tool

CATALOG = {
 'research_read_context': 'Read public instruction.md or your own private candidate.py.',
 'research_read_public': 'Read bounded schema/counts from this task public data only.',
 'research_read_feedback': 'Read the previous candidate public score and error from this run.',
 'research_read_trace': 'Read bounded public per-setting or per-graph diagnostics from this run.',
 'research_read_history': 'Read current-run candidate feedback by exact label.',
 'research_write_candidate': 'Write complete source to your own candidate.py.',
 'research_preflight': 'Check your own source against the task entrypoint and import allow-list.',
 'research_probe_candidate': 'Probe your own source with a bounded public-image contract check.'}
CATALOG_TEXT = 'Task generation tools available to each agent:\n' + '\n'.join(
 f'- {k}: {v}' for k,v in CATALOG.items())
LOCK=threading.Lock()
PUBLIC_PROBES={}  # One bounded budget per candidate MAS execution root.

def audit(root,agent,tool,ok):
 with LOCK:
  with (root/'tool_audit.jsonl').open('a',encoding='utf8') as f:
   f.write(json.dumps({'agent':agent,'tool':tool,'success':bool(ok)})+'\n')

class Base(Tool):
 output_type='string'
 def __init__(self,root,agent,task_root,task):
  if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',agent):raise ValueError('unsafe agent ID')
  self.root=Path(root).resolve();self.agent=agent;self.task_root=Path(task_root).resolve();self.task=task
  self.scratch=self.root/'tool_workspace'/agent
  self.scratch.mkdir(parents=True,exist_ok=True)
  super().__init__()
 def record(self,ok): audit(self.root,self.agent,self.name,ok)

class ReadContext(Base):
 name='research_read_context';description=CATALOG[name]
 inputs={'item':{'type':'string','description':'Exactly instruction.md or candidate.py'}}
 def forward(self,item:str)->str:
  path={'instruction.md':self.root/'tool_workspace/instruction.md',
        'candidate.py':self.scratch/'candidate.py'}.get(item)
  ok=path is not None and path.is_file() and not path.is_symlink();self.record(ok)
  return path.read_text(encoding='utf8')[:24000] if ok else 'DENIED: item unavailable'

class ReadPublic(Base):
 name='research_read_public';description=CATALOG[name]
 inputs={'item':{'type':'string','description':'Exactly summary'}}
 def forward(self,item:str)->str:
  if item!='summary':self.record(False);return 'DENIED: choose summary'
  if self.task=='qec':
   base=self.task_root/'environment/settings_visible'
   result={'settings':{p.parent.name:{k:v for k,v in json.loads(p.read_text()).items()
                         if k in ('distance','rounds','n_detectors','n_observables','n_dev','n_eval','decode_budget_sec')}
                       for p in sorted(base.glob('*/meta.json'))}}
  elif self.task in ('bbo','inventory','graph'):
   data=json.loads((self.task_root/'environment/data/visible.json').read_text())
   cases=data.get('instances',[])
   result={'n_instances':len(cases),'fields':sorted(cases[0]) if cases else [],
           'budget':data.get('budget')}
  elif self.task=='cxr':
   base=self.task_root/'environment/data'
   result={'public_grade_rows':sum(1 for _ in (base/'grade.jsonl').open()),
           'calibration_rows':sum(1 for _ in (base/'calibration.jsonl').open())}
  elif self.task=='game2048':
   result=json.loads((self.task_root/'environment/visible_seeds.json').read_text())
  elif self.task=='dcf':
   import csv
   base=self.task_root/'environment/data'
   result={'public_calibration_rows':sum(1 for _ in csv.DictReader((base/'calibration_truth.csv').open())),
           'public_fundamental_rows':sum(1 for _ in csv.DictReader((base/'fundamentals.csv').open()))}
  else:
   self.record(False);return 'DENIED: unsupported public task'
  self.record(True);return json.dumps(result,ensure_ascii=False)[:10000]

class ReadHistory(Base):
 name='research_read_history';description=CATALOG[name]
 inputs={'item':{'type':'string','description':'Exactly index or a label returned by index'}}
 def forward(self,item:str)->str:
  path=self.root/'tool_workspace/history.json'
  entries=json.loads(path.read_text()) if path.is_file() else []
  if item=='index':self.record(True);return json.dumps([{k:v for k,v in e.items() if k!='source'} for e in entries])
  found=next((x for x in entries if x['label']==item),None)
  self.record(found is not None)
  return json.dumps(found,ensure_ascii=False)[:24000] if found else 'DENIED: unknown current-session label'

class ReadFeedback(Base):
 name='research_read_feedback';description=CATALOG[name]
 inputs={'item':{'type':'string','description':'Exactly summary'}}
 def forward(self,item:str)->str:
  path=self.root/'tool_workspace/history.json'
  if item!='summary':self.record(False);return 'DENIED: choose summary'
  rows=json.loads(path.read_text()) if path.is_file() else []
  last=rows[-1] if rows else None;self.record(True)
  return json.dumps({k:v for k,v in last.items() if k!='source'},ensure_ascii=False)[:5000] if last else 'NO_PRIOR_VERSION'

class ReadTrace(Base):
 name='research_read_trace';description=CATALOG[name]
 inputs={'item':{'type':'string','description':'Exactly checkpoints'}}
 def forward(self,item:str)->str:
  path=self.root/'tool_workspace/history.json'
  if item!='checkpoints':self.record(False);return 'DENIED: choose checkpoints'
  rows=json.loads(path.read_text()) if path.is_file() else []
  last=rows[-1] if rows else None;self.record(True)
  return json.dumps({k:v for k,v in last.items() if k in ('label','metric','valid','error','settings','effective_conflicts','per_graph_tail')},ensure_ascii=False)[:5000] if last else 'NO_PRIOR_VERSION'

class WriteCandidate(Base):
 name='research_write_candidate';description=CATALOG[name]
 inputs={'source':{'type':'string','description':'Complete candidate.py Python source, up to 100000 characters'}}
 def forward(self,source:str)->str:
  if not isinstance(source,str) or not source.strip() or len(source)>100000:
   self.record(False);return 'DENIED: source is empty or too long'
  path=self.scratch/'candidate.py'
  if path.is_symlink():self.record(False);return 'DENIED: symbolic link'
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
  with os.fdopen(fd,'w',encoding='utf8') as f:f.write(source)
  self.record(True);return 'WROTE: your candidate.py'

class Preflight(Base):
 name='research_preflight';description=CATALOG[name]
 inputs={'item':{'type':'string','description':'Exactly candidate.py after writing it'}}
 def forward(self,item:str)->str:
  path=self.scratch/'candidate.py'
  if item!='candidate.py' or path.is_symlink() or not path.is_file():
   self.record(False);return 'DENIED: write your candidate.py first'
  try:
   from task_visible import static_gate
   static_gate(self.task,path.read_text(encoding='utf8'))
   self.record(True);return 'PASS: syntax, task entrypoint and allowed imports; official runtime selfcheck still required'
  except Exception as exc:
   self.record(False);return f'FAIL: {type(exc).__name__}: {exc}'

class ProbeCandidate(Base):
 name='research_probe_candidate';description=CATALOG[name]
 inputs={'item':{'type':'string','description':'Exactly candidate.py, after writing it'}}
 def forward(self,item:str)->str:
  path=self.scratch/'candidate.py'
  if item!='candidate.py' or path.is_symlink() or not path.is_file():
   self.record(False);return 'DENIED: write your own candidate.py first'
  with LOCK:
   used=PUBLIC_PROBES.get(str(self.root),0)
   if used<4:PUBLIC_PROBES[str(self.root)]=used+1
  if used>=4:self.record(False);return 'DENIED: probe limit (4 per candidate MAS)'
  from task_visible import static_gate
  try:static_gate(self.task,path.read_text(encoding='utf8'))
  except Exception as exc:
   self.record(False);return f'FAIL: {type(exc).__name__}: {exc}'
  # The public, isolated image parses the actual candidate. No task data is
  # mounted from the host and there is no hidden grader access.
  images={'bbo':'bbo_noisy_continuous','inventory':'bbo_simopt_inventory','graph':'johnson1991_leighton_graph_coloring',
          'qec':'qec_decoder_arena','cxr':'cxr_ood_triage_policy',
          'game2048':'game2048_policy_search','dcf':'finscope_dcf_valuation'}
  source=path.read_text(encoding='utf8')
  cmd=['docker','run','--rm','-i','--network','none','--read-only',
       '--security-opt','no-new-privileges','--tmpfs','/tmp:rw,nosuid,size=256m',
       '--cpus','2','--memory','4g','--pids-limit','128',
       '-e','PYTHONDONTWRITEBYTECODE=1','rsi-subset-'+images[self.task]+':public',
       'python','-c','import ast,sys; ast.parse(sys.stdin.read()); print("PASS: public-image parse")']
  try:
   result=subprocess.run(cmd,input=source,text=True,capture_output=True,timeout=35)
   self.record(result.returncode==0)
   return ('PUBLIC_IMAGE_EXIT='+str(result.returncode)+'\n'+result.stdout+result.stderr)[-1200:]
  except (OSError,subprocess.TimeoutExpired) as exc:
   self.record(False);return f'PUBLIC_IMAGE_ERROR: {type(exc).__name__}: {exc}'


FACTORIES={x.name:x for x in (ReadContext,ReadPublic,ReadFeedback,ReadTrace,ReadHistory,WriteCandidate,Preflight,ProbeCandidate)}
def make_tool(name,root,agent,task_root,task):
 return FACTORIES[name](root,agent,task_root,task)
