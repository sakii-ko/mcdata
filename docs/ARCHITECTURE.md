# mcdata 架构与设计原则

本文档是仓库的长期架构约束。所有新代码必须符合这里的分层和依赖规则；如果某条规则
确实挡路，先在 iteration report 里给出证据与替代设计，再显式修改本文档，而不是绕过它。

## 设计哲学

1. **数据文件即接口（artifacts as contracts）**
   模块之间通过磁盘上的数据文件解耦，而不是通过函数调用链耦合：
   - action 层的输出 = trajectory JSON；
   - render 层的输出 = run dir（视频 + manifest + 日志）；
   - qa 层的输入 = run dir，输出 = QA report。
   只要契约不变，任何一层都可以整体替换（例如把 A* 策略换成 VPT policy，render 层完全无感知）。

2. **配置优先于代码（config over code）**
   新的渲染组合 = `asset_sets.yml` / `profiles.yml` 里的新条目；新的巡逻路线 = `actions.yml` 里的新 strategy 配置。只有当现有参数表达不了需求时才扩展代码，而且扩展必须是**通用参数**（例如 `waypoint_actions`），不允许把具体场景坐标写死进 Python。
   shader 的非默认开关写在 profile 的 `shader_options` 映射中；bootstrap 只在恰好选中一个
   shader ZIP 时，按键名排序生成 `shaderpacks/<exact ZIP filename>.txt`，禁止依赖 GUI 状态。

3. **纯逻辑与副作用分离（pure core, effectful shell）**
   路径规划、事件序列生成、manifest 构建、QA 指标计算都是纯函数，不碰网络/文件/子进程；I/O 和进程管理收敛在薄 wrapper 里。这是可测试性的来源：纯逻辑用单元测试覆盖，不需要 GPU、不需要启动游戏。

4. **每个阶段可独立运行**
   每个阶段都有独立 CLI 子命令（`make-trajectory`、`bootstrap`、`run`、`qa-run`、`viz-trajectory`……），可以单独执行、单独调试。`run-matrix` 只是编排器，不包含任何独有逻辑。

5. **一切产出可验证（evidence-based review）**
   每个 run 必须留下 manifest + 结构化日志；每个功能必须留下测试或可视化产物。review 看证据（测试输出、QA 报告、轨迹图、抽帧），不看口头描述。

## 模块分层与依赖规则

```
                 ┌─────────────┐
                 │   cli.py    │  薄分发层，不含业务逻辑
                 └──────┬──────┘
        ┌───────────┬───┴────────┬────────────┐
   ┌────▼────┐ ┌────▼─────┐ ┌────▼────┐ ┌─────▼─────┐
   │ actions │ │  render  │ │   qa    │ │ manifest/ │
   │         │ │          │ │         │ │ runlog    │
   └────┬────┘ └────┬─────┘ └────┬────┘ └─────┬─────┘
        │      ┌────▼─────┐      │            │
        │      │  packs/  │      │            │
        │      │ modrinth │      │            │
        │      └────┬─────┘      │            │
        └───────────┴─────┬──────┴────────────┘
                    ┌─────▼──────┐
                    │ config /   │  基础层：yml 加载、路径、
                    │ paths / net│  下载、schema 定义
                    └────────────┘
```

依赖规则（import 白名单）：

