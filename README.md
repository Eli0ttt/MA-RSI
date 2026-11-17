# MA-RSI

在 RSI-Exam 官方任务上，从通用池和当前任务的已进化池选择两份 MAS 父配置。配置 YAML 用于组织代码生成；本实验真正计数、集中审查和最终测评的对象是其生成的求解器源码。每个 seed 必须取得 **六份源码哈希不同、完整且通过官方公开 selfcheck 的 solver 版本**，然后才做最终公开泛化风险探索并将选中的一份交给 Harbor 密封评测。生成失败、配置无效、代码接口无效、selfcheck 失败和重复源码均不占六版名额。研究时限从任务 `task.toml` 的 `[agent].timeout_sec` 读取；到时不足六版时停止该 seed，保留证据但不跑 hidden。

## 运行

以下六条完整命令分别放在六个终端，每条顺序跑三个 seed。每个终端单独定义 `ROOT` 和 `STATE`。研究运行不会自动重试已经完成的 seed；`outputs/` 中按时间保存每次运行。

```bash
ROOT=/data/user/wenxinyi/experiments/MA-RSI; STATE="$ROOT/repro/runner/state_current"; for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" bbo original "$seed"; done
ROOT=/data/user/wenxinyi/experiments/MA-RSI; STATE="$ROOT/repro/runner/state_current"; for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" inventory original "$seed"; done
ROOT=/data/user/wenxinyi/experiments/MA-RSI; STATE="$ROOT/repro/runner/state_current"; for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" graph original "$seed"; done
ROOT=/data/user/wenxinyi/experiments/MA-RSI; STATE="$ROOT/repro/runner/state_current"; for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" cxr original "$seed"; done
ROOT=/data/user/wenxinyi/experiments/MA-RSI; STATE="$ROOT/repro/runner/state_current"; for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" game2048 original "$seed"; done
ROOT=/data/user/wenxinyi/experiments/MA-RSI; STATE="$ROOT/repro/runner/state_current"; for seed in 11 22 33; do bash "$ROOT/repro/runner/run.sh" "$ROOT" "$STATE" dcf original "$seed"; done
```

统一统计在 `repro/runner/state_current/results.csv`：每个 seed 的官方平台分数、官方原指标（如果 verifier 提供）、研究用时、密封评测用时、总用时、按模型可观测的 worker input/output/total token，以及已有 seed 的逐任务均值。均值行 `MEAN_1/2/3` 表示当前已完成样本数；三个 seed 全部结束后为 `MEAN_3`。元模型 token 和未提供 usage 的调用无法从现有日志可靠计数。

## 演化逻辑

通用候选池位于 `repro/pool/rsi-exam-general/`，运行时已经晋升的该任务 YAML 也加入初选池，由元模型选择两份。选中的父配置和后续所有自检有效的配置组成临时池 W。先枚举 W 的全部两两组合：同一组至少四个可比公开子样本，两边各在至少两个子样本占优，并存在相同 agent ID 的不同模型分配，才进行 EvoMAS 式交叉；继承一份完整拓扑，按对应位置重组 agent。否则从 W 取 reward 最高的一份有效配置，基于其反馈进行单父变异，仅选择 model_id 或 topology。新 agent 使用 s2 和八项基础研究工具。关联性证据不等于单个 agent 的因果优势；未满足条件的任务只进行变异。

`trajectory_evidence.jsonl` 保存操作与公开证据，`best_reward_config.yaml` 和 `best_visible_solver.py` 保存最终选择。公开的经验与角色摘要并发写入 `repro/memory/`、`repro/skill/`；密封分数只进入汇总与 manifest。新任务的官方公开包位于 `rsi_exam_tasks/`，没有打包密封测试。

## 当前实验流程与边界

下面的图按实际执行顺序展开。`YAML` 是多 agent 的执行配置，`candidate_solver.py` 是该配置一次运行生成的代码；只有后者通过官方公开 selfcheck，且源码 SHA256 在本轮首次出现，才算一个有效 solver 版本。2048 的官方提交文件名为 `policy.py`，DCF 为 `forecaster.py`，其他任务为 `solver.py`。

