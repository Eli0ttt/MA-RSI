#!/usr/bin/env python3
"""Install local official task bundles and runtime state into this code scaffold.

Copies only; original checkout is never edited or deleted. Do not publish local tests/.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def set_shell(raw,key,value):
    line=f'{key}={shlex.quote(str(value))}'
    pattern=re.compile(r'^'+re.escape(key)+r'=.*$',re.M)
    return pattern.sub(lambda _:line,raw) if pattern.search(raw) else raw+'\n'+line+'\n'

def mark_legacy_runs(path, old_root):
    """Make copied knowledge portable after the source checkout is removed."""
    try:
        payload = json.loads(path.read_text(encoding='utf8'))
    except (OSError, json.JSONDecodeError):
        return
    prefix = str(old_root) + os.sep

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'run' and isinstance(item, str) and item.startswith(prefix):
                    value[key] = 'legacy/' + item[len(prefix):]
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf8')

def copy_task(name, source, destination):
    """Copy one official task, never the old project's unrelated root files."""
    if name == 'bbo_noisy_continuous':
        destination.mkdir(parents=True)
        for relative in ('task.toml', 'instruction.md'):
            shutil.copy2(source / relative, destination / relative)
        for relative in ('environment', 'tests'):
            shutil.copytree(source / relative, destination / relative)
    else:
        shutil.copytree(source, destination)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--old',type=Path,required=True,help='Old bbo_noisy_continuous root')
    ap.add_argument('--new',type=Path,default=Path(__file__).resolve().parent)
    args=ap.parse_args()
    old=args.old.resolve();new=args.new.resolve()
    if old==new or not (old/'repro/subset_evomas/run.sh').is_file():
        ap.error('Old project not found or is the target')
    if new != Path(__file__).resolve().parent or not (new/'repro/runner/run.sh').is_file():
        ap.error('Run migrate.py from its extracted MA-RSI scaffold')
    tasks={'bbo_noisy_continuous':old,
           'bbo_simopt_inventory':old/'rsi_exam_tasks/bbo_simopt_inventory',
           'johnson1991_leighton_graph_coloring':old/'rsi_exam_tasks/johnson1991_leighton_graph_coloring',
           'qec_decoder_arena':old/'rsi_exam_tasks/qec_decoder_arena'}
    for name,src in tasks.items():
        for required in ('task.toml','instruction.md','environment/Dockerfile','environment/methods/main/solver.py','tests/Dockerfile'):
            if not (src/required).is_file():raise FileNotFoundError(src/required)
        dst=new/'rsi_exam_tasks'/name
        if dst.exists():raise FileExistsError(f'Refusing to overwrite {dst}')
    env=new/'env/experiment.env'
    if env.exists():raise FileExistsError(f'Refusing to overwrite credentials {env}')
    for name,src in tasks.items():
        dst=new/'rsi_exam_tasks'/name
        copy_task(name, src, dst)
        for relative in ('task.toml','instruction.md','environment/Dockerfile','environment/methods/main/solver.py'):
            assert digest(src/relative)==digest(dst/relative), (name,relative)
    previous=old/'env/dsh_bbo_experiment.env'
    original=previous.read_text(encoding='utf8') if previous.is_file() else (new/'env/experiment.env.example').read_text(encoding='utf8')
    renamed={'SUBSET_MAX_VERSIONS':'MAX_VALID_VERSIONS',
             'BBO_OPENAI_TIMEOUT':'MODEL_REQUEST_TIMEOUT_SEC',
             'BBO_OPENAI_MAX_RETRIES':'MODEL_REQUEST_MAX_RETRIES'}
    allowed={'OPENAI_API_KEY','OPENAI_BASE_URL','META_MODEL','MODEL_POOL','HARBOR_BIN',
             'MAX_VALID_VERSIONS','MODEL_REQUEST_TIMEOUT_SEC',
             'MODEL_REQUEST_MAX_RETRIES','EVOMAS_PER_MODEL_CONCURRENCY',
             'EVOMAS_MODEL_LOCK_DIR','KNOWLEDGE_READ_MODE'}
    lines=[]
    for line in original.splitlines():
        key=line.split('=',1)[0].strip().removeprefix('export ')
        if key in renamed:
            line=line.replace(key+'=',renamed[key]+'=',1)
            key=renamed[key]
        if key in allowed or (line.strip().startswith('source ') and '/bin/activate ' in line):
            lines.append(line)
    raw='\n'.join(lines)+'\n'
    for key,value in (('MA_RSI_ROOT',new),('RSI_TASKS_ROOT',new/'rsi_exam_tasks'),
                      ('MAX_VALID_VERSIONS',6),('MODEL_REQUEST_TIMEOUT_SEC',180),
                      ('MODEL_REQUEST_MAX_RETRIES',2)):
        if not any(line.split('=',1)[0].strip()==key for line in raw.splitlines()):
            raw=set_shell(raw,key,value)
    env.write_text(raw,encoding='utf8');env.chmod(0o600)
    # Keep the current two lineage YAMLs and distilled knowledge available locally.
    for folder in ('pool','memory','skill'):
        src=old/'repro'/folder;dst=new/'repro'/folder
        if src.is_dir():
            dst.mkdir(parents=True,exist_ok=True)
            for item in src.iterdir():
                if item.name.startswith('.') or item.name.endswith('.lock') or item.name=='manage.py':
                    continue
                if item.is_dir():shutil.copytree(item,dst/item.name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.lock','.lock'))
                elif item.suffix=='.json':shutil.copy2(item,dst/item.name)
    metadata_files = (list((new/'repro/memory').glob('*/*.json')) +
                      list((new/'repro/pool').glob('*/index.json')) +
                      [new/'repro/skill/skills.json'])
    for metadata_file in metadata_files:
        if metadata_file.is_file():
            mark_legacy_runs(metadata_file, old)
    state=new/'repro/runner/state_current'
    subprocess.run([sys.executable,str(new/'repro/runner/state.py'),'init',
                    '--root',str(new),'--state',str(state)],check=True)
    subprocess.run([sys.executable,str(new/'repro/pool_manager.py'),'init',
                    '--root',str(new)],check=True)
    for name in tasks:
        assert (new/'rsi_exam_tasks'/name/'task.toml').is_file()
    assert json.loads((state/'manifest.json').read_text())['runs']==[]
    with tempfile.TemporaryDirectory(prefix='layout-check-',dir=new/'repro/runner') as td:
        for task in ('bbo','inventory','graph','qec'):
            subprocess.run([sys.executable,str(new/'repro/runner/state.py'),'export',
                '--root',str(new),'--state',str(state),'--task',task,
                '--dest',str(Path(td)/task),'--shared-parent-pool',str(new/'repro/pool'/task)],check=True,
                stdout=subprocess.DEVNULL)
            assert sorted(p.name for p in (Path(td)/task/'working_pool').glob('*.yaml'))==['majority_vote.yaml','smoa.yaml']
    print('MA-RSI installed and four task layouts exported; original project untouched.')
    print('Local sealed task tests were copied but ignored by Git. Never force-add them.')
    print('Copied knowledge marks old evidence as legacy/...; the old checkout is not a runtime dependency.')

if __name__=='__main__':main()
