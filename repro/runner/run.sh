#!/usr/bin/env bash
set -euo pipefail
# 初始化 Conda 并激活 mem 环境
source /data/user/wenxinyi/miniconda3/etc/profile.d/conda.sh
conda activate /data/user/wenxinyi/miniconda3/envs/mem
# 确认当前 Python 环境
echo "[ENV] CONDA_PREFIX=$CONDA_PREFIX"
echo "[ENV] Python=$(command -v python)"
ROOT="$(cd "${1:?Usage: run.sh ROOT STATE TASK original SEED}" && pwd)"
STATE="$(realpath -m "${2:?STATE}")"
TASK="${3:?TASK}"; MODE="${4:?MODE}"; SEED="${5:?SEED}"
[[ "$TASK" =~ ^(bbo|inventory|graph|cxr|game2048|dcf)$ && "$MODE" == original && "$SEED" =~ ^[0-9]+$ ]] || exit 2
set -a
source "$ROOT/env/experiment.env"
set +a
[[ "$MA_RSI_ROOT" == "$ROOT" && -n "${OPENAI_API_KEY:-}" && -n "${OPENAI_BASE_URL:-}" ]] || exit 2
[[ -n "${META_MODEL:-}" && -n "${MODEL_POOL:-}" ]] || { echo 'META_MODEL and MODEL_POOL are required in env/experiment.env' >&2; exit 2; }
[[ "${MAX_VALID_VERSIONS:-}" == 6 ]] || { echo 'MAX_VALID_VERSIONS must equal 6' >&2; exit 2; }
RUNNER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$TASK" in
 bbo) NAME=bbo_noisy_continuous;;
 inventory) NAME=bbo_simopt_inventory;;
 graph) NAME=johnson1991_leighton_graph_coloring;;
 cxr) NAME=cxr_ood_triage_policy;;
 game2048) NAME=game2048_policy_search;;
 dcf) NAME=finscope_dcf_valuation;;
esac
TASK_ROOT="$RSI_TASKS_ROOT/$NAME"
python "$RUNNER/task_preflight.py" "$TASK" "$TASK_ROOT"
[[ -x "$HARBOR_BIN" ]] || { echo 'Harbor missing' >&2; exit 2; }
docker image inspect "rsi-subset-$NAME:public" >/dev/null
OUT="$RUNNER/outputs/${TASK}_${MODE}_seed${SEED}_$(date -u +%Y%m%dT%H%M%SZ)_$$"
POOL="$OUT.input";mkdir -p "$POOL"
GENERAL_POOL="$ROOT/repro/pool/rsi-exam-general"
[[ -d "$GENERAL_POOL" ]] || { echo 'General parent pool missing' >&2; exit 2; }
shopt -s nullglob
for source in "$GENERAL_POOL"/*.yaml; do cp -- "$source" "$POOL/${source##*/}"; done
for source in "$ROOT/repro/pool/$TASK"/evolved_*.yaml; do cp -- "$source" "$POOL/task__${source##*/}"; done
shopt -u nullglob
[[ $(find "$POOL" -maxdepth 1 -name '*.yaml' | wc -l) -ge 2 ]] || { echo 'At least two parent YAML files required' >&2; exit 2; }
printf 'RUN_DIR=%s\nRESULTS=%s/results.csv\n' "$OUT" "$STATE"
python "$RUNNER/task_pipeline.py" "$TASK" --project-root "$ROOT" --task-root "$TASK_ROOT" \
 --seed-pool "$POOL" --output-dir "$OUT" --seed "$SEED" --max-versions "$MAX_VALID_VERSIONS" 2>&1 | tee "$OUT.log"
python "$RUNNER/submit_hidden.py" --root "$ROOT" --run "$OUT" --task "$TASK" \
 --harbor-bin "$HARBOR_BIN" 2>&1 | tee "$OUT.hidden_handoff.log"
python "$RUNNER/state.py" experiment-commit --root "$ROOT" --state "$STATE" --run "$OUT" \
 2>&1 | tee "$OUT.memory_commit.log"
rm -rf -- "$POOL"
