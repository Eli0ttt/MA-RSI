# MA RSI

基于 EvoMAS 的四个 RSI-Exam 任务研究框架。元模型选择 MAS 配置并进行 prompt、model ID、tools、topology 四类中的一次变异（或 crossover）；各任务的研究 agent 生成 `solver.py`，使用公开 selfcheck 选择候选，完成后交给独立的官方 Harbor verifier。单次研究最多 6 个版本、两个父配置、mutation 概率 0.7。`repro/evolution_core.py` 提供共同的进化动作调度和证据记录；任务接口与评测器依官方任务保留各自实现。

## 目录

- `rsi_exam_tasks/<official-task>/`：四个任务相同层级。官方 `environment/`、`instruction.md`、`task.toml` 可纳入代码库；服务器本地 `tests/` 包含密封数据，整个目录被 Git 忽略。
- `repro/runner/`：唯一研究入口 `run.sh`，四任务统一调用；`continuous_*` 是连续 BBO 的专用执行与公开评测模块，`task_*` 处理其他三个任务。需要不同适配器是因为官方 solver 接口及评测过程不同，不表示 BBO 有额外的权限或版本预算。
- `repro/seeds/<task>/`：迁移时的两个父 YAML 快照。`repro/pool/<task>/` 是服务器运行时更新的两个谱系槽，不纳入 Git。
- `repro/memory/`、`repro/skill/`、`repro/runner/state_current/`、`repro/runner/outputs/`：运行时知识、状态和轨迹，不纳入 Git。
- `vendor/meta_engine/` 与 `vendor/agent_engine/`：基于 EvoMAS 的两个本地 fork，保留原 LICENSE 和来源 README；研究入口只依赖其 `src/`。
- `submission/agent.py`：将公开胜出的 solver 交给 Harbor 的无模型生成提交代理，不属于早期 DSH 单/双模型研究方案。

## 迁移与安全边界

执行附带的 `migrate.py`，从原项目根目录读取 BBO 的 `task.toml`、`environment/`、`tests/`，并从 `rsi_exam_tasks/` 读取另三个任务；最终四项均放在新仓库的 `rsi_exam_tasks/<task>/` 下。BBO 根目录版 `task.toml` 不固定已构建镜像 ID，和其他三项一样由 Harbor 构建任务镜像；其余 BBO 任务文件与旧 prebuilt 副本字节一致。迁移同时复制服务器本地环境文件、父池和共享知识；原项目不被修改。新 `state_current` 从空 manifest 开始；旧轨迹的 `run` 字段会转为 `legacy/...`，只保留作为证据标识，正常运行不会再访问旧项目。父池的 YAML 可能是旧实验晋升的配置，不等于原始论文种子；做严格的纯起点实验应另建干净状态。

仓库不应上传 API key、`tests/` 下的密封评测数据、运行轨迹或持久状态。部署前执行 `git status --short` 与 `git ls-files` 检查。新机器须从获准的官方任务包恢复服务器本地 `tests/`，才能进行正式 Harbor 评测；不要以公开自检冒充正式成绩。

## 启动

```bash
ROOT=/data/user/wenxinyi/experiments/MA-RSI
cd "$ROOT"
# 首次克隆且尚无本地状态时：
cp env/experiment.env.example env/experiment.env
chmod 600 env/experiment.env
# 在 env/experiment.env 设置 OPENAI_API_KEY 与本机路径，并恢复官方本地 tests。
python repro/pool_manager.py init --root "$ROOT"
python repro/runner/state.py init --root "$ROOT" --state "$ROOT/repro/runner/state_current"
# 现有迁移目录已由 migrate.py 初始化，无须再次 init。
bash repro/runner/run.sh "$ROOT" "$ROOT/repro/runner/state_current" bbo original 72
```

先确保本机 Harbor、Docker、四任务研究依赖已安装，任务镜像已按官方 Dockerfile 构建，且环境文件中指定的 BBO 公开评测容器在运行。其余任务将 `bbo` 换成 `inventory`、`graph`、`qec`；`run.sh` 在同一路径解析每个 task 的 `task.toml`。终端使用的 agent tools 与最终提交的 solver 导入白名单是两个不同边界，不能通过修改研究工具扩张官方 solver 权限。

官方规则：[BBO](https://rsi-exam.ai/tasks/bbo_noisy_continuous_v1.html)、[inventory](https://rsi-exam.ai/tasks/bbo_simopt_inventory_v1.html)、[graph](https://rsi-exam.ai/tasks/johnson1991_leighton_graph_coloring.html)、[QEC](https://rsi-exam.ai/tasks/qec_decoder_arena.html)。
