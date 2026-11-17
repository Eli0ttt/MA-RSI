# MA-RSI 六任务实验

在 RSI-Exam 六项官方任务上，研究 agent 从通用 MAS 配置池选择两份父配置，使用各任务公开数据生成和检查候选，并将最终公开胜出产物交给 Harbor 独立密封验证。每个 seed 最多六个**通过公开自检**的版本；失败的生成或自检尝试另记在 `attempts.json`，不计版本。每个任务的研究时限从其 `task.toml` 的 `[agent].timeout_sec` 读取。

## 安装

将补丁 ZIP 从项目父目录解压并覆盖同名文件；本地的 `env/experiment.env`、`tests/` 和已有结果不在补丁内。检查 `env/experiment.env` 的 `OPENAI_API_KEY`、`MA_RSI_ROOT`、`RSI_TASKS_ROOT`、`HARBOR_BIN`。四模型池、元模型、版本数与请求超时均在 `env/experiment.env` 中设置；`env/experiment.env.example` 给出完整模板。官方公开 Docker 镜像须按每项 `rsi_exam_tasks/<name>/environment/Dockerfile` 构建；六个任务均通过各自的 `rsi-subset-<official-task>:public` 镜像执行公开检查，每次使用一次性隔离容器。保留服务器上原有的官方密封 `tests/`，不要让研究模型读到它。

## 运行

以下六条命令分别放在六个终端，每条顺序跑三个 seed。研究运行不会自动重试已经完成的 seed；`outputs/` 中按时间保存每次运行。

```bash
ROOT=/data/user/wenxinyi/experiments/MA-RSI
STATE="$ROOT/repro/runner/state_current"
for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" bbo original "$seed"; done
for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" inventory original "$seed"; done
for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" graph original "$seed"; done
for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" cxr original "$seed"; done
for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" game2048 original "$seed"; done
for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" dcf original "$seed"; done
```

统一统计在 `repro/runner/state_current/results.csv`：每个 seed 的官方平台分数、官方原指标（如果 verifier 提供）、研究用时、密封评测用时、总用时、按模型可观测的 worker input/output/total token，以及已有 seed 的逐任务均值。均值行 `MEAN_1/2/3` 表示当前已完成样本数；三个 seed 全部结束后为 `MEAN_3`。元模型 token 和未提供 usage 的调用无法从现有日志可靠计数。

## 演化逻辑

通用候选池位于 `repro/pool/rsi-exam-general/`，运行时已经晋升的该任务 YAML 也加入初选池，由元模型选择两份。选中的父配置和后续所有自检有效的配置组成临时池 W。先枚举 W 的全部两两组合：同一组至少四个可比公开子样本，两边各在至少两个子样本占优，并存在相同 agent ID 的不同模型分配，才进行 EvoMAS 式交叉；继承一份完整拓扑，按对应位置重组 agent。否则从 W 取 reward 最高的两份，基于反馈变异其中分数最高的一份，仅选择 model_id 或 topology。新 agent 使用 s2 和八项基础研究工具。关联性证据不等于单个 agent 的因果优势；未满足条件的任务只进行变异。

`trajectory_evidence.jsonl` 保存操作与公开证据，`best_reward_config.yaml` 和 `best_visible_solver.py` 保存最终选择。公开的经验与角色摘要并发写入 `repro/memory/`、`repro/skill/`；密封分数只进入汇总与 manifest。新任务的官方公开包位于 `rsi_exam_tasks/`，没有打包密封测试。

## 环境变量命名

现有服务器 `env/experiment.env` 中若保留旧变量名，请只替换通用变量的名称、保留其值及 API key。旧的 `BBO_EVAL_CONTAINER` 无需保留；六项公开评测均直接使用任务镜像。

| 旧变量 | 新变量 | 用途 |
| --- | --- | --- |
| `BBO_ROOT` | `MA_RSI_ROOT` | 项目根目录 |
| `BBO_EVAL_CONTAINER` | 删除 | 不再需要长驻公开容器 |
| `BBO_OPENAI_TIMEOUT` | `MODEL_REQUEST_TIMEOUT_SEC` | 全任务模型请求超时 |
| `BBO_OPENAI_MAX_RETRIES` | `MODEL_REQUEST_MAX_RETRIES` | 全任务模型请求重试 |
| `SUBSET_MAX_VERSIONS` | `MAX_VALID_VERSIONS` | 每次任务的有效版本上限 |

`run.sh` 只读取并检查环境变量，不再重写元模型、模型池或版本数。`migrate.py` 也会把旧项目环境变量转换为新名称。

BBO 公开镜像可从官方公开 `environment/Dockerfile` 构建，名称与其余任务相同：

```bash
ROOT=/data/user/wenxinyi/experiments/MA-RSI
docker build -t rsi-subset-bbo_noisy_continuous:public "$ROOT/rsi_exam_tasks/bbo_noisy_continuous/environment"
```

公开评测会将候选 `solver.py` 只读挂载到镜像的 `/app/methods/main/solver.py`，执行官方 `selfcheck.py --json`。接口探针与公开轨迹探针也在同一镜像的独立临时容器中执行；无需已有的 `bbo-evomas-visible-wenxinyi` 容器。