| 模块 | 允许 import | 明确禁止 |
|---|---|---|
| `mcdata.actions`（strategies/viz） | config, paths, scene_model | render、qa（策略不知道渲染的存在） |
| `mcdata.actions.replay` | —（零 mcdata 依赖） | 一切 mcdata 模块 |
| `mcdata.render` | config, paths, packs, resourcepacks, net, mojang, modrinth, manifest, runlog, settings, scene_model, action_curriculum, actions.replay（输入回放，见注）, qa.probe（ffprobe 封装）, render.*（包内 lifecycle/scene/probe 分层） | actions 的策略实现（只消费 trajectory JSON 文件） |
| `mcdata.qa` | paths（可选 numpy/Pillow） | render、actions（只消费 run dir） |
| `mcdata.scene_model` / `mcdata.manifest` / `mcdata.runlog` / `mcdata.settings` | config（scene_model/settings）, paths（settings/manifest/runlog） | render、actions、qa（被依赖方，不反向依赖） |
| `mcdata.terrain` | config, scene_model（纯校验/哈希；只读 canonical registry 与所绑定配置） | render、actions、qa、manifest、dataset（Phase 1 尚未 wiring） |
| `mcdata.action_curriculum` | 仅标准库 | 所有上层模块（L1–L4 taxonomy、证据归纳与纯验证） |
| `mcdata.packs` / `modrinth` / `mojang` | net, paths（packs 另可 config, modrinth） | 上层模块 |
| `mcdata.resourcepack_format` / `mcdata.resourcepacks` | 仅标准库（resourcepacks 可依赖 resourcepack_format） | packs/render（资源格式发现、effective ZIP 规范化与双 SHA 溯源） |
| `mcdata.dataset` / `mcdata.dataset_support` | action_curriculum；dataset 另依赖 dataset_support；support 包内可互相依赖 | render/actions/qa（只聚合落盘的 manifest 与 QA evidence） |
| `mcdata.cli` | 所有模块 | —（但只做参数解析和调用，不写业务逻辑） |

注：`actions.replay` 是运行时输入注入后端（消费 trajectory JSON、驱动 X 输入），允许被 render 调用，但它自身必须保持零 mcdata 依赖；长期可能迁出 actions 包成为独立模块。

白名单由 `scripts/check_standards.py` 机械执行（dev_check.sh 的一部分），与本表不一致时以先修文档、再改 checker 为流程。函数/文件级行为规范（环境变量纪律、错误处理、日志要求等）见 `docs/CODE_STANDARDS.md`。违反依赖规则的 PR 一律打回。

## 数据契约

### trajectory JSON（actions → render）

```jsonc
{
  "type": "astar_walk",          // 策略类型
  "duration_sec": 123.4,
  "route": [{"x": 0, "z": -14}], // 可选：规划路线（供可视化/QA 用）
  "events": [                    // open-loop replay 消费的字段
    {"t": 1.0, "key": "w", "action": "down"},
    {"t": 1.5, "mouse_dx": 540, "mouse_dy": 0, "duration": 0.35},
    {"t": 2.2, "pause": true, "duration": 2.0}
  ]
}
```

`feedback_roam` 是第二种执行契约：`events=[]`，并额外携带确定性的闭合 `route`、
`planning` 和 `navigation`。render 以 server 的 `Pos` / `Rotation` 回执为反馈，使用正常
键鼠输入追踪路线，输出 `navigation_log.jsonl`；它不得 teleport，反馈陈旧、超出高度、
越过硬偏差或恢复预算耗尽时必须终止录制。相同 trajectory SHA 表示相同路线与控制策略，
不表示不同画质 profile 会产生 byte-identical 的自适应输入序列。

事件语义：`key` 事件注入键盘输入；`mouse_dx` / `mouse_dy` 事件注入相对鼠标移动；`{"pause": true}` 事件只占用时间轴，用于在 waypoint 停留观察，不产生输入。高阶动作由事件的
`semantic_action` 明确标注；taxonomy v1 当前允许 `deliberate_jump`、
`deterministic_block_placement` 和 `controlled_combat`。后两种只有契约名、尚无执行器，replay
必须跳过输入并记录 `execution_status=unsupported_contract_only`，不能把“已调度”伪装成“已执行”。

约束：`events` 按 `t` 非递减排序；每个 `key` 的 down/up 必须配对；同一配置生成的 JSON 必须 byte-identical（确定性，这是 N-way 渲染对齐的根基）。replay 对未知事件字段必须静默跳过并记录 replay log；这是 trajectory 契约的前向兼容规则。每个 dispatch 还必须记录 `executed`、`non_input` 或 `unsupported_contract_only`，数据集归纳器只相信与事件 primitive 一致的状态。

