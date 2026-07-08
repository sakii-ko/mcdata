# PLAN.md — mcdata 协作计划

本文件是 planner 和 coder 之间的工作契约，由 planner 维护。coder 只做本文件"当前 iteration"里列出的任务；做完写 report，等 planner review。

配套文档：
- `docs/ARCHITECTURE.md` — 架构分层、依赖规则、数据契约、git 约定（**先读这个再动代码**）。
- `docs/CODE_STANDARDS.md` — 硬性代码规范（环境变量纪律、副作用分离、错误处理、日志要求、依赖纪律）。其中 [checker] 条目由 `scripts/check_standards.py` 机械执行，**任何时候都必须保持退出码 0**。
- `PROGRESS.md` — 长期交接文档，记录已完成能力和远端机器状态。
- `docs/iterations/ITER-NN-report.md` — coder 的完成汇报（coder 写）。
- `docs/iterations/ITER-NN-review.md` — planner 的 review 结论（planner 写）。

## 协作流程

1. planner 在本文件写清当前 iteration 的任务和验收标准，打 tag `iter-NN-planned`。
2. coder 从 main 切分支 `iter/NN-<slug>`，按任务顺序执行；每个任务至少一个独立 commit，前缀见 ARCHITECTURE.md；提交身份用 `mcdata-coder`（export GIT_AUTHOR_NAME/GIT_COMMITTER_NAME 等，见 ARCHITECTURE.md）。
3. coder 完成后写 `docs/iterations/ITER-NN-report.md`（要求见文末），**不自行 merge**。
4. planner review 代码 + 复跑验收命令，写 review 文件，merge --no-ff 进 main，打 `iter-NN-done`。

## 项目北极星目标（不变部分，摘自 PROGRESS.md）

对同一个 Minecraft 世界、同一条 action 轨迹，采集 N-way（当前 17 个 matrix profile）渲染差异视频数据集：底层世界内容与行动完全一致，只改变材质包/光影/水反/emissive 等渲染质量。录制 24fps、无黑边、保留 HUD、无干扰 toast、不录加载过程；action 在地面真实游走（A* 规划）；支持天气/时间/夜晚亮度控制。最终渲染设备是远端 4090 / L40S（GPU-backed X display），本机只能做逻辑验证和 Xvfb 软渲染 smoke test。

数据可用性的根基是**可复现与可验证**：每个 run 必须能从 manifest 完整还原（哪个 profile、哪些资源、哪条轨迹、什么世界状态），每批数据必须有自动 QA 证据（帧率/尺寸/黑边/对齐）。

## 当前状态摘要

- 代码链路（bootstrap → server/scene → launch → join-gate → warmup → capture → replay）已跑通，见 PROGRESS.md。
- **原硬阻塞已解除（2026-07-08）**：管理员已在 4090 安装 `mcdata-xorg` systemd 服务，`DISPLAY=:77` 为 NVIDIA RTX 4090（direct rendering: Yes，驱动 550.67）。planner 已用 baseline 代码在真 GPU 上完成 20s smoke run（`matrix_low` + `ground_astar_loop`）：capture.mp4 1280x720 / 24fps / 480 帧整，Sodium 识别到 NVIDIA 适配器，56 mods 正常加载，run 结束无残留进程。远端 run dir：`/home/lyf/mcdata/runs/20260708T052709Z_matrix_low`。
  - 注意：GPU 0 与其他用户的训练任务共享（约 9GB 显存被占）。ITER-02 大规模采集前评估换空闲卡（重装服务改 `MCDATA_GPU_INDEX` 即可）。
