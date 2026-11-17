#!/usr/bin/env python3
"""Two task-specific lineage parents; public scores only, with locked writes.

Crossover inherits the selected primary parent lineage only when its public score
beats both parents; it may then replace that lineage slot.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone
import yaml

TASKS=('bbo','inventory','graph','qec')
LINEAGES=('majority_vote','smoa')
ALL_SLOTS=LINEAGES+('mixed',)

def atomic(path, data):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(data,encoding='utf8')
    os.replace(tmp,path)

def init(root,pool):
    sys.path.insert(0,str(root/'repro/runner'))
    from state import adapt
    pool.mkdir(parents=True,exist_ok=True)
    with (pool/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        for task in TASKS:
            dest=pool/task;dest.mkdir(exist_ok=True)
            for lineage in LINEAGES:
                target=dest/(lineage+'.yaml')
                if not target.exists():
                    seed=root/'repro/seeds'/task/(lineage+'.yaml')
                    atomic(target,seed.read_text(encoding='utf8'))
            idx=dest/'index.json'
            if not idx.exists():
                atomic(idx,json.dumps({lineage:{'public_metric':None,
                    'source':'seed' if lineage in LINEAGES else 'none','signature':None}
                    for lineage in ALL_SLOTS},indent=2)+'\n')
            else:
                data=json.loads(idx.read_text())
                if 'mixed' not in data:
                    data['mixed']={'public_metric':None,'source':'none','signature':None}
                    atomic(idx,json.dumps(data,indent=2)+'\n')
            # A seed slot follows the checked-in seed definition. Never replace
            # a promoted lineage, even when the local seed changes.
            data=json.loads(idx.read_text())
            for lineage in LINEAGES:
                slot=data[lineage]
                if slot.get('source')=='seed' and slot.get('public_metric') is None:
                    seed=root/'repro/seeds'/task/(lineage+'.yaml')
                    expected=seed.read_text(encoding='utf8')
                    target=dest/(lineage+'.yaml')
                    if target.read_text(encoding='utf8')!=expected:
                        atomic(target,expected)

def freeze(pool,dest):
    if dest.exists():raise FileExistsError(dest)
    with (pool/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_SH)
        dest.mkdir(parents=True)
        for task in TASKS:
            target=dest/task;target.mkdir()
            for name in ('majority_vote.yaml','smoa.yaml','index.json'):
                source=pool/task/name
                if not source.is_file():raise FileNotFoundError(source)
                shutil.copyfile(source,target/name)
    return dest

def candidate_score(task,c):
    if task=='bbo':
        score=c.get('visible_score')
        valid=c.get('contract_valid') is True and c.get('selfcheck_passed') is True
    else:
        visible=c.get('visible') or {}
        score=visible.get('metric')
        valid=c.get('valid') is True and visible.get('valid') is True
        if task=='graph':
            valid=valid and visible.get('failed_graphs',0)==0
        if task=='qec':
            settings=visible.get('settings') or []
            valid=valid and len(settings)==4 and all(not x.get('error') for x in settings)
    return float(score) if valid and isinstance(score,(int,float)) and math.isfinite(score) else None

def promote(root,pool,run):
    summary=json.loads((run/'summary.json').read_text(encoding='utf8'))
    task=summary.get('task','bbo')
    if task not in TASKS:raise ValueError(task)
    if summary.get('hidden_status')!='completed' or not isinstance(summary.get('hidden_score'),(int,float)):
        raise ValueError('Only fully graded runs can update the shared pool')
    candidates=summary.get('candidates',[])
    if not 2<=len(candidates)<=6:raise ValueError('Two through six evaluated versions required')
    rows={c['label']:c for c in candidates}
    evidence=run/'decision_evidence.jsonl'
    decisions={r['label']:r for r in map(json.loads,evidence.read_text().splitlines())} if evidence.is_file() else {}
    lineage={'parent_00':'majority_vote','parent_01':'smoa'}
    for c in candidates[2:]:
        label=c['label'];decision=decisions.get(label,{})
        lineage[label]=lineage.get(decision.get('selected_parent'))
    instruction=root/'rsi_exam_tasks'/({'bbo':'bbo_noisy_continuous','inventory':'bbo_simopt_inventory','graph':'johnson1991_leighton_graph_coloring','qec':'qec_decoder_arena'}[task])/'instruction.md'
    if not instruction.is_file():raise FileNotFoundError(instruction)
    signature={'instruction_sha256':hashlib.sha256(instruction.read_bytes()).hexdigest(),
               'public_image_id':summary.get('public_image_id')}
    initial={'majority_vote':candidate_score(task,rows['parent_00']),
             'smoa':candidate_score(task,rows['parent_01'])}
    parent_best=max((x for x in initial.values() if x is not None),default=None)
    best={}
    for c in candidates[2:]:
        name=lineage.get(c['label']);score=candidate_score(task,c)
        if name in LINEAGES and score is not None and (initial[name] is None or score>initial[name]+1e-9):
            if ('crossover' in c['label'] or decisions.get(c['label'],{}).get('action')=='crossover') and parent_best is not None and score<=parent_best+1e-9:
                continue
            if name not in best or score>best[name][0]:best[name]=(score,c['label'])
    init(root,pool)
    with (pool/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        dest=pool/task;idx=json.loads((dest/'index.json').read_text())
        idx.setdefault('mixed',{'public_metric':None,'source':'none','signature':None})
        accepted=[]
        for name,(score,label) in best.items():
            old=idx[name];prior=old.get('public_metric')
            if old.get('signature') not in (None,signature):
                raise ValueError('Public image/instruction changed; do not compare scores across versions')
            if prior is not None and score<=prior+1e-9:continue
            source=run/'evaluations'/(label+'.yaml')
            if not source.is_file():raise FileNotFoundError(source)
            config=yaml.safe_load(source.read_text())
            if not isinstance(config,dict) or not isinstance(config.get('agents'),dict):
                raise ValueError('Invalid promoted YAML '+str(source))
            atomic(dest/(name+'.yaml'),source.read_text(encoding='utf8'))
            idx[name]={'public_metric':score,'source':'evolved','run':str(run.resolve()),
                       'label':label,'signature':signature,'method':run.parent.parent.name,
                       'updated_utc':datetime.now(timezone.utc).isoformat()}
            accepted.append({'lineage':name,'label':label,'public_metric':score})
        atomic(dest/'index.json',json.dumps(idx,ensure_ascii=False,indent=2)+'\n')
    return accepted

def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=('init','freeze','promote'))
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--pool',type=Path)
    ap.add_argument('--dest',type=Path);ap.add_argument('--run',type=Path)
    args=ap.parse_args();root=args.root.resolve();pool=(args.pool or root/'repro/pool').resolve()
    if args.command=='init':init(root,pool)
    elif args.command=='freeze':freeze(pool,args.dest.resolve())
    else:print(json.dumps(promote(root,pool,args.run.resolve()),ensure_ascii=False))
if __name__=='__main__':main()