### Action curriculum v1

四个 bucket 固定为累积能力集合：`l1= navigation`、`l1_l2= navigation + deliberate_jump`、
`l1_l2_l3= ... + deterministic_block_placement`、`l1_l2_l3_l4= ... + controlled_combat`。
trajectory 可用严格的 `action_curriculum={taxonomy_version, planned_level, capabilities}` 声明
高阶计划；字段缺失的现有 A*/roam/feedback trajectory 兼容推导为 L1。`deliberate_jump` 必须是
显式标注的单次 Space tap。

run manifest 的顶层 `action_curriculum` 同时记录 planned level/capabilities、逐类 observed semantic
counts、observed level、bucket、动作日志哈希以及 `controller_recovery_counts`。open-loop 的 observed
只从与完整 trajectory 逐事件一致的 `replay_log.jsonl` 归纳；feedback 的 L1 输入从
`navigation_log.jsonl` 的 control 决策归纳。navigator 为脱困执行的一次 Space + S 只记为
`attempts/jump_taps/reverse_moves`，永不记成 deliberate jump。

accepted 数据 fail closed：缺日志、日志与 trajectory 不一致、manifest 计数被篡改、能力列表非
累积、出现未声明语义动作、或 planned L2–L4 没有观察到该最高等级动作都会拒绝。dataset index
为每个 episode 原样保存该记录，并在 `action_buckets` 中生成四组精确的排序 ID/count；它不复制
capture，训练端可据此按比例采样。

迁移边界按 manifest 版本机械执行。新录制一律写 v3，v3 的 claim 缺失或为 null 时 dataset
loader 直接拒绝，不允许 fallback。历史 v2 若已有 claim，必须按当前规则与日志重新计算结果完全
一致，并在 episode 标 `action_curriculum_source=manifest`；只有历史 v2 缺 claim 时，才允许从
已由 manifest SHA 绑定的 trajectory 与完整 `replay_log.jsonl` / `navigation_log.jsonl` 严格派生，
标 `derived_legacy_replay`。旧 replay 可缺后来新增的 `execution_status`，但事件对象、顺序、数量和
scheduled time 仍须与 trajectory 逐项一致；缺日志或任一篡改继续 fail closed。dataset index 本身
仍为 schema v2，其 episode manifest artifact 明确允许 v2/v3，并显式保存上述 source 枚举。

训练侧的 `curriculum_plan.json` 是由 accepted dataset index v2 派生的只读 schedule artifact，schema
位于 `src/mcdata/schemas/curriculum_plan.schema.json`。采样单位固定为 edit pair；pair 两端必须共享
episode 证据声明的 action bucket，禁止按 profile 名推断。四桶比例用最大余数法得到精确整数计数，
桶内与全局展开均使用显式 seed/epoch 和 SHA-256 固定排序，不读取当前时间。plan 同时绑定逻辑
`dataset_id`、源 index 文件 SHA 和自身规范 JSON `plan_id`，只引用 pair ID，不复制 capture。可选
训练 split 只能以显式 pair allowlist 进入并原样记录 lineage；工具不假装已有隐式 split。

### run dir（render → qa / 数据集）

```
<run_dir>/
  capture.mp4        # 24fps 录制
  manifest.json      # 见 manifest schema
  pipeline.jsonl     # 结构化日志（每行一个 JSON 事件，含 stage/ts）
  positions.jsonl    # server 反馈坐标/朝向及相对时间
  replay_log.jsonl   # open-loop 每个 trajectory event 的 dispatch 证据
  navigation_log.jsonl # feedback_roam 控制决策（仅闭环导航）
  server.log         # dedicated server 日志（如启用）
```

### manifest.json（每个 run 的完整可复现描述）