- 本 iteration 仍按原计划做**不依赖 GPU、本机可完整验证**的工程化任务：manifest/QA/测试是 GPU 放量采集的前置条件，缺了它们采回来的数据无法验收。ITER-01 merge 后立即启动 ITER-02（真 GPU 3-way → 17 全矩阵）。
- **L40S 单卡已打通（2026-07-08）**：`ssh l40s` 是一个仅有 compute 能力的容器（root 权限，driver 580.173.02，单卡 46GB 空闲）。X server 侧 NVIDIA 模块在镜像里是空壳 stub 且被 bind-mount 锁死，planner 通过"提取同版本 deb 模块到 `/opt/nvidia-xorg` + ModulePath 前置"绕过，`:77` 已是 NVIDIA L40S renderer。全过程固化为 `scripts/l40s_container_gpu_display.sh`（install/start/verify 三步，幂等）——**容器重建后跑一遍即可恢复，8 卡机器上每卡换 `MCDATA_GPU_INDEX`/display 重复执行即可**。注意 L40S 属虚拟显示模式，Xorg 配置禁用 `UseDisplayDevice "None"` 选项（脚本已处理）。
- 仓库已初始化 git（`v0.0-baseline`）。canonical repo 是 NAS 路径；`/home/chijw/workspace/projs/mcdata` 是历史手工镜像，先不要动它（planner 后续处理）。

## 渲染主机与数据存放政策（2026-07-08 起）

**渲染主机是算力，不是存储。**

- 每批采集结束后，runs 立即回传 local NAS：`scripts/pull_runs_from_remote.sh <host> <remote_runs_dir> [--purge]`，落到 `$MCDATA_OUTPUT_DIR/remote_<host>/`。脚本做二次 rsync 零传输校验，通过后才允许 `--purge` 清远端；远端有活跃 pipeline 时拒绝 purge。
- **4090**：`/home` 已 97% 满，跑完即回传 + purge。2026-07-08 首次演练完成：24 个历史 run（48MB）全部拉回 `/root/nas/bigdata1/tmp/mcdata/runs/remote_4090/`，二次校验零差异，远端 runs 已清空。
- **L40S**：容器自带 298T CephFS（`/root/nas/bigdata1`，与 local 的同名路径**不是**同一个存储）。`MCDATA_TMP_ROOT=/root/nas/bigdata1/tmp/mcdata`，runs 可先囤在容器侧大盘，批次归档时再回传 local NAS。容器根分区只有 50G overlay，**任何大文件都不要落在 `/root` 下**（`mcdata_env.sh` 会误选 `/root/mas`，在 l40s 上必须显式 export `MCDATA_TMP_ROOT`）。

---

## ITER-01：可验证性基建（当前 iteration）

分支名：`iter/01-foundations`。执行顺序 T0 → T1 → T2/T3/T4（后三个相互独立，顺序随意）。

### T0 — 开发工具链（前置，小）

- `pyproject.toml` 增加：
  - `[project.optional-dependencies]`：`dev = ["pytest>=8", "jsonschema", "ruff"]`，`qa = ["numpy", "Pillow", "matplotlib"]`。
  - `[tool.pytest.ini_options]`：`pythonpath = ["src"]`，`testpaths = ["tests"]`。
- 新建 `scripts/dev_check.sh`：依次跑 `python -m compileall -q src`、`python3 scripts/check_standards.py`、`ruff check src tests`、`pytest -q`，任一失败即非零退出。用 `.venv/bin/python`。（`check_standards.py` 已由 planner 提供，勿改其规则；误报或规则要调整时在 report 里提。）
- 安装：`.venv/bin/pip install -e '.[dev,qa]'`（网络装不上某个包就在 report 里说明并降级处理，不要卡死）。

**验收**：`bash scripts/dev_check.sh` 退出码 0，输出贴进 report。

### T1 — actions 纯化 + 策略注册表 + 单元测试

目的：把"纯逻辑与副作用分离"和"golden 锁定确定性"落到 actions 层（`src/mcdata/actions/strategies.py`）。

- 重构（行为必须完全不变）：
  - 拆出纯函数 `build_trajectory(name: str, spec: dict) -> dict`，不碰文件系统；`generate_strategy` 保持现有签名，内部变成 load config → build → 写文件。
  - `strategies.py:19-34` 的 if/elif 分发改成模块级注册表 `STRATEGY_BUILDERS: dict[str, Callable]`，后续外部 policy adapter 可以注册新类型而不改分发代码。
