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
5. **信号直连（2026-07-09 起，替代人工中转）**。信箱在 `/tmp/mcdata-collab/`（本机，不进 git），信箱语义（消息落盘直到被取走）保证不丢。

   **双方共同义务**：
   - 后台常驻自己的 listener：`scripts/collab_wait.sh planner`（planner 侧）/ `scripts/collab_wait.sh coder`（coder 侧）。进程退出=收到消息=被唤醒。
   - 交接固定三步：**写文档 → push → `scripts/collab_notify.sh <对方角色> "一句话指引"`**。消息只是指针（如 "T1g done, 见 report §T1g"），正式内容一律在 repo 文档里。
   - 被唤醒后：读信箱打印的指引 → `git pull` → 读对应文档 → 干活 → 交接 → **重启自己的 listener**（这步最容易忘）。

   **内容落点约定**：planner 的下一步计划永远写在本文件（PLAN.md）当前 iteration 小节；验收结论写 `docs/iterations/ITER-NN-review*.md`。coder 的完成汇报写 `docs/iterations/ITER-NN-report.md`。信号里不携带正文。

   **链路测试（首次启用时执行一次）**：coder 拉起 listener 后，执行 `scripts/collab_notify.sh planner "link test ack from coder"`；planner 被唤醒后回发 `scripts/collab_notify.sh coder "link confirmed, proceed"`；coder 的 listener 收到该回执即测试通过，此后开始正常推进。

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

T1 参考命令（按序执行，遇错在 report 记录后再处理）：

```bash
# 本机：同步代码
rsync -az --delete --exclude .git --exclude .venv --exclude .mcdata --exclude runs \
  /root/nas/bigdata1/cjw/projs/mcdata/ 4090:/home/lyf/mcdata/
ssh 4090 nvidia-smi          # GPU0 空闲显存 <6G 则整段改在 l40s 执行

# 4090 上（tmux 内）
cd /home/lyf/mcdata && export DISPLAY=:77 PYTHONPATH=src
python3 -m mcdata.cli run-matrix \
  --profiles matrix_low,matrix_textured,matrix_shader_high \
  --strategy ground_astar_loop --duration 60
python3 -m mcdata.cli run --profile matrix_night_complementary \
  --with-server --replay-actions --capture --strategy ground_astar_loop --duration 60

# 本机：回传+清理，然后对回传副本跑 QA
source scripts/mcdata_env.sh
scripts/pull_runs_from_remote.sh 4090 /home/lyf/mcdata/runs --purge
.venv/bin/mcdata qa-run "$MCDATA_OUTPUT_DIR/runs/remote_4090/<run_dir>" --frames 12   # 每个 run 一次
.venv/bin/mcdata qa-compare <low_run> <textured_run> <shader_run> --frames 12 \
  --out-dir docs/qa_samples/iter02_4090_3way
```


### T1b — 3-way 对齐修复与重采 —— 代码项通过，数据验收 FAIL（2026-07-08，见 review-t1b），由 T1c 接续

背景：T1 四路采集中 matrix_low（冷启动首个 run）中途偏航入水，其余三路相互对齐；证据与根因分析见 `docs/iterations/ITER-02-review-interim.md`。**以下设计 planner 定死，照此实现。**

1. **capture 前状态重置**（消除 warmup 期漂移）：`launch_profile` 中，warmup 结束后、`_prepare_capture_view` 之前，再调用一次 `apply_join_state(server_proc, profile)` 并 sleep 1.0s，runlog 记 `("join", "re_apply_state")`。这样无论 warmup 期间发生什么，t=0 时玩家位置/朝向/时间/天气都精确一致。
2. **位置 ground-truth 探针**：`server.py` 新增：

```python
def start_position_probe(proc, username: str, *, interval_sec: float = 5.0) -> threading.Event
```

   返回 stop event；线程每 interval 向 server stdin 写 `data get entity <username> Pos`。`launch_profile` 在 capture start 时启动、capture stop 时置位停止；run 结束后新增函数解析 server.log 中 `<username> has the following entity data:` 行，写 `<run_dir>/positions.jsonl`（每行 `{"idx": n, "x":, "y":, "z":}`）。不改 manifest schema。
