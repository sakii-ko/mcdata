# mcdata 架构与设计原则

本文档是仓库的长期架构约束，由 planner 维护。所有新代码必须符合这里的分层和依赖规则；如果某条规则确实挡路，先在 iteration report 里提出，由 planner 决定是否修改本文档，而不是绕过它。

## 设计哲学

1. **数据文件即接口（artifacts as contracts）**
   模块之间通过磁盘上的数据文件解耦，而不是通过函数调用链耦合：
   - action 层的输出 = trajectory JSON；
   - render 层的输出 = run dir（视频 + manifest + 日志）；
   - qa 层的输入 = run dir，输出 = QA report。
   只要契约不变，任何一层都可以整体替换（例如把 A* 策略换成 VPT policy，render 层完全无感知）。

2. **配置优先于代码（config over code）**
   新的渲染组合 = `asset_sets.yml` / `profiles.yml` 里的新条目；新的巡逻路线 = `actions.yml` 里的新 strategy 配置。只有当现有参数表达不了需求时才扩展代码，而且扩展必须是**通用参数**（例如 `waypoint_actions`），不允许把具体场景坐标写死进 Python。

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
| `mcdata.actions` | config, paths | render、qa（策略不知道渲染的存在） |
| `mcdata.render` | config, paths, packs, manifest, runlog | actions 的内部实现（只消费 trajectory JSON 文件） |
| `mcdata.qa` | config, paths（可选 numpy/Pillow） | render、actions（只消费 run dir） |
| `mcdata.manifest` / `mcdata.runlog` | paths | render、actions、qa（被依赖方，不反向依赖） |
| `mcdata.packs` / `modrinth` / `mojang` | net, paths | 上层模块 |
| `mcdata.cli` | 所有模块 | —（但只做参数解析和调用，不写业务逻辑） |

违反依赖规则的 PR 一律打回。

## 数据契约

### trajectory JSON（actions → render）

```jsonc
{
  "type": "astar_walk",          // 策略类型
  "duration_sec": 123.4,
  "route": [{"x": 0, "z": -14}], // 可选：规划路线（供可视化/QA 用）
  "events": [                    // 唯一被 replay 消费的字段
    {"t": 1.0, "key": "w", "action": "down"},
    {"t": 1.5, "mouse_dx": 540, "mouse_dy": 0, "duration": 0.35}
  ]
}
```

约束：`events` 按 `t` 非递减排序；每个 `key` 的 down/up 必须配对；同一配置生成的 JSON 必须 byte-identical（确定性，这是 N-way 渲染对齐的根基）。

### run dir（render → qa / 数据集）

```
<run_dir>/
  capture.mp4        # 24fps 录制
  manifest.json      # 见 manifest schema
  pipeline.jsonl     # 结构化日志（每行一个 JSON 事件，含 stage/ts）
  server.log         # dedicated server 日志（如启用）
```

### manifest.json（每个 run 的完整可复现描述）

必含字段：`schema_version`、`run_id`、`profile`、`mc_version`、资源清单（mods / resourcepacks / shaderpacks，含文件名 + sha256）、`world`（seed + world_state）、`trajectory`（路径 + sha256 + strategy 名 + 事件数）、`capture`（fps / size / ffprobe 实测）、`env`（hostname / DISPLAY / GL renderer / GPU）、`git`（commit + dirty）、时间戳。schema 定义放 `src/mcdata/schemas/manifest.schema.json`，测试用 jsonschema 校验。

## 测试策略分层

| 层级 | 依赖 | 覆盖内容 | 何时跑 |
|---|---|---|---|
| unit | 无（纯 Python） | A*、事件生成、manifest 构建、QA 指标、配置交叉引用 | 每次提交，`scripts/dev_check.sh` |
| integration | 网络/磁盘，无 GPU | bootstrap dry-run、trajectory 落盘、manifest 落盘 | 每个 iteration 至少一次 |
| e2e | GPU-backed display | run-matrix 真实录制 + qa-run/qa-compare | GPU display 就绪后 |

## 版本管理约定

- canonical repo：`/root/nas/bigdata1/cjw/projs/mcdata`（NAS）。`/home/chijw/workspace/projs/mcdata` 是历史遗留的手工镜像，后续如需本地盘加速应改为 `git clone`/worktree，禁止双向手工 rsync。
- 大文件（视频、jar、resource/shader pack、世界存档）永不入库；文档配图压缩到单张 <300KB。
- 身份区分：planner 用仓库默认身份 `mcdata-planner`；coder 提交前在 shell 里
  `export GIT_AUTHOR_NAME=mcdata-coder GIT_AUTHOR_EMAIL=coder@mcdata.local GIT_COMMITTER_NAME=mcdata-coder GIT_COMMITTER_EMAIL=coder@mcdata.local`。
  这样 `git log --author=mcdata-coder` / `git blame` 可以直接区分进度和归属。
- commit 前缀：`[plan]` 计划、`[arch]` 架构、`[impl]` 功能、`[test]` 测试、`[qa]` QA 工具/报告、`[fix]` 修复、`[docs]` 文档。一个任务多个小 commit，禁止大杂烩 squash。
- 分支：coder 在 `iter/NN-<slug>` 分支上工作，完成后**不自行 merge**，由 planner review 后 `merge --no-ff` 进 main 并打 `iter-NN-done` tag。
- 禁止 force push，禁止移动已有 tag。
