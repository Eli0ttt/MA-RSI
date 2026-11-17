#!/usr/bin/env python3
"""Evaluate a full solver on the official visible selfcheck and local API probe."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

class CandidateSelfcheckError(Exception):
    """Official traceback references the solver."""


CONTRACT_PROBE = r'''
import importlib.util, json, sys
import numpy as np
spec = importlib.util.spec_from_file_location("candidate_contract_probe", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
report = {"valid": True, "cases": [], "warnings": []}
batch_cases = (([1] * 12, [31], [7, 13, 17, 23]) if sys.argv[2:] == ["--quick"]
               else ([1] * 120, [31, 31, 31, 27], [120], [7, 13, 17, 19, 23, 41]))
for batches in batch_cases:
    opt = module.Optimizer(10, np.full(10, -5.0), np.full(10, 5.0), 120, 0, np.random.default_rng(0))
    seen = []
    try:
        for n in batches:
            points = np.asarray(opt.ask(n), dtype=float)
            assert points.ndim == 2 and 1 <= len(points) <= n and points.shape[1] == 10
            assert np.isfinite(points).all() and (points >= -5).all() and (points <= 5).all()
            seen.extend(map(tuple, points))
            # Deterministic synthetic observations, independent of coordinates.
            # This tests the interface and state transitions, not optimization.
            y = (np.arange(len(points)) + len(seen)) % 7
            opt.tell(points, y.astype(float))
    except Exception as exc:
        report.update(valid=False, failed_case=list(batches[:8]),
                      failed_after=len(seen), error=type(exc).__name__ + ": " + str(exc))
        print("PROBE_JSON:" + json.dumps(report))
        sys.exit(1)
    unique = len(set(seen))
    report["cases"].append({"requests": len(batches), "observed": len(seen),
                            "unique": unique})
    if len(batches) == 120:
        for attr in ("queries_used", "query_count"):
            value = getattr(opt, attr, None)
            if isinstance(value, (int, np.integer)) and value != len(seen):
                report["warnings"].append(
                    f"internal {attr}={value} but tell received {len(seen)} points")
        if len(seen) >= 30 and len(set(seen[-30:])) == 1:
            report["warnings"].append("last 30 proposals were the same point")
print("CONTRACT_PASS")
print("PROBE_JSON:" + json.dumps(report))
'''

# Research-side diagnostic only. This reads the official PUBLIC visible cases
# and uses the official selfcheck's seed, batch, validation and tell helpers.
# It never becomes part of the submitted solver or supplies an objective tool.
PUBLIC_TRACE_PROBE = r'''
import json, sys
import numpy as np
sys.path.insert(0, "/app")
import selfcheck as sc
import bbo_harness
kind = sc._load_optimizer(__import__("pathlib").Path(sys.argv[1]))
report = []
for idx in (0, 5, 11):
    instance = sc.DATA["instances"][idx]
    for seed_idx in (0, 1):
        seed = sc._run_seed(instance, instance_index=idx, seed_index=seed_idx)
        objective, lower, upper = bbo_harness.make_objective(instance, seed=seed)
        lower, upper = np.asarray(lower, float), np.asarray(upper, float)
        optimizer = kind(dim=len(lower), lower=lower, upper=upper,
                         budget=120, seed=seed, rng=np.random.default_rng(seed))
        best = float("inf")
        checkpoints = []
        observed = []
        points = []
        next_batch = sc._batch_hint(optimizer, remaining=120)
        while len(observed) < 120:
            n = min(next_batch, 120-len(observed))
            X = sc._validate_points(optimizer.ask(n), dim=len(lower), lower=lower,
                                    upper=upper, requested=n, remaining=120-len(observed))
            hint = sc._batch_hint(optimizer, remaining=120-len(observed))
            y = []
            for x in X:
                value = float(objective(x))
                latent = float(bbo_harness.latent_objective(instance, x))
                best = min(best, latent)
                y.append(value)
                observed.append(value)
                points.append(tuple(x))
                if len(observed) in (20, 60, 120):
                    checkpoints.append(round(best, 5))
            sc._tell(optimizer, X, np.asarray(y, float))
            next_batch = min(hint, max(120-len(observed), 1))
        report.append({"instance": idx, "seed_index": seed_idx,
                       "latent_best_20_60_120": checkpoints,
                       "unique_120": len(set(points)),
                       "noisy_min": round(min(observed), 5)})
print("PUBLIC_TRACE_JSON:" + json.dumps(report, separators=(",", ":")))
'''


def _public_command(solver: Path, image: str, *args: str) -> list[str]:
    """One isolated public image invocation, matching the other task adapters."""
    from task_visible import command, NAMES
    expected = f"rsi-subset-{NAMES['bbo']}:public"
    if image != expected:
        raise ValueError(f"Expected the official BBO public image {expected}")
    base = command('bbo', solver.parent, solver, Path(__file__))
    return base[:base.index(image) + 1] + ['python', *args]


def _probe(solver: Path, image: str, program: str, timeout: int):
    return subprocess.run(_public_command(solver, image, '-c', program,
                          '/app/methods/main/solver.py'),
                          text=True, capture_output=True, timeout=timeout, check=False)


def run_behavior_probe(solver: Path, output_dir: Path, image: str) -> dict:
    """Synthetic ask/tell contract check in a fresh public image."""
    try:
        proc = _probe(solver, image, CONTRACT_PROBE, 60)
        (output_dir/'contract_probe.log').write_text(proc.stdout + proc.stderr)
        record = next((json.loads(line.removeprefix('PROBE_JSON:'))
                       for line in proc.stdout.splitlines()
                       if line.startswith('PROBE_JSON:')), None)
        result = record if isinstance(record, dict) else {
            'valid': False, 'error': (proc.stderr or proc.stdout)[-1200:]}
        result['valid'] = bool(result.get('valid') and proc.returncode == 0
                               and 'CONTRACT_PASS' in proc.stdout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = {'valid': False, 'error': str(exc)[-1200:]}
        (output_dir/'contract_probe.log').write_text(result['error'])
    (output_dir/'behavior_probe.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def run_public_trace(solver: Path, output_dir: Path, image: str) -> None:
    """Diagnostic on six visible instance/seed pairs in another fresh image."""
    try:
        proc = _probe(solver, image, PUBLIC_TRACE_PROBE, 30)
        record = next((json.loads(line.removeprefix('PUBLIC_TRACE_JSON:'))
                       for line in proc.stdout.splitlines()
                       if line.startswith('PUBLIC_TRACE_JSON:')), None)
        if proc.returncode or not isinstance(record,list):
            raise ValueError((proc.stderr or proc.stdout)[-700:])
        (output_dir/'public_trace.json').write_text(json.dumps(record,indent=2)+'\n')
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        (output_dir/'public_trace_error.txt').write_text(str(exc)[-900:]+'\n')


def parse_selfcheck_output(stdout: str) -> dict:
    required = {'score','score_anytime','score_final','mean_final_latent_objective'}
    for line in stdout.splitlines():
        try: result = json.loads(line)
        except json.JSONDecodeError: continue
        if isinstance(result,dict) and required.issubset(result):return result
    raise ValueError('Official BBO selfcheck emitted no complete score')


def evaluate_solver(solver_text: str, output_dir: Path, image: str) -> dict:
    output_dir.mkdir(parents=True,exist_ok=True)
    solver=output_dir/'candidate_solver.py'
    solver.write_text(solver_text,encoding='utf8')
    behavior=run_behavior_probe(solver,output_dir,image)
    if not behavior['valid']:
        raise CandidateSelfcheckError('BBO contract probe failed: '+str(behavior.get('error',''))[-1000:])
    proc=subprocess.run(_public_command(solver,image,'/app/selfcheck.py','--json'),
                        text=True,capture_output=True,check=False)
    (output_dir/'selfcheck.stdout.log').write_text(proc.stdout+proc.stderr)
    if proc.returncode:
        raise CandidateSelfcheckError('Official BBO public selfcheck failed: '+
                                      (proc.stderr or proc.stdout)[-2200:])
    try:result=parse_selfcheck_output(proc.stdout)
    except ValueError as exc:raise CandidateSelfcheckError(str(exc)) from exc
    result['contract_valid']=True
    result['contract_issue']=''
    result['behavior_probe']=behavior
    (output_dir/'selfcheck.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    run_public_trace(solver,output_dir,image)
    return result