必含字段：`schema_version`、`run_id`、`lane`（并行 shard 标识，未分片时为 null）、`profile`、`mc_version`、资源清单（mods / resourcepacks / shaderpacks，含文件名 + sha256）、`world`（seed + world_state）、`trajectory`（路径 + sha256 + strategy 名 + 事件数）、`action_curriculum`、`capture`（fps / size / ffprobe 实测）、`env`（hostname / DISPLAY / GL renderer / GPU）、`git`（commit + dirty）、时间戳。schema v2 起要求顶层 `lane`；schema v3 起要求顶层 `action_curriculum`。现行 schema 定义放 `src/mcdata/schemas/manifest.schema.json`，测试用 jsonschema 校验。

open-loop N-way cohort 的 invariant 是相同 Minecraft 版本、代码 commit、trajectory sha256、world seed、
完整 world-state、scene 和采集规格。有意改变天气或时间的 profile 必须归入单独的
world-state variant，不能与严格 rendering-only cohort 混称；dataset index/report 必须显式记录该分组。
闭环 `feedback_roam` 只能称为 `policy_aligned_rendering_matrix`：路线、策略、seed 和世界状态
一致，但每档画质允许由反馈产生不同的微小纠偏；compare 必须基于时间对齐的位置轨迹并
报告差异，不能宣称 exact open-loop action alignment。

### accepted dataset（run / QA → 可发布批次）

`mcdata dataset-index` 只扫描 dataset 根目录的直接子目录，并把含 `manifest.json` 的目录
视为 episode。它逐文件核对 manifest、视频、trajectory、positions、单路 QA、严格 cohort
compare、资源包 runtime gate 与人工视觉 review；任何 profile 缺失/重复或 provenance 漂移
都会 fail closed。完整 world-state 的规范 JSON SHA-256 决定 cohort，primary profile 所在组
open-loop primary 组标为 `strict_rendering_matrix`，闭环反馈 primary 组标为
`policy_aligned_rendering_matrix`，其余组标为 `world_state_variant`。

产物 `dataset_index.json` 遵循 `src/mcdata/schemas/dataset_index.schema.json`；schema v2 起，
`dataset-index` 必须通过 `--pair-manifest` 绑定 dataset 根目录内的 edit-pair manifest（输入
schema 为 `src/mcdata/schemas/edit_pair_manifest.schema.json`）。输入的每条 pair 只声明自然语言
`prompt`、`source_episode`、`target_episode` 和唯一 `edit_axis`；索引器从两端已通过 QA 的
episode manifest 重新计算 `axis_values` 和 invariants，绝不采信调用方提交的差分结论。当前
单轴集合为 `material_style`、`shader_quality`、`time_of_day`、`weather`、`snow_weather`；后者
要求两端共享显式 `world.state.biome={id, precipitation: "snow"}`，底层 clear/rain 在索引中
规范化为 clear/snow。一个 accepted episode 必须至少出现在一条 pair 中；同一 target 不能
被不同 source/axis 冲突占用。

每条 pair 强制共享 Minecraft 版本、capture commit、world seed/profile、scene、player spawn、
trajectory contract 和 capture settings（只排除部署路由用的 display）。材质轴只允许实际
resource-pack 内容哈希变化；光影轴要求无 shader 的 source 到启用 shader 的 target；时间轴
只接受 `noon`↔`midnight` 或 `noon`↔精确 numeric tick `12000`；索引中的后者规范化为
`golden_hour`，其他任意 tick 一律拒绝。普通天气轴只接受非雪地 clear/rain。雪地 profile 的
`biome.regions` 会在 scene 建成后通过分块 `fillbiome` 真正应用，每块不超过服务端 32768 block
限制；server readiness gate 把这些命令和 126 个 scene fill/setblock 一起计数，并等待 26.2
`fillbiome` 成功回执后才允许继续，因此雪地 profile 的期望 mutation receipt 是 `126+5=131`。
clear/snow 两端必须共享逐字节相同的 biome id、降水语义和 regions。mods、client options、
其余 world state、未声明的渲染轴或任一上述
invariant 发生变化都会 fail closed。这样 time/weather episode 可以属于 world-state variant，
但 pair 本身仍保持可机器证明的单变量 edit 契约。

