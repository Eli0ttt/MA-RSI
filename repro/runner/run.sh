#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "${1:?Usage: run.sh ROOT STATE TASK original SEED}" && pwd)"
STATE="$(realpath "${2:?STATE}")"
TASK="${3:?TASK}"; MODE="${4:?MODE}"
set -a
source "$ROOT/env/experiment.env"
set +a
SEED="${5:-${EXPERIMENT_SEED:-72}}"
[[ "$TASK" =~ ^(bbo|inventory|graph|qec)$ && "$MODE" == original && "$SEED" =~ ^[0-9]+$ ]] || exit 2
[[ "$BBO_ROOT" == "$ROOT" && -n "${OPENAI_BASE_URL:-}" ]] || exit 2
[[ -n "${META_MODEL:-}" && -n "${MODEL_POOL:-}" && ,"$MODEL_POOL", == *,"$META_MODEL",* ]] || { echo 'Invalid META_MODEL or MODEL_POOL' >&2; exit 2; }
[[ "$SUBSET_MAX_VERSIONS" == 6 && "$MUTATION_PROBABILITY" == 0.7 ]] || exit 2
[[ -n "$OPENAI_API_KEY" && -f "$STATE/manifest.json" && -x "$HARBOR_BIN" ]] || exit 2
RUNNER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$RUNNER/outputs/${TASK}_${MODE}_seed${SEED}_$(date -u +%Y%m%dT%H%M%SZ)_$$"
INPUT="$OUT.input"
PARENT_POLICY="${PARENT_CONFIG_POLICY:-exact}"
[[ "$PARENT_POLICY" == exact || "$PARENT_POLICY" == generated ]] || exit 2
[[ "${PARENT_POOL_POLICY:-seeds}" == seeds ]] || { echo "Use the two lineage YAML slots with PARENT_POOL_POLICY=seeds" >&2; exit 2; }
PARENT_FLAG=()
[[ "$PARENT_POLICY" != exact ]] || PARENT_FLAG=(--exact-parents)
POOL_POLICY="${PARENT_POOL_POLICY:-seeds}"
[[ "$POOL_POLICY" == seeds || "$POOL_POLICY" == task_top2 ]] || exit 2
PARENT_POOL_DIR="${PARENT_POOL_DIR:-$ROOT/repro/pool}"
SHARED_ARGS=()
[[ -z "${PARENT_POOL_DIR:-}" ]] || SHARED_ARGS=(--shared-parent-pool "$PARENT_POOL_DIR/$TASK")
python "$RUNNER/state.py" export --root "$ROOT" --state "$STATE" --task "$TASK" --dest "$INPUT" --pool-policy "$POOL_POLICY" "${SHARED_ARGS[@]}"
printf 'RUN_DIR=%s\nINPUT_SNAPSHOT=%s\n' "$OUT" "$INPUT"
case "$TASK" in
  bbo) NAME=bbo_noisy_continuous;;
  inventory) NAME=bbo_simopt_inventory;;
  graph) NAME=johnson1991_leighton_graph_coloring;;
  qec) NAME=qec_decoder_arena;;
esac
TASK_ROOT="$RSI_TASKS_ROOT/$NAME"
[[ -f "$TASK_ROOT/task.toml" ]] || { echo "Missing task.toml: $TASK_ROOT" >&2; exit 2; }
if [[ "$TASK" == bbo ]]; then
  [[ "$(docker inspect -f '{{.State.Running}}' "$BBO_EVAL_CONTAINER")" == true ]]
  python "$RUNNER/task_preflight.py" "$TASK" "$TASK_ROOT"
  PYTHONPATH="$RUNNER${PYTHONPATH:+:$PYTHONPATH}" \
    python "$RUNNER/continuous_pipeline.py" \
      --evomas-root "$ROOT/vendor/meta_engine" --seed-pool "$INPUT/working_pool" \
      --instruction "$TASK_ROOT/instruction.md" --output-dir "$OUT" --container "$BBO_EVAL_CONTAINER" \
      --meta-model "$META_MODEL" --model-pool "$MODEL_POOL" \
      "${PARENT_FLAG[@]}" --num-parents 2 --max-solver-versions "$SUBSET_MAX_VERSIONS" --skip-final-repeat \
      --mutation-probability "$MUTATION_PROBABILITY" --mutation-mode "$MODE" --tool-mode bbo \
      --seed "$SEED" --pool-warm-start --knowledge-scope cross-run \
      --initial-memory "$INPUT/memory.json" --knowledge-snapshot "$INPUT" \
      --no-memory-evolution 2>&1 | tee "$OUT.log"
else
  docker image inspect "rsi-subset-$NAME:public" >/dev/null
  python "$RUNNER/task_preflight.py" "$TASK" "$TASK_ROOT"
  python "$RUNNER/task_pipeline.py" "$TASK" \
    --project-root "$ROOT" --task-root "$TASK_ROOT" --seed-pool "$INPUT/working_pool" \
    --initial-memory "$INPUT/memory.json" --output-dir "$OUT" \
    "${PARENT_FLAG[@]}" --mutation-mode "$MODE" --seed "$SEED" --max-versions "$SUBSET_MAX_VERSIONS" \
    --mutation-probability "$MUTATION_PROBABILITY" 2>&1 | tee "$OUT.log"
fi
cp "$INPUT/provenance.json" "$OUT/input_provenance.json"
python "$RUNNER/submit_hidden.py" --root "$ROOT" --run "$OUT" --task "$TASK" \
  2>&1 | tee "$OUT.hidden_handoff.log"
python "$RUNNER/state.py" commit --root "$ROOT" --state "$STATE" --run "$OUT" \
  2>&1 | tee "$OUT.memory_commit.log"

# Parent YAML and scores remain in RUN_DIR/evaluations; discard the input copy.
rm -rf -- "$INPUT"
