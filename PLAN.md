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

## ITER-01：可验证性基建 —— ✅ 已完成（2026-07-08）

review 通过并 merge（merge commit `b10a4ec`，tag `iter-01-done`）。任务明细、验收证据与 5 个非阻塞 findings 见 `docs/iterations/ITER-01-report.md` / `ITER-01-review.md`；findings 修复并入 ITER-02 T0。

## ITER-02：真 GPU 采集验证 + 多卡并行化（当前 iteration）

分支名：`iter/02-gpu-collection`（从 main 切，`git switch -c iter/02-gpu-collection main`；注意 main 已被 planner worktree 占用 checkout，直接从任意分支切即可）。执行顺序 T0 → T1 → T2；T3 依赖用户提供 8 卡环境，T2 的代码先行。

前置事实（planner 已备好，见"渲染主机与数据存放政策"）：
- 4090 `:77` = NVIDIA RTX 4090 display 可用，smoke run 已验证。远端代码在 `/home/lyf/mcdata`（系统 python3、XTEST backend、无 venv），**开跑前先 rsync 同步 main 最新代码**。
- L40S 单卡容器 `ssh l40s` 可用：`:77` display 已起（挂了重跑 `scripts/l40s_container_gpu_display.sh start`）、python 依赖已装、项目在 `/root/mcdata`。**必须显式 `export MCDATA_TMP_ROOT=/root/nas/bigdata1/tmp/mcdata`**。
- 回传：`scripts/pull_runs_from_remote.sh` 已在两台机器演练。

### T0 — ITER-01 review findings 修复（小，先做）

1. ARCHITECTURE.md trajectory 契约补充：`{"pause": true}` 事件语义；"replay 对未知事件类型必须静默跳过"的前向兼容规则。
2. `write_run_manifest` 与 run 内 trajectory 拷贝改为 `.tmp` + `os.replace` 原子写（R16）。
3. xdotool 后端注入失败加 warning（非零 returncode 至少记一次，可去重防刷屏）（R9）。
4. `settings._parse_bool` 非法值从静默 False 改为 RuntimeError，与其他 parser 一致；更新单测。

**验收**：dev_check 全绿；每项修复有对应单测或文档 diff。

### T1 — 4090 真 GPU 3-way 采集验证（关键路径）

1. rsync main 代码到 4090（命令见 PROGRESS.md；exclude .git/.venv/.mcdata/runs）。跑前 `nvidia-smi` 确认 GPU 0 显存余量（与他人训练共享）；若紧张改在 l40s 执行本任务（命令相同，路径/存储见前置事实）。
2. tmux 里 `DISPLAY=:77` 跑 `run-matrix --profiles matrix_low,matrix_textured,matrix_shader_high --strategy ground_astar_loop --duration 60`。
3. 每个 run 跑 `qa-run`；三方两两 `qa-compare`。
4. 另跑一次 `matrix_night_complementary`（60s）验证夜晚亮度可用。
5. 人工抽帧核对（PROGRESS.md 录制要求）：无黑边、不含加载画面、HUD 保留、无 toast、shader profile 有可见水反/光影/emissive 差异。
6. 回传 + 清理：`pull_runs_from_remote.sh 4090 /home/lyf/mcdata/runs --purge`。
7. 产物入库 `docs/qa_samples/iter02_4090_3way/`：qa_report ×4、compare 报告（跨 profile NCC 数值）、每 profile 一张代表帧（压缩，单张 <300KB）。

**验收**：4 个 manifest 完整且 `env` 里 GL renderer 为 NVIDIA；ffprobe fps=24、1280x720、时长正确；跨 profile NCC 数值记录在案（本轮不设硬阈值，收集定标数据）；回传后远端 runs 已清空。

### T2 — run-matrix 多实例并行化改造

目标：同一台多卡机器，N 个 profile 在 N 张卡上并行采集互不干扰。

- `launch_profile` / `run-matrix` 增加显式覆盖参数：`--display`、`--server-port`；server_dir 按实例隔离。世界一致性依赖固定 seed + scene 命令重建（跨机等价已在 4090/L40S smoke 验证）。
- 铁律：并行实例之间零共享可写路径（server dir / run dir / instance dir / trajectory 拷贝逐一确认）。DISPLAY 传参优先于环境变量，进 CaptureSettings 并写入 manifest。
- 新增 `scripts/matrix_shard.sh <gpu_index> <profiles_csv> [duration]`：DISPLAY=:$((77+gpu))、端口偏移、调用 run-matrix；遵守 R21。
- 本机无 GPU 验证：两个并发 dry-run 实例证明无路径/端口冲突（集成测试或脚本演示均可，证据入 report）。

**验收**：单测 + 并发 dry-run 证据；dev_check 全绿。

### T3 — L40S 8 卡全矩阵（依赖用户提供 8 卡容器，代码就绪即可开跑）

1. 每卡：`MCDATA_GPU_INDEX=i MCDATA_HEADLESS_DISPLAY=:$((77+i)) scripts/l40s_container_gpu_display.sh install && start && verify`。
2. 17 个 matrix profile 按卡分片（每卡 2–3 个），`matrix_shard.sh` 并行 60s 采集。
3. 全部 qa-run + 关键组合 qa-compare；结果先落 l40s CephFS，批次归档回传 local NAS。

**验收**：17 份完整 manifest + QA 报告；摘要 + 代表帧入库 `docs/qa_samples/iter02_full_matrix/`。

### ITER-02 完成定义（DoD）

- dev_check 全绿；`docs/iterations/ITER-02-report.md`（要求同 ITER-01：commit 哈希、命令与输出、产物路径、偏离说明、review 提示点）；分支不自行 merge。

---

## Backlog（ITER-02+，暂不执行）

1. `launch_profile`（`pipeline.py:141-258`）编排层分解（plan 阶段纯函数化 + 进程管理拆到 procutil/capture 模块）——等 T2/T3 的观测和 QA 就位后再动，降低回归风险。
3. 轨迹相机契约从像素改成角度（`yaw_deg`/`pitch_deg`），px-per-degree 换算下沉到 replay 层并按 profile 标定；向后兼容旧字段。
4. 渲染机上优先 XTEST backend（避免 xdotool 每步 spawn 子进程的抖动），补全 keycode 表。
5. 外部 policy adapter（MineRL/VPT/Voyager）：`external` 类型对接，输出统一 trajectory JSON。
6. 数据集打包器：扫描 runs 目录 → 汇总 episode 索引（manifest 聚合 + QA 通过标记）。
7. workspace 镜像目录改成 git clone/worktree（planner 处理）。

## 等待用户/管理员的事项

- ~~4090 headless Xorg~~ **已完成（2026-07-08）**：`mcdata-xorg` 服务运行中，`:77` = RTX 4090，smoke run 已验证（见"当前状态摘要"）。
- 远端大规模采集的可写大盘仍待解决（4090 `/home` 6.0T 只剩 209G，97% 使用）。短期用回传+purge 缓解；长时段采集建议直接用 l40s（自带 298T CephFS）。
- **8 卡 L40S 容器**：单卡验证已通过，T3 需要用户提供 8 卡环境（还是 `ssh l40s` 这种容器即可，要求 NVIDIA_VISIBLE_DEVICES 暴露 8 卡；graphics 能力不需要，脚本会自行补齐 X 模块）。