- 新建 `tests/`，覆盖：
  - `test_astar.py`：`_astar` 绕开 blocked、越界不可走、不可达时 raise、路径首尾正确；`_points_in_rect` 边界含端点。
  - `test_route_segments.py`：`_route_segments` 同向合并、`_shortest_turn`（如 350→10 应为 +20）、`_turn_steps`（>90 度拆两半）。
  - `test_trajectory_contract.py`：对 `configs/actions.yml` 里每个非 external 策略执行 `build_trajectory`，断言 events 按 `t` 非递减、key down/up 配对、同配置两次生成结果相等（确定性）；`ground_astar_loop` 的 route 不进 blocked_rects、不越 bounds。
  - `test_golden_trajectories.py`：把每个策略的生成结果存为 `tests/golden/<name>.json`（**重构前的输出**），测试断言重构后 byte-identical。生成 golden 的方法：在动 strategies.py 之前先提交 golden 文件（这一步单独一个 commit，`[test] add golden trajectories from baseline behavior`），再做重构 commit——这样 git 历史本身就是行为等价的证据。
  - `test_configs.py`：三个 yml 交叉引用完整性——每个 profile 的 `asset_set` 存在于 asset_sets.yml；所有 `matrix_` profile 的 `world_profile` 都是 `render_matrix_base` 且 `server_port` 一致；actions.yml 每个策略的 `type` 都在注册表里。
  - `test_options.py`：`write_options` 输出包含全部 QUIET_CAPTURE_OPTIONS 键、resourcePacks 格式正确；`write_iris_config` 有/无 shaderpack 两种情况。

**验收**：`pytest -q` 全绿；golden 覆盖所有非 external 策略；重构 commit 与 golden commit 分离。

### T2 — run manifest + 结构化日志 + 溯源

目的：每个 run 落盘完整可复现描述 + 机器可读日志。契约见 `docs/ARCHITECTURE.md` 的 manifest 一节。

- 新模块 `src/mcdata/manifest.py`：
  - 纯函数 `build_run_manifest(...)`，输入全部显式传参（profile dict、资源清单、trajectory 路径+sha256、capture 设置、ffprobe 结果、env 信息、git commit/dirty），输出 dict；单独的 `write_run_manifest(run_dir, manifest)` 落盘为 `manifest.json`。
  - schema 文件 `src/mcdata/schemas/manifest.schema.json`，测试里用 jsonschema 校验。
- 新模块 `src/mcdata/runlog.py`：`RunLogger`，同时输出 console 和 `<run_dir>/pipeline.jsonl`（每行 `{"ts": ..., "stage": ..., "event": ..., **detail}`）。stage 取值如 `bootstrap/server/launch/join/warmup/capture/replay/teardown`。
- pipeline 接入（`src/mcdata/render/pipeline.py`）：
  - 关键节点全部走 RunLogger：server start、player join、apply_join_state、warmup、capture start/stop（含完整 ffmpeg 命令）、replay start/end、各进程退出码、terminate。
  - 散落的 `MCDATA_CAPTURE_SIZE / MCDATA_CAPTURE_FPS / MCDATA_CAPTURE_DESKTOP / MCDATA_HIDE_HUD / MCDATA_VIEW_SETTLE_SEC / MCDATA_CAPTURE_READY_DELAY` 收敛成 `CaptureSettings.from_env(profile)` dataclass（一次解析、显式传参、整体写进 manifest）。dataclass 放**新文件 `src/mcdata/settings.py`**——这是 CODE_STANDARDS R2 规定的 env 边界文件，放 pipeline.py 里会被 check_standards 标为 baseline 违规。
  - trajectory JSON 复制一份到 run_dir 内并记 sha256（当前 `runs/trajectories/<strategy>.json` 会被后续运行覆盖，run 内拷贝保证溯源）。
  - run 结束时写 manifest.json；dry-run 也写（capture/ffprobe 字段为 null）。
  - `run-matrix`（`src/mcdata/cli.py`）：game_version 在矩阵开始时解析一次并透传给每个 profile 的 bootstrap/launch，避免矩阵跑到一半上游发新版导致版本漂移；解析结果记入每个 manifest。
