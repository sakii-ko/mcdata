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
| `mcdata.render` | config, paths, packs, resourcepacks, net, mojang, modrinth, manifest, runlog, settings, scene_model, actions.replay（输入回放，见注）, qa.probe（ffprobe 封装）, render.*（包内 lifecycle/scene/probe 分层） | actions 的策略实现（只消费 trajectory JSON 文件） |
| `mcdata.qa` | paths（可选 numpy/Pillow） | render、actions（只消费 run dir） |
| `mcdata.scene_model` / `mcdata.manifest` / `mcdata.runlog` / `mcdata.settings` | config（scene_model/settings）, paths（settings/manifest/runlog） | render、actions、qa（被依赖方，不反向依赖） |
| `mcdata.packs` / `modrinth` / `mojang` | net, paths（packs 另可 config, modrinth） | 上层模块 |
| `mcdata.resourcepack_format` / `mcdata.resourcepacks` | 仅标准库（resourcepacks 可依赖 resourcepack_format） | packs/render（资源格式发现、effective ZIP 规范化与双 SHA 溯源） |
| `mcdata.dataset` / `mcdata.dataset_support` | dataset 仅依赖 dataset_support；support 包内可互相依赖 | render/actions/qa（只聚合落盘的 manifest 与 QA evidence） |
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

事件语义：`key` 事件注入键盘输入；`mouse_dx` / `mouse_dy` 事件注入相对鼠标移动；`{"pause": true}` 事件只占用时间轴，用于在 waypoint 停留观察，不产生输入。

约束：`events` 按 `t` 非递减排序；每个 `key` 的 down/up 必须配对；同一配置生成的 JSON 必须 byte-identical（确定性，这是 N-way 渲染对齐的根基）。replay 对未知事件字段必须静默跳过，仅记录 replay_log 时间戳；这是 trajectory 契约的前向兼容规则。

### run dir（render → qa / 数据集）

```
<run_dir>/
  capture.mp4        # 24fps 录制
  manifest.json      # 见 manifest schema
  pipeline.jsonl     # 结构化日志（每行一个 JSON 事件，含 stage/ts）
  positions.jsonl    # server 反馈坐标/朝向及相对时间
  navigation_log.jsonl # feedback_roam 控制决策（仅闭环导航）
  server.log         # dedicated server 日志（如启用）
```

### manifest.json（每个 run 的完整可复现描述）

必含字段：`schema_version`、`run_id`、`lane`（并行 shard 标识，未分片时为 null）、`profile`、`mc_version`、资源清单（mods / resourcepacks / shaderpacks，含文件名 + sha256）、`world`（seed + world_state）、`trajectory`（路径 + sha256 + strategy 名 + 事件数）、`capture`（fps / size / ffprobe 实测）、`env`（hostname / DISPLAY / GL renderer / GPU）、`git`（commit + dirty）、时间戳。schema v2 起要求顶层 `lane` 字段；schema 定义放 `src/mcdata/schemas/manifest.schema.json`，测试用 jsonschema 校验。

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
标为 `strict_rendering_matrix`，其余组标为 `world_state_variant`。

产物 `dataset_index.json` 遵循 `src/mcdata/schemas/dataset_index.schema.json`；所有路径均为
相对 dataset 根目录的 POSIX 路径，`dataset_id` 由不含自身 ID 的规范化索引内容计算，输出
不写当前时间且稳定排序；`generator.git_commit` 单独记录索引器代码版本，不与 run manifest
里的 capture commit 混淆。缺少显式 `visual_review/review.json` 时状态最多是
`automated_pass`；只有自动闸门和人工 review 都通过才是 `accepted`。同目录的
`SHA256SUMS` 覆盖索引及批次内全部普通文件，用于回传后逐字节复验。

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