3. **qa-compare 位置对齐**：两个 run dir 都有 positions.jsonl 时，按 idx 对齐计算逐点欧氏偏差，报告 max/mean；**max > 2.0 block 判 FAIL**（写进 report md 顶部）。单测用合成 positions 文件覆盖 pass/fail 两侧。
4. **`--game-version`**：`run` 与 `run-matrix` 各加 `--game-version`（str|None）Option，透传 bootstrap_profile/launch_profile（参数已存在）。此后远端采集一律 CLI，禁止手工 API 直调。
5. **同步脚本**：新增 `scripts/sync_to_remote.sh <host> <dest_dir>`（R21）：`git rev-parse HEAD > .sync_commit && rsync -az --delete --exclude .git --exclude .venv --exclude .mcdata --exclude runs ./ <host>:<dest>/`（`.sync_commit` 随包同步）。`pipeline._git_manifest` 在 git 不可用时 fallback 读 `<root>/.sync_commit`，manifest `git.commit` 来源标注 `"source": "sync_commit"`。
6. **冷启动规程**：重采前先跑一个 10s 丢弃 run（`--duration 10`，产物不留），再正式采。写入本任务执行步骤，T3 的 shard 流程同样适用（每卡首个 run 前丢弃跑一次）。
7. **重采**：修复合入后，用 CLI（`--game-version 26.2`）重采完整 3-way + night 同批次四路；qa-run ×4、两两 qa-compare（含位置对齐结果）；替换 `docs/qa_samples/iter02_4090_3way/` 全部产物；回传 + purge；本地删除两个带空格的 stray 目录。

**验收**：四路位置对齐 max 偏差 ≤2.0 block（positions.jsonl 为证）；视觉抽帧 t=30 四路同机位；其余同 T1 验收标准。

### T1c — 定位 T1b 回归 + 路线基准门 + 重采（最高优先级）

背景：T1b 重采四路"相互对齐但集体偏航"（自第二个拐角起转向丢失，直线出岛入海），证据见 `docs/iterations/ITER-02-review-t1b.md`。第一批（无 T1b 改动）同轨迹是正确的 ⇒ 回归由 T1b 批次引入。**以下设计 planner 定死。**

**步骤 0 — 零成本诊断（先做，结果写进 report 再动代码）**：
本地对比 `runs/remote_4090/20260708T062500Z_matrix_textured`（第一批，正确）与 T1b 批次 textured run 的 `pipeline.jsonl`：逐事件时间线（join→apply→warmup→re_apply→view_prepared→capture start→probe first_sample→replay 释放）、各间隔秒数、以及 `replay_log.jsonl` 首事件的墙钟对位。找出两批次在 replay 开始前后的一切行为差异，列表写进 report。

**步骤 1 — 路线基准门（ground truth，系统性修复）**：
1. 探针加时间戳：`start_position_probe` 每次发送记录 `time.monotonic()`；`launch_profile` 在 `ready_event.set()` 处记录 `replay_start_mono` 并传给 `write_positions_jsonl`，每条记录增加 `"t_rel"`（秒，相对 replay 释放；释放前的样本允许为负）。
2. 新纯函数模块 `src/mcdata/actions/simulate.py`：

```python
def simulate_track(trajectory: dict) -> list[dict]:
    # 由 route + events 推演理想位置时间线：w 按下期间沿 route 匀速推进
    # （该 leg 距离 /（按下时长）），转向期间原地不动；返回 [{"t":, "x":, "z":}]
```

   配单测：合成小轨迹验证若干时刻位置。
3. qa 增加 `check_route_reference(positions, ideal_track, *, max_dev=3.0)`：对每个带 `t_rel≥0` 的样本，在理想时间线上插值取 (x,z)，算欧氏偏差；同时校验 y∈[63.0, 66.0]。max 偏差 >3.0 或 y 越界 → FAIL。`qa-run` 在 run dir 同时存在 `positions.jsonl` 与 `trajectory.json` 时自动执行，结果写 qa_report md/json 头部。合成数据单测覆盖 pass/fail。
4. 既有的四路交叉对齐门（≤2.0）保留，两门都过才算数。