所有路径均为相对 dataset 根目录的 POSIX 路径，`dataset_id` 由不含自身 ID 的规范化索引内容
计算，输出不写当前时间且稳定排序；`generator.git_commit` 单独记录索引器代码版本，不与 run
manifest 里的 capture commit 混淆。缺少显式 `visual_review/review.json` 时状态最多是
`automated_pass`；只有自动闸门和人工 review 都通过才是 `accepted`。同目录的
`SHA256SUMS` 覆盖索引、pair manifest 及批次内全部普通文件，用于回传后逐字节复验。

## 测试策略分层

| 层级 | 依赖 | 覆盖内容 | 何时跑 |
|---|---|---|---|
| unit | 无（纯 Python） | A*、事件生成、manifest 构建、QA 指标、配置交叉引用 | 每次提交，`scripts/dev_check.sh` |
| integration | 网络/磁盘，无 GPU | bootstrap dry-run、trajectory 落盘、manifest 落盘 | 每个 iteration 至少一次 |
| e2e | GPU-backed display | run-matrix 真实录制 + qa-run/qa-compare | GPU display 就绪后 |

## 版本管理约定

- canonical repo：`/root/nas/bigdata1/cjw/projs/mcdata`（NAS）。
  `/home/chijw/workspace/projs/mcdata` 当前是同一目录的路径别名（同设备、同 inode），
  不应视作第二份镜像或做双向 rsync。
- 大文件（视频、jar、resource/shader pack、世界存档）永不入库；文档配图压缩到单张 <300KB。
- 身份署名（2026-07-08 第二次修订，现行规则）：所有 commit 的 author/committer 统一为 **`sakii-ko <chijw2004@outlook.com>`**（仓库级 git config 已设，任何一方不要再改 `user.*`）。角色归属改由 commit message 承载：
  - commit 前缀照旧（`[plan]`/`[impl]`/…）；
  - 消息末尾必须带角色 trailer：`Role: planner` 或 `Role: coder`（放在 Claude 的 `Co-Authored-By` 之前）；
  - 用 `git log --grep "Role: coder"` 区分进度/blame/credit。
  历史勘误（不重写）：`iter-01-done` 之前的提交用的是 `mcdata-planner`/`mcdata-coder` 双身份方案，其中 `5cd13d1`、`efb2980`、`8960a68` 实为 planner 所写但署名 coder。
- commit 前缀：`[plan]` 计划、`[arch]` 架构、`[impl]` 功能、`[test]` 测试、`[qa]` QA 工具/报告、`[fix]` 修复、`[docs]` 文档。一个任务多个小 commit，禁止大杂烩 squash。
- 分支：从 main 创建范围明确的 `iter/NN-<slug>` / `fix/<slug>` 分支。自 2026-07-10
  起同一 executor 可完成实现、证据 review 与 `merge --no-ff`；`Role: coder/planner` trailer
  保留为工作类型审计，不再代表必须人工交接的两个人。
- 禁止 force push，禁止移动已有 tag。
- **远端仓库（2026-07-08 起）**：`origin = github-mcdata:sakii-ko/mcdata.git`（`github-mcdata` 是 `~/.ssh/config` 里的别名，走 `/home/chijw/.ssh/id_github`；不要把 URL 改回 `git@github.com:` 形式，默认 key 无权限）。推送纪律：
  - 工作分支每完成一个可回退任务后 push；每次 merge / 打 tag 后 push `main` 和 `--tags`。
  - 禁止 push 任何大文件（视频/jar/pack），R17 照常适用。