- replay 观测（`src/mcdata/actions/replay.py`）：每个事件记录 scheduled_t 与 actual_t（monotonic 差值）到 `<run_dir>/replay_log.jsonl`（run_dir 通过参数传入，保持 replay 不依赖 render 模块）；`_keycode` 查不到键时打 warning 而不是静默丢弃。
- ffprobe 封装放 `src/mcdata/qa/probe.py`（T3 也用，谁先做谁建，注意 qa 包不 import render——render 调 probe 是允许方向）。

**验收**：
- manifest 纯函数单测 + schema 校验通过；CaptureSettings 单测（env 设置/未设置/非法值）。
- `python3 scripts/check_standards.py` 无 FAIL 且 **R2 baseline 清零**（pipeline.py 不再直接读 env；随后把 checker 里 `ENV_TEMP_BASELINE` 的 pipeline 条目删除——这是唯一允许 coder 改 checker 的操作）。
- 集成验证：若本机 Xvfb 可用（`mcdata doctor` 确认），跑 `run --profile matrix_low --with-server --replay-actions --capture --duration 10`，产出 manifest.json / pipeline.jsonl / replay_log.jsonl；不可用则至少 dry-run 产出 manifest。样例 manifest 拷到 `docs/examples/run_manifest_example.json` 入库。

### T3 — QA 工具（离线，用现有录像验证）

目的：把"抽帧人眼检查"变成可重复执行的命令。新包 `src/mcdata/qa/`，**禁止 import render/actions**，输入只有 run dir / 视频文件。

- 结构：`probe.py`（ffprobe json 封装）、`frames.py`（ffmpeg 均匀抽 N 帧）、`metrics.py`（纯 numpy 函数）、`report.py`（组装 json+md+图）。
- `mcdata qa-run <run_dir|video> [--frames 12]`：
  - ffprobe 校验：fps==24、分辨率、时长、编码。
  - 黑边检测：每帧四边 8px 带的亮度均值/方差，低于阈值判黑边（阈值做成参数）。
  - 亮度统计：灰度 p5/p50/p95 + 直方图（夜晚可用性判据：p50 过低报 warning）。
  - 产出 `qa_report.json`、`qa_report.md`、`contact_sheet.jpg`（Pillow 拼 3x4 网格，jpg quality~80）。
- `mcdata qa-compare <dirA> <dirB> [...]`：
  - 相同 timestamp 抽帧，缩到 64x36 灰度，两两算 zero-mean NCC（手写 numpy，不引 scipy）；相似度高 = 内容/机位对齐、只有渲染不同，这正是 N-way 数据集的核心验收指标。
  - 产出并排对比图 + markdown 表格（每 timestamp 一行，NCC 值 + 缩略图）。
- `metrics.py` 单测：numpy 合成图像（纯黑边框/无边框/加噪、平移图像对）验证黑边检测和 NCC 方向正确。
- 真实数据验证：对 `runs/screen_recordings/matrix_low_ground_astar_final_20260707T173901`、`matrix_low_ground_astar_stable_24fps_20260707T170812`、`matrix_emissive_makeup_nochat_20260707T171753` 跑 qa-run；对前两个（同策略不同 run）跑 qa-compare。报告 md + 压缩图入 `docs/qa_samples/`（单图 <300KB）。

**验收**：单测全绿；三份 qa_report + 一份 compare 报告入库；`qa` 包无 render/actions import（可在 test_configs.py 里加一条 import 检查断言）。

### T4 — 路线扩展 + 轨迹可视化

目的：不启动游戏就能 review 路线；用配置（而非新代码）扩出贴近水面/玻璃/光源的采集路线。