**步骤 2 — 回归定位实验（4090，matrix_low 30s ×4，探针常开）**：
`run` 命令加两个 hidden typer Option：`--debug-no-reapply`（跳过 capture 前二次 apply_join_state）、`--debug-no-replay-gate`（不等首个探针样本，capture start 即释放 replay），布尔透传 launch_profile。四组：
- A：两个 debug flag 全开（= 第一批行为 + 探针）
- B：仅 `--debug-no-replay-gate`（只加 re_apply）
- C：仅 `--debug-no-reapply`（只加 gate）
- D：默认（完整 T1b）
每组跑前先做一次 10s 丢弃 run。用步骤 1 的路线基准门判定各组在/不在路线上，锁定引入回归的机制。**若 A 也偏航，停下来上报 planner**（说明回归不在 T1b 代码，而在环境/时序因素）。

**步骤 2 结果（2026-07-08，planner 诊断，B/C/D 取消）**：A 组偏航触发停止条件后由 planner 现场仪器化诊断，根因已定：**replay 截断导致 XTEST 按键状态残留在 X server 上跨 run 污染**（铁证与完整复盘见 `docs/iterations/ITER-02-t1c-diagnosis.md`）。T1b 各机制无罪保留；planner 此前规定的"10s 丢弃 run"规程正式撤销（它就是播种源）。

**步骤 3 — 修复（规格 planner 定死）**：

1. `src/mcdata/actions/replay.py` 按键状态保证释放：
   - `replay_trajectory` 新增参数 `stop_event: threading.Event | None = None`；事件循环每次 sleep 以 ≤0.25s 分片并检查 `stop_event.is_set()`，置位即 break。
   - 维护 `held: set[str]`（提取纯 helper `_update_held(held, event)`：action=="down" 加入、=="up"/"tap" 移除；配单测）。
   - 整个循环包 try/finally：finally 对 `held` 中每个键发 KeyRelease（两种 backend 都实现）并 log 一行 released 键列表。
   - `pipeline.launch_profile`：创建 `replay_stop = threading.Event()` 传入线程；teardown finally 的**第一行**先 `replay_stop.set()`，join(timeout=5) 后再 terminate 各进程。
2. replay 开始前的卫生释放（防 SIGKILL 等跳过 finally 的场景）：focus 之后、首事件之前，XTEST backend 用 `query_keymap` 找出移动键集合 {w,a,s,d,space,left_shift} 中处于按下状态的键并释放，**有释放时 log warning "inherited stuck keys: [...]"**（观测污染来源）；xdotool backend 无法查询则对该集合无条件 keyup。
3. `options.py` QUIET_CAPTURE_OPTIONS 增加 `"rawMouseInput": "true"`（确定性硬化，消除对 GLFW 默认值的依赖）；同步 test_options。
4. 单测：`_update_held` 纯逻辑；mock backend 记录调用，验证 stop_event 提前置位的截断 replay 会对 held 键发 KeyRelease。

**步骤 3.5 — 修复验证（4090）**：
1. `sync_to_remote.sh` 同步 → `bootstrap --profile matrix_low --game-version 26.2`（让 options.txt 正式含 rawMouseInput）。
2. **不做丢弃 run**，连续两个默认配置 run：`run --profile matrix_low --with-server --replay-actions --capture --strategy ground_astar_loop --duration 60 --game-version 26.2 --lane t1cval1`（第二次 `t1cval2`）。两个都必须路线基准门 PASS（第一 run 验证卫生释放兜住既有污染，第二 run 验证自洁）。
3. 其中一个 run 并行跑 `scripts/pointer_probe.py`（用法见文件头），report 记录 gameplay 段 edge-parking 占比——用于对 P2（指针边缘截断）结案：若 run PASS 则 P2 归档为污染次生现象。
4. 任一 FAIL：停，收集 positions/pointer_probe/replay 日志上报 planner，不要自行加机制。

**步骤 4 — 全量重采**（同原文）：验证通过后四路同批次重采（**无需丢弃 run**），双门 PASS + t=30 抽帧对照路线图，替换 `docs/qa_samples/iter02_4090_3way/`，回传 + purge。

**步骤 4 — 全量重采**：修复合入后四路同批次重采（丢弃 run 先行），验收 = 每路路线基准门 PASS（≤3.0）+ 四路交叉门 PASS（≤2.0）+ t=30 抽帧与 `docs/trajectories/ground_astar_loop.png` 预期场景一致（平台上）；整体替换 `docs/qa_samples/iter02_4090_3way/`；回传 + purge。

**验收**：上述四步全部有落盘证据；report 含步骤 0 的差异列表与步骤 2 的四组判定表。

### T1d — 转向标定修正 + 朝向观测 + 全量重采（T1c 步骤 3.5/4 由此取代）

