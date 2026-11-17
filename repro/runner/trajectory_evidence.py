"""Auditable public evidence and semantic checks for MAS evolution."""
from __future__ import annotations
import json
import math
import yaml


def mutation_component(parent, child):
    a,b=yaml.safe_load(parent),yaml.safe_load(child)
    if set(a)!=set(b) or any(a[k]!=b[k] for k in a if k not in ('agents','topology')):
        raise ValueError('Unrelated YAML metadata changed')
    old,new=a['agents'],b['agents']
    if any(new[k].get('tools')!=old[k].get('tools') for k in old.keys()&new.keys()):
        raise ValueError('Existing research tool assignment changed')
    topology=a['topology']!=b['topology'] or set(old)!=set(new)
    changed_model=any(old[k].get('model_id')!=new[k].get('model_id') for k in old.keys()&new.keys())
    if any(any(old[k].get(field)!=new[k].get(field) for field in ('temperature','max_tokens','device','agent_type')) for k in old.keys()&new.keys()):
        raise ValueError('Existing agent execution parameters changed')
    role_changed=any(old[k].get('role')!=new[k].get('role') for k in old.keys()&new.keys())
    prompt_changed=any(old[k].get('prompt')!=new[k].get('prompt') for k in old.keys()&new.keys())
    if any(old[k].get('prompt')!=new[k].get('prompt') and old[k].get('role')==new[k].get('role')
           for k in old.keys()&new.keys()):
        raise ValueError('Prompt may change only with a changed role')
    topology=topology or role_changed
    if topology:
        if changed_model:raise ValueError('Topology mutation also switched an existing model')
        for k in new.keys()-old.keys():
            if new[k]['model_id']!='openai:deepseek-v4-flash-s2':raise ValueError('New agent must start on s2')
        return 'topology'
    if prompt_changed:raise ValueError('Standalone prompt edit is disallowed')
    if changed_model:return 'model_id'
    raise ValueError('No effective mutation')


def crossover_change(parent,child):
    a,b=yaml.safe_load(parent),yaml.safe_load(child)
    return a['topology']!=b['topology'] or a['agents']!=b['agents']


def crossover_compatible(first,second,child):
    a,b,c=map(yaml.safe_load,(first,second,child))
    if c['topology'] not in (a['topology'],b['topology']):raise ValueError('Topology must be inherited whole')
    donor=a if c['topology']==a['topology'] else b
    if set(c['agents'])!=set(donor['agents']):raise ValueError('Topology agent positions mismatch')
    if any(c['agents'][k]['tools']!=donor['agents'][k]['tools'] for k in c['agents']):
        raise ValueError('Research tool assignments changed')
    for k,v in c['agents'].items():
        if k not in a['agents'] and k not in b['agents']:
            raise ValueError('Crossover created a new position')
        if v.get('prompt') not in [d['agents'][k].get('prompt') for d in (a,b) if k in d['agents']]:
            raise ValueError('Crossover invented a new prompt')
        if v.get('model_id') not in [d['agents'][k].get('model_id') for d in (a,b) if k in d['agents']]:
            raise ValueError('Crossover invented a new model assignment')
    if not crossover_change(first,child) or not crossover_change(second,child):
        raise ValueError('Offspring must differ from both parents')
    if not any(c['agents'][k].get('model_id')==b['agents'][k].get('model_id') != a['agents'][k].get('model_id')
               for k in c['agents'].keys()&a['agents'].keys()&b['agents'].keys()):
        raise ValueError('No inherited model assignment from the second parent')
    return True


def complement_pair(records, excluded=frozenset()):
    """All valid pairs in W; same public cases, two independent directional wins."""
    candidates=[]
    for i,left in enumerate(records):
        x=left['visible'].get('components') or {}
        if not left['visible']['valid'] or len(x)<4:continue
        for j in range(i+1,len(records)):
            right=records[j];y=right['visible'].get('components') or {}
            if (left['label'],right['label']) in excluded:continue
            if not right['visible']['valid'] or x.keys()!=y.keys() or len(y)<4:continue
            if not any(yaml.safe_load(z['config'])['topology']==yaml.safe_load(left['config'])['topology']
                       for z in (left,right)):continue
            wins_l=[k for k in x if math.isfinite(x[k]) and math.isfinite(y[k]) and x[k]>y[k]+1e-9]
            wins_r=[k for k in x if math.isfinite(x[k]) and math.isfinite(y[k]) and y[k]>x[k]+1e-9]
            if len(wins_l)<2 or len(wins_r)<2:continue
            shared=set(yaml.safe_load(left['config'])['agents'])&set(yaml.safe_load(right['config'])['agents'])
            differing=[k for k in shared if yaml.safe_load(left['config'])['agents'][k]['model_id']!=yaml.safe_load(right['config'])['agents'][k]['model_id']]
            if not differing:continue
            evidence={'left':left['label'],'right':right['label'],'left_wins':wins_l,'right_wins':wins_r,
                      'matched_public_cases':len(x),'aligned_positions':differing,
                      'interpretation':'Association only; public cases do not isolate agent contributions'}
            candidates.append((len(wins_l)+len(wins_r),left['reward']+right['reward'],i,j,evidence))
    return max(candidates,key=lambda row:(row[0],row[1]))[2:] if candidates else None


def public_rank(task,result,reward=0.):
    metric=result.get('metric')
    return (bool(result.get('valid')),float(metric) if isinstance(metric,(int,float)) and math.isfinite(metric) else float('-inf'),reward)