```mermaid
flowchart TD
  A["启动 task、seed，加载 experiment.env"] --> B["预检官方任务文件、Harbor 与本任务公开 Docker 镜像"]
  B --> C["读取 task.toml 研究时限；强制 MAX_VALID_VERSIONS=6"]
  C --> D["合并 pool/rsi-exam-general/*.yaml 与 pool/任务/evolved_*.yaml"]
  D --> E["通用配置保留原名，任务进化配置加 task__ 前缀；冻结 input/pool_snapshot 和 provenance.json"]
  E --> F["复制并规范化 YAML：模型池、s2 初始化、八项工具；验证 DAG"]
  F --> G["元模型从快照选两份不同父 YAML；预写选择证据"]
  G --> H["分别执行父 MAS；保存节点轨迹及候选源码"]
  H --> I["检查代码接口；在任务公开镜像中执行官方公开检查"]
  I --> J["写 visible.json、attempts.json、轨迹与选择后证据"]
  J --> K{"源码完整且公开检查有效？"}
  K -->|否| L["标记失败阶段和类型；不进入 W，不增加版本"]
  K -->|是| M["YAML 哈希去重后进入本轮 W；保存 working_pool/index.json"]
  M --> N{"源码 SHA256 本轮首次出现？"}
  N -->|是| O["candidate_manifest 增加一个有效 solver 版本"]
  N -->|否| P["记录 duplicate_solver；不增加版本"]
  L --> Q{"已得六份有效 solver？"}
  O --> Q
  P --> Q
  Q -->|否| R{"仍在官方研究时限内且 W 有至少一份有效配置？"}
  R -->|是| S["先遍历整个 W 的有效 YAML 两两组合"]
  S --> T{"至少四个相同公开子样本，双方各胜至少两个，模型位置可衔接？"}
  T -->|是| U["优先交叉：继承完整拓扑，按同位置重组 agent"]
  T -->|否| V["W 按 reward 取最高的一份有效配置并变异"]
  V --> W["只变更 model_id 或 topology；新增节点用 s2 和八工具"]
  U --> X["动作前写 evidence 引用、假设、替代动作与预测"]
  W --> X
  X --> Y{"新 YAML 可解析、DAG 合法、改动范围合法？"}
  Y -->|否| Z["记录配置失败；不增加版本"]
  Y -->|是| AA["执行子 MAS，保存 evaluations/attempt_*/agent_*.json 和 candidate_solver.py"]
  AA --> AB["代码静态契约检查与官方公开检查；记录各设置、用时和失败"]
  AB --> AC["动作后补实际指标、模型/拓扑变化和父子对照"]
  AC --> K
  Z --> Q
  R -->|否| AD["记 incomplete_public；保留证据；本 seed 不跑 hidden"]
  Q -->|是| AE["六份不同有效源码进入最终公开集中审查"]
  AE --> AF["逐份核对源码哈希、来源 YAML、接口、原 selfcheck 与耗时"]
  AF --> AG["公开子样本均值、最差值、波动、两两胜负与最差退化"]
  AG --> AH["逐个去掉一个公开子样本观察诊断排序是否变化"]
  AH --> AI["写 final_generalization_review.json，标出风险与不确定性"]
  AI --> AJ["在公开 reward 近似相同时用去单样本排名破同分；冻结源码与哈希"]
  AJ --> AK["submit_hidden.py 再核六版文件、审查文件和选中源码及 YAML 哈希"]
  AK --> AL["Harbor 单次密封评测；只挂载冻结源码"]
  AL --> AM["写 summary.json、任务经验、角色观察、results.csv"]
  AM --> AN["满足公开收益与风险门槛才晋升 pool/任务/*.yaml 和 index.json"]
```

初选候选池的两个来源是 `repro/pool/rsi-exam-general/*.yaml` 和 `repro/pool/<task>/evolved_*.yaml`。后者只取当前任务此前晋升的进化 YAML；`index.json` 是溯源记录，不作为候选配置。运行脚本保留通用文件名，对任务进化文件添加 `task__` 前缀，避免同名文件覆盖，并把来源写入 `input/provenance.json`。

元模型从冻结的初选池选择两份不同父 YAML，初始未选中的通用配置**不属于 W**。选中父配置经有效公开检查后进入本轮临时池 W；本轮子 YAML 也须通过检查才进入 W。W 按 YAML 文本哈希去重，独立于 solver.py 的版本哈希。交叉在整个 W 找可比的互补对；没有满足条件的组合时，只取 W 中 reward 最高的一份有效配置进行 mutation。双向公开子样本优势只是配置关联证据，不能证明单个 agent 的因果优势。

失败尝试记录在 `attempts.json` 和 `trajectory_evidence.jsonl`，包括配置错误、节点/候选生成错误、代码接口及公开检查错误。`decision_evidence.jsonl` 分开写入事前选择依据与事后观察；元模型的变异理由另见 `mutation_rationales.jsonl`。`evaluations/<label>.yaml`、`evaluations/<label>/candidate_solver.py` 和 `visible.json` 保留每次具体配置、产物与公开分数；`candidate_manifest.json` 仅列出六份以内的不同有效 solver 源码。

只有恰好六份不同且经公开检查的源码，才运行最终公开泛化风险探索并冻结最终文件。`final_generalization_review.json` 汇集六份源码与 YAML 哈希、公开指标、各子样本、成本、最差子样本、子样本波动、两两胜负及去掉一个公开子样本后的诊断排名。先对全部六份源码建立风险画像，再选择公开 reward 最高的代码；若另有代码与最高分相差不超过 0.5%，且至少四个可比公开子样本存在，则在近似同分组中优先选去单样本后最差排名更好者。风险项和缺乏独立留出数据的不确定性同时保留，不把公开敏感性分析称为独立泛化证明。