背景：第二根因 P4（转向欠转 10%，自 ITER-01 存在）已由 planner 定位并实证，见 `docs/iterations/ITER-02-t1c-diagnosis.md` 追加节。**以下设计 planner 定死。**

1. **标定修正**：`configs/actions.yml` 全部 astar_walk 策略的 `turn_px_per_degree` 从 `6.0` 改为 `6.6667`（= 600px/90°，对应 MC sensitivity 0.5 的 0.15°/px）。`options.py` QUIET_CAPTURE_OPTIONS 增加 `"mouseSensitivity": "0.5"` 显式钉死（不依赖游戏默认值），同步 test_options。
2. **golden 更新**：轨迹事件因此变化，golden 全量重生成——单独一个 commit，message 说明"P4 标定修正导致的预期行为变更"。`docs/trajectories/` 的 JSON/PNG 同步重生成。
3. **朝向观测**：位置探针的查询从 `data get entity <user> Pos` 扩为 Pos 与 Rotation 两条（同周期发送）；positions.jsonl 每条增加 `"yaw"`（浮点，MC 值域 -180..180）。`simulate_track` 增加输出理想 yaw 时间线；route gate 增加 yaw 校验：**每样本 |实际yaw − 理想yaw| 循环差 > 10° 判 FAIL**（阈值参数化）。合成单测覆盖。
4. **转向保真探针入库**：`configs/actions.yml` 正式加入 `turn_calibration_probe`（scripted，8×600px、间隔 2.5s；planner 已在 l40s 临时验证过），作为标定回归工具；README 或 PLAN 不需要额外文档，策略名自说明。
5. **验证（两台机器都要）**：4090 与 l40s 各跑 `turn_calibration_probe`（+360°/+720° 回归点抽帧 NCC ≥0.8 且目检重合）+ 各连续两个 `ground_astar_loop` 60s 默认 run，**路线门（位置+yaw）四个 run 全 PASS**。任一 FAIL：停，收集证据上报。
6. **全量重采**（原 T1c 步骤 4）：验证过后 4090 四路同批次重采（无丢弃 run），双门+yaw 门 PASS，t=30 抽帧对照路线图，替换 `docs/qa_samples/iter02_4090_3way/`，回传 + purge。l40s 侧临时文件清理（/root/mcdata/l40sval*.log、turn600.log、configs 里 planner 临时加的 turn_probe_600 条目由正式 turn_calibration_probe 取代）。
7. 疑难备案：若步骤 5 中 yaw 门显示残差随转向次数线性累积并最终突破阈值，上报 planner（预案是 waypoint 处服务端 yaw 重同步，暂不实现）。

**验收**：步骤 5 的 4+2 个验证产物 + 步骤 6 的重采产物全部入库/落盘；dev_check 全绿。

### T1e（修订 v2）— 场景岩浆围挡（P6）+ 行走速度标定（P5）+ 终局重采

修订原因（2026-07-08 第二次）：首版标定 probe 设计有两处 planner 错误——①标定路线朝南直穿场景岩浆流（岩浆源 (2,64,-10) 裸露，流淌半径 3 格覆盖 x∈[-1,5]×z∈[-13,-7]；hold-1 实测 4.5 格 ≈ 岩浆内移速×4s，证据吻合）；②4 个等长 hold 拟合 v/t0 秩亏（coder 指出，正确）。并由此发现 P6：**真实路线第一段也擦过岩浆流边缘**，不围挡则步速修好后位置门仍会被岩浆减速拖爆。yaw≈171° 判定为 1s 采样落在转向中途的伪影，非缺陷。

1. **P6 岩浆围挡**（`server.py` `_scene_commands`）：在 lava setblock 之前加一行 `fill ox+1 oy oz-11 ox+3 oy oz-9 minecraft:glass`，再原位 `setblock ox+2 oy oz-10 minecraft:lava`（源被四邻玻璃围死不再流淌；光照/emissive 保留，隔玻璃可见）。同步：`configs/actions.yml` 所有 astar_walk 的 `blocked` 增补围挡 9 格 `[1,-11],[2,-11],[3,-11],[1,-10],[3,-10],[1,-9],[2,-9],[3,-9],[2,-10]`（源本就在 blocked 则去重）。scene 命令如有单测断言数量/内容，一并更新。
2. **标定 probe 重设计**（替换现有 walk_calibration_probe）：走**东西向干净走廊 z=-13.5**（该行无任何场景物）。序列（scripted）：
   - t=2.0：`{mouse_dx: -600}`（南→东）；
   - t=4.0 起四个**变长** hold，方向交替（每次 hold 后 2×600px 转 180°，转前后各 pause ≥1.5s）：T₁=1.0s（东）、T₂=1.5s（西）、T₃=2.0s（东）、T₄=2.5s（西）。累计漂移 ≈ −v×1s ≈ −4.3 格，全程 x∈[-5,12] 不出走廊、不碰任何东西。
   - 总时长 ~28s，`--duration 32 --probe-interval 1`。