- `mcdata viz-trajectory <traj.json> --out <png>`（新 CLI，另可选 `--spec-strategy <name>` 直接从 actions.yml 读 spec 画禁区）：俯视图画 bounds 边框、blocked_rects 半透明填充、blocked 散点、route 折线 + 起点/终点标记 + goals 序号。matplotlib Agg backend。实现放 `src/mcdata/actions/viz.py`（只依赖 trajectory JSON 和 spec dict）。
- astar_walk 通用扩展：spec 支持 `waypoint_actions`（列表，形如 `{"at": [x, z], "pause_sec": 2.0, "look_dy_px": 40}`）——到达对应 goal 后插入停顿/低头抬头事件。实现为通用参数，禁止写死具体场景坐标；golden 文件不受影响（旧策略不配这个字段）。
- `configs/actions.yml` 新增三条路线（场景坐标参考 `server.py:225-254` 的 `_scene_commands`，水区 x∈[-14,-5] z∈[-2,7]，玻璃区 x∈[5,14] z∈[-2,7]，光源排 z=-10）：
  - `water_edge_loop`：沿水区外沿一圈（如 x=-4 和 z=-3/z=8 边线），在两个岸边点驻留看水面。
  - `glass_edge_loop`：镜像地沿玻璃区外沿一圈。
  - `light_closeup_tour`：沿 z=-8 从 x=-10 走到 x=+11，在每个光源前（z=-10 那排）驻留 2s。
- 每条新路线：生成 trajectory JSON + viz PNG 入 `docs/trajectories/`；`test_trajectory_contract.py` 扩展断言其 route 不进 blocked_rects/不越界。
- 夜晚采集复用 `ground_astar_loop` + `matrix_night_complementary` profile，不需要新路线。

**验收**：三张路线图 + JSON 入库、测试全绿、`viz-trajectory` 对旧的 `ground_astar_loop` 也能出图（一并入库）。

### ITER-01 完成定义（DoD）

- `bash scripts/dev_check.sh` 全绿。
- 上述每个 T 的验收条目都有落盘证据（测试输出 / 报告 / 图 / 样例文件）。
- `docs/iterations/ITER-01-report.md` 写完：每任务 commit 哈希、跑过的命令与关键输出、产物路径、偏离计划的决定及理由、希望 planner 重点 review 的点。
- 分支推到 `iter/01-foundations`，不 merge。

---

## Backlog（ITER-02+，暂不执行）

1. **GPU 就绪后立刻做**：4090/L40S 真 GPU display 上跑 3-way matrix + qa-run/qa-compare 全套验证 → 扩 17 profile 全矩阵（这是 ITER-02 主体）。每批次跑完执行回传政策（见"渲染主机与数据存放政策"）。
1b. **L40S 8 卡扩展**：单卡容器已验证。8 卡机器上每卡一个 headless Xorg（`MCDATA_GPU_INDEX=i MCDATA_HEADLESS_DISPLAY=:$((77+i)) scripts/l40s_container_gpu_display.sh install/start/verify`），17 个 matrix profile 分片到 8 卡并行采集；需要给 `run-matrix` 增加 display/端口隔离参数（每个并行实例独立 `DISPLAY`、独立 `server_port`、独立 instance dir），或提供一个按卡分片的包装脚本。ITER-02 具体化。
2. `launch_profile`（`pipeline.py:141-258`）编排层分解（plan 阶段纯函数化 + 进程管理拆到 procutil/capture 模块）——等 T2/T3 的观测和 QA 就位后再动，降低回归风险。
3. 轨迹相机契约从像素改成角度（`yaw_deg`/`pitch_deg`），px-per-degree 换算下沉到 replay 层并按 profile 标定；向后兼容旧字段。
4. 渲染机上优先 XTEST backend（避免 xdotool 每步 spawn 子进程的抖动），补全 keycode 表。
5. 外部 policy adapter（MineRL/VPT/Voyager）：`external` 类型对接，输出统一 trajectory JSON。
6. 数据集打包器：扫描 runs 目录 → 汇总 episode 索引（manifest 聚合 + QA 通过标记）。
7. workspace 镜像目录改成 git clone/worktree（planner 处理）。

## 等待用户/管理员的事项

- ~~4090 headless Xorg~~ **已完成（2026-07-08）**：`mcdata-xorg` 服务运行中，`:77` = RTX 4090，smoke run 已验证（见"当前状态摘要"）。
- 远端大规模采集的可写大盘仍待解决（`/home` 6.0T 只剩 209G，97% 使用）。17 profile 全矩阵长时段采集前需要挂载大盘或远端 NAS。
