"""Shared research evolution scheduling and decision evidence.

Task-specific validation, MAS execution and public scoring live in the adapters.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class StepPlan:
    step: int
    action: str
    label: str
    best_index: int
    ranked: tuple[int, ...]


class EvolutionCycle:
    def __init__(self, seed: int, probability: float, parent_count: int):
        if not 0 <= probability <= 1 or parent_count < 1:
            raise ValueError("Invalid evolution settings")
        self.rng = random.Random(seed)
        self.probability = probability
        self.parent_count = parent_count

    def steps(self, first: int, last: int):
        """Iterate the numbered variation slots after the evaluated parents."""
        yield from range(first, last + 1)

    def skipped(self, count: int = 1) -> None:
        # Preserve the random schedule when resuming BBO after an evaluated step.
        if self.parent_count != 1:
            for _ in range(count):
                self.rng.random()

    def plan(self, step: int, count: int, rank: Callable[[int], tuple]) -> StepPlan:
        if count < 1:
            raise ValueError("No evaluated parent")
        ranked = tuple(sorted(range(count), key=rank, reverse=True))
        mutation = self.parent_count == 1 or count < 2 or self.rng.random() < self.probability
        action = "mutation" if mutation else "crossover"
        return StepPlan(step, action, f"step_{step:02d}_{action}", ranked[0], ranked)


def record_decision(run: Path, task: str, row: dict, append_evidence: Callable) -> dict:
    """Same on-disk envelope for both evaluators and both mutation scopes."""
    row = dict(row)
    row["task"] = task
    row.setdefault("schema_version", 2)
    row.setdefault("parent_public", None)
    row.setdefault("child_public", None)
    row.setdefault("changed_components", [])
    row.setdefault("model_decision", None)
    row.setdefault("prediction_met", None)
    row.setdefault("insight", None)
    path = run / "decision_evidence.jsonl"
    with path.open("a", encoding="utf8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    append_evidence(run, task, row)
    return row