3. **拟合**：d_i 取每个 hold 前后**静止段**样本的欧氏位移（静止段=不在任何事件 ±0.5s 内）；`d = v×(T−t0)` 两未知、四方程最小二乘；残差 >0.5 格 → 停、上报。yaw 校验同样只用静止段样本。
4. **generator 修正**（同首版）：hold 公式 `distance × seconds_per_block + walk_startup_comp_sec`；actions.yml 填实测 v、t0。
5. **golden / docs/trajectories 重生成**（P5+P6 一并，独立 commit；路线会因 blocked 增补绕行，viz PNG 重出）。
6. **验证 + 终局重采**（同首版步骤 6）：两台各 2×60s 默认 run 双门全 PASS → 4090 四路重采替换 QA samples → 回传 + purge。任一 FAIL：停、留证据、上报。
7. 执行顺序注意：步骤 1 合入并部署后再跑步骤 2 的标定 run（旧 lane 世界有历史岩浆流，**标定与验证一律用新 lane 名**保证场景重建）。

**验收**：标定拟合数据（v、t0、残差、各 hold 的 d_i/T_i 表）写进 report；围挡后场景截图一张入 report 佐证；四个验证 run + 重采产物入库；dev_check 全绿。

### T1f — 场景障碍完备映射（P7）+ yaw 门去伪影 + 终局重采

背景：T1e v2 标定完美（v=4.3172、t0≈0.9ms、残差 2.5e-5），且该验证 run 证实 P3/P4/P5 全部根治（起点精确、静止段 yaw 全为 90 的整数倍、结束后干净静止）。剩余两个问题（planner 已从 run 数据定位）：①yaw 门未过滤转向窗口内的采样（新轨迹转向密度大增后 5s 采样必然撞上，35.9° 为 mid-turn 伪影）；②P7——场景固体障碍（树叶墙 z=9 x∈[-2,2]、白墙 z=14 x∈[-4,4]、角落标记块）从未进入 A* blocked 配置，P6 重规划后的新路线合法穿墙，t≈20s 起玩家被树叶墙卡住导致位置发散。**以下设计 planner 定死。**

1. **yaw 门去伪影**：route gate 的 yaw 校验跳过转向窗口内样本（窗口 = 每个 mouse 事件的 `[t − 0.5, t + duration + 0.5]`；trajectory.json 就在 run dir，gate 可直接读）。位置校验不变（转向中玩家静止，位置样本仍有效）。合成单测覆盖"mid-turn 样本被跳过"。
2. **P7 障碍完备映射**：coder 逐条核对 `server.py _scene_commands`，把 y=64/65 层的每一个固体格枚举成表（report 里附全表：命令 → 占用格 → 是否已在 blocked/blocked_rects），并把缺失的补进 `configs/actions.yml` 全部 astar_walk 的 `blocked`。已知缺失至少包括：树叶墙 `[-2,9],[-1,9],[0,9],[1,9],[2,9]`；白墙 `[-4,14]..[4,14]`（9 格）；角落标记 `[-14,12],[14,12]`。光源排（z=-10）与岩浆盆已在配置的逐一确认。
3. **防回归契约测试**：新增测试内置场景障碍格清单（硬编码镜像 `_scene_commands`，注释注明来源与同步义务），断言所有 astar_walk 策略的 route 与全部障碍格无交集。场景与配置的单一来源统一（scene.yml 方案）记入 Backlog，ITER-03 处理。
4. **golden / docs/trajectories 重生成**（独立 commit，注明 P7 路线变更）；目检新 viz PNG 确认路线不再穿越任何场景元素后再进验证。
5. **验证 + 终局重采**（同 T1e v2 步骤 6）：l40s 先行 2×60s；4090 侧 nvidia-smi 阻塞若持续，报告即可、勿硬等（planner 跟进环境问题）；4090 恢复后 2×60s。四个 run（或 l40s 双 run + 4090 待补）双门全 PASS 后做四路重采、替换 QA samples、回传 + purge。
6. 停止条件照旧：任何 FAIL 停、留证据、上报。

**验收**：障碍映射全表 + 新 viz 目检 + 验证 run 结果 + 重采产物；dev_check 全绿。

### T1g — 场景构建静默失败（P8）+ t_rel 基准偏移（P9）+ 终局重采

背景（planner 从 t1fl40snasA 数据+录像+server.log 定位，证据链见该 run 目录）：
- **P8**：`_scene_commands` 的空域清理 `fill ... air` 体积 39,701 格 > vanilla 上限 32,768，**自 ITER-01 起每次静默失败**（server.log 铁证 "Too many blocks..."）。平台上方天然植被从未清除，seed=1 原生树木立在路线上（两次失败 run 均精确卡死在 x≈7, z≈9–10 的树干处）。此前未撞上只因旧路线从未被正确执行。
- **P9**：positions 的 t_rel 基准记录于 ready_event，而 replay 线程在卫生释放+窗口聚焦（~0.3s）之后才开始计时——移动中样本恒定落后 ~1.3 格（0.3s×4.317），驻留时刻偏差仅 0.12 格，是测量偏移不是物理问题。
- 另外该 run 画面有黑边（窗口小于 1280×720 或捕获几何异常），一并查。

**修复规格（planner 定死）**：

1. **P8 fill 拆分**：空域清理拆成两条：`fill ox-18 oy oz-18 ox+18 oy+22 oz+18 air`（37×23×37=31,487）与 `fill ox-18 oy+23 oz-18 ox+18 oy+28 oz+18 air`（37×6×37=8,214）。
2. **P8 系统性防护——场景构建回执校验**：`server.py` 新增 `verify_scene_commands(log_path, *, expected_fill_count)`：场景命令下发后轮询 server.log（≤10s），出现 `Too many blocks`、`Cannot place`、`Expected`、`Unknown` 等失败样式即 raise（消息含违规行与日志路径）；成功需数到与场景命令数量一致的回执行（`Successfully filled`/`Changed the block`/`No blocks were filled` 均计）。`launch_profile` 在 server start 后调用。单测用合成日志覆盖成功/超限/缺回执三侧。
3. **P9 基准修正**：`replay_trajectory` 在卫生释放+聚焦完成、进入事件循环前，向 replay_log.jsonl 写首行 `{"event":"start","mono": <time.monotonic()>}`；`write_positions_jsonl` 改为从 replay_log 首行读取该基准计算 t_rel（读不到时回退 ready_event 基准并 warning）。
4. **黑边排查**：capture start 时把 `xwininfo` 实测窗口几何写进 runlog；对本次验证 run 跑 qa-run 确认 border 检查是否命中，结果写 report（若窗口尺寸异常，先记录现象与数据，方案听 planner）。
5. golden 无需变更（P8/P9 不动轨迹生成）。**验证 + 终局重采**同 T1f 步骤 5/6（l40s-only：2×60s 双门 PASS → 四路重采改在 l40s 执行 → 替换 QA samples → 归档 CephFS + 回传 local NAS）。停止条件照旧。

**验收**：场景回执校验单测 + 新验证 run 的 server.log 无失败样式 + 双门 PASS + 重采产物；dev_check 全绿。

### T2 — run-matrix 多实例并行化改造

目标：同一台多卡机器，N 个 profile 在 N 张卡上并行采集互不干扰。**以下设计由 planner 定死，照此实现；发现设计缺陷在 report 里提出，不要自行变更接口。**

并行模型是**进程级并行**：每张卡一个独立的 `run-matrix` 进程（由 `matrix_shard.sh` 启动），进程内各 profile 仍串行。因此 DISPLAY 是进程全局属性，不需要穿透每一层。

1. `src/mcdata/settings.py` 新增（env 写同样收敛在 R2 边界文件内）：

```python
def apply_display_override(display: str) -> None:
    """Process-global DISPLAY override; call once at CLI entry before anything touches X."""
    os.environ["DISPLAY"] = display
```

2. `cli.py`：`run` 与 `run-matrix` 各新增三个 Option：`--display`（str|None）、`--server-port`（int|None）、`--lane`（str|None）。`--display` 给定时在命令体第一行调用 `apply_display_override(display)`；后两者透传给 bootstrap_profile / launch_profile。

3. `pipeline.py`：`bootstrap_profile` / `launch_profile` 各新增 kwargs `server_port: int | None = None`、`lane: str | None = None`。在 `load_profile` 之后立即 overlay：

```python
if server_port is not None:
    profile = {**profile, "server_port": int(server_port)}
```

`launch_profile` 内：lane 存在时 run dir 命名为 `<stamp>_<profile>__<lane>`；lane 透传给 `start_server`；manifest 顶层新增 `lane` 字段（null 允许），`manifest.schema.json` 同步，`SCHEMA_VERSION` 升为 2——此契约变更 planner 在此批准，ARCHITECTURE.md manifest 一节同步一行。

4. `server.py`：`ensure_server` / `start_server` 新增 `lane: str | None = None`；lane 存在时 `server_profile = f"{world_profile}__{lane}"`（世界目录与 level-name 同名隔离）。世界一致性由固定 seed + scene 命令保证，不拷贝世界。

5. `run-matrix` 的共享 trajectory 落盘路径改为 `f"{strategy}_matrix_{lane or 'main'}.json"`，避免并发写同一文件。

6. 新脚本 `scripts/matrix_shard.sh <gpu_index> <profiles_csv> [duration=60]`（R21）：display=`:$((77+gpu_index))`，port=`$((25600+gpu_index))`，lane=`gpu<gpu_index>`，调用 `run-matrix --profiles <csv> --strategy ground_astar_loop --duration <d> --display <:n> --server-port <p> --lane <lane> --no-bootstrap`。脚本开头逐个检查 profile 的 instance dir 存在，缺失则报错退出并提示先串行 bootstrap（并发 bootstrap 会在共享 launcher main_dir 上竞争下载，因此 shard 一律 `--no-bootstrap`）。

7. 测试（全部本机可验证，无需 GPU）：
   - 单测：server_port overlay；lane 对 run dir / server dir / trajectory 路径的命名；`apply_display_override` 后 `CaptureSettings.from_env` 取到新 DISPLAY；manifest 含 lane 且 schema v2 校验通过。
   - 并发证据：本机同时起两个 dry-run（不同 `--lane/--server-port/--display`），断言两个 run dir 相互独立、无共享路径写入、两份 manifest 的 lane/port 正确。pytest 或脚本演示均可，证据进 report。

**验收**：dev_check 全绿 + 第 7 条并发证据。

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
6a. **场景单一来源统一**：`_scene_commands` 与 A* blocked 配置目前双维护（P7 教训），ITER-03 统一为 `configs/scene.yml`（方块清单 → server 生成 fill/setblock 命令 + actions 派生障碍格），杜绝手工同步。
6b. **仿真/渲染加速（deferred，ITER-04+，方案由 planner 设计，coder 勿自行引入）**：目标是超实时出片。硬性要求：**加速采集的渲染结果必须与实时采集等价可互换**（同一世界/轨迹/资源下逐帧内容一致或统计上不可区分，QA 工具可验证）。候选主路线 ReplayMod 离线渲染（record-once-render-N，顺带获得完美 N-way 对齐），spike 需验证：MC 版本兼容、Iris 光影渲染、HUD 保留方案、实测速度倍率、与实时采集的等价性对比。tick-rate 加速路线因 correctness 风险已排除。在此之前，采集管线里禁止引入任何时间缩放。
7. workspace 镜像目录改成 git clone/worktree（planner 处理）。

## 等待用户/管理员的事项

- ~~4090 headless Xorg~~ **已完成（2026-07-08）**：`mcdata-xorg` 服务运行中，`:77` = RTX 4090，smoke run 已验证（见"当前状态摘要"）。
- 远端大规模采集的可写大盘仍待解决（4090 `/home` 6.0T 只剩 209G，97% 使用）。短期用回传+purge 缓解；长时段采集建议直接用 l40s（自带 298T CephFS）。
- **8 卡 L40S 容器**：单卡验证已通过，T3 需要用户提供 8 卡环境（还是 `ssh l40s` 这种容器即可，要求 NVIDIA_VISIBLE_DEVICES 暴露 8 卡；graphics 能力不需要，脚本会自行补齐 X 模块）。
