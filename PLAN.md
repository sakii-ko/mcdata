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
   - 交接固定三步：**写文档 → push → 在后台运行 `scripts/collab_handoff.sh <自己的角色> "一句话指引"`**（原子命令：通知对方后立即变身自己的 listener，杜绝忘记重启监听）。消息只是指针（如 "T1g done, 见 report §T1g"），正式内容一律在 repo 文档里。
   - 被唤醒后：读信箱打印的指引 → 直接读对应文档（**同机同工作树，无需 pull**——planner 的改动经其 worktree 提交后由 planner merge 进本分支，落盘即可见；push 仅为 GitHub 备份）→ 干活 → 交接 → **重启自己的 listener**（这步最容易忘）。

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

## ITER-02：真 GPU 采集验证 + 多卡并行化 —— ✅ 已完成（2026-07-09，tag `iter-02-done`）

T1 链条根治 P3–P10 八层根因，四路对齐 0.216–0.475 格；T2 多实例并行化就绪；T4 架构清账完成。明细见 `docs/iterations/ITER-02-report.md` / `ITER-02-review*.md`。**T3（8 卡全矩阵）为运行型任务：用户提供容器后随时从 main 执行，优先级高于 ITER-03 一切任务**（流程：每卡 `l40s_container_gpu_display.sh install/start/verify` → 串行 bootstrap → `matrix_shard.sh <gpu> <profiles>` 分片并行 → qa → CephFS 归档 + 回传）。

## ITER-03：程序化漫游 + 场景单一来源 + 矩阵扩容 —— ✅ 已完成（2026-07-10）

分支名：`iter/03-roaming-scene`（从 main 切）。顺序 T0 → T1 → T2。**8 卡容器到位即中断当前任务，先执行 ITER-02 T3。**

T0 scene.yml 单一来源、T1 六条确定性 roam 路线和 T2 26.2 可用矩阵扩容均已完成；本地 94 项测试通过，L40S 验收与可视化目检通过。七个 T2 候选中五个没有 Modrinth 26.2 版本，按约定跳过，最终为 20 asset sets / 19 matrix profiles。明细见 `docs/iterations/ITER-03-report.md` 与 `ITER-03-review.md`。

### T0 — scene.yml 场景单一来源（消灭 P7 双维护）

1. 新配置 `configs/scene.yml`：把 `_scene_commands` 的每条命令改写为数据条目，格式：

```yaml
scene:
  origin: [0, 64, 0]
  entries:
    - {kind: fill, block: "minecraft:air", from: [-18, 0, -18], to: [18, 28, 18]}   # 相对 origin，自动拆分超限
    - {kind: fill, block: "minecraft:water", from: [-14, -1, -2], to: [-5, -1, 7], region: water_pool}
    - {kind: setblock, block: "minecraft:torch", at: [-10, 0, -10]}
    # ……逐条对应现有命令，named region 用于障碍派生
```

2. 新纯模块 `src/mcdata/scene_model.py`（基础层；actions 与 render 均可 import——ARCHITECTURE 白名单表与 checker 同步，此变更 planner 在此批准）：
   - `load_scene(config_dir) -> SceneSpec`；
   - `scene_commands(spec) -> list[str]`：生成 fill/setblock 命令，**fill 体积 >32768 自动按 y 切片拆分**（P8 修复泛化进生成器）；
   - `walk_obstacles(spec) -> set[tuple[int,int]]`：派生 y∈[0,1]（相对 origin，即世界 y64/65）层的固体占用格（air/water 下沉层不算；与现审计表一致）。
3. `render/scene.py` 改为消费 scene_model（命令生成删除硬编码）；`actions/strategies.py` 的 astar_walk/roam 障碍 = config `blocked` ∪ `walk_obstacles(spec)`；测试里的硬编码镜像表删除，契约测试改为对派生集断言。
4. **行为不变证据**：新增"命令 golden"测试——`scene_commands()` 输出与现硬编码列表逐字符一致（含拆分后的 air fill 两条）；轨迹 golden 预期零变更（派生障碍集合应与现 blocked 完全一致，若有出入先列差异表上报，不得静默改路线）。
5. l40s 一次 60s 验证 run 双门 PASS。

### T1 — 程序化漫游 roam 策略

1. `strategies.py` 新 builder `type: roam`（注册表加项）。spec 通用参数：
   `seed`（必填）、`bounds`、`num_goals=8`、`min_goal_dist=6`、`pause_prob=0.3`、`pause_sec_range=[1.0,3.0]`、`look_dy_px_choices=[20,40]`，行走/转向参数与 astar_walk 相同（seconds_per_block/turn_px_per_degree/walk_startup_comp_sec 沿用标定值）。
2. 实现：`random.Random(seed)`；从可行走格（bounds − 派生障碍 − blocked）采样目标序列（相邻目标 ≥min_goal_dist，不可达则重采，重采上限 100 次后 raise）；A* 串联；按 pause_prob 在目标点采样驻留/低头事件（waypoint_actions 机制复用）。与 astar_walk 共享事件生成 helper（提炼 `_walk_events(route, spec)`，两 builder 共用——顺路消化重复）。
3. 路线库：`configs/actions.yml` 加 `roam_a`…`roam_f`（seed 101–106）。每条：golden + `docs/trajectories/roam/` viz PNG（目检入库）。
4. 测试：确定性（同 seed byte-identical）、全部 roam 路线与派生障碍零交集、不同 seed 路线互异（route 序列不等）。
5. l40s 验证：`roam_a` 与 `roam_d` 各一个 60s run，双门 PASS。

### T2 — 材质/光影矩阵扩容（18 → 25 asset sets）

1. 新增 7 个 asset set（Modrinth slug 不可用或不支持 MC 26.2 时：跳过并在 report 记录，不找替代品）：
   - `photon_faithful`（photon-shader + Faithful 32x）
   - `rethinking_voxels_hd`（rethinking-voxels + Default HD 64x）
   - `super_duper_vanilla`（super-duper-vanilla，vanilla 材质）
   - `euphoria_complementary`（euphoria-patches + Complementary Reimagined）
   - `nostalgia_faithful`（nostalgia-shader + Faithful 32x）
   - `kappa_hd`（kappa-shader + Default HD 64x）
   - `solas_patrix`（solas-shader + Patrix 32x）
2. `profiles.yml` 对应新增 `matrix_*` profile（共享 `render_matrix_base`，参数抄现有 matrix 条目）。
3. 验证（l40s）：新 profile 逐个 bootstrap + 10s smoke + qa-run；每个 profile 一张代表帧（压缩 <300KB）入 `docs/qa_samples/iter03_expansion/`；不可用 slug 的清单入 report。
4. test_configs 交叉引用自动覆盖；PROGRESS.md 概览段的 profile/asset 计数更新。

### ITER-03 完成定义（DoD）

- dev_check 全绿；`docs/iterations/ITER-03-report.md`（要求同前）；分支不自行 merge；交接用 `collab_handoff.sh`（确认 notify 输出成功——上轮 T4 的 handoff 消息 planner 未收到，本轮留意）。

## Backlog（ITER-02+，暂不执行）

1. ~~launch_profile 编排层分解~~ ✅ ITER-02 T4 完成（RunPlan/RunState + 阶段函数）。pipeline.py 文件级拆分（plan/phases 分文件）为可选 polish，触碰该文件时顺路。
3. 轨迹相机契约从像素改成角度（`yaw_deg`/`pitch_deg`），px-per-degree 换算下沉到 replay 层并按 profile 标定；向后兼容旧字段。
4. XTEST keycode 表补全（渲染机已实际使用 XTEST backend；表仍只覆盖基础键位）。
5. 外部 policy adapter（MineRL/VPT/Voyager）：`external` 类型对接，输出统一 trajectory JSON。
6. 数据集打包器：扫描 runs 目录 → 汇总 episode 索引（manifest 聚合 + QA 通过标记）。
5b. **录制→轨迹转换管线**：捕获一段真人/智能体的输入流转成 trajectory JSON 再逐 profile 重放——接入 Baritone/VPT/MineRL 的通用入口（ITER-04 起）。
6b. **仿真/渲染加速（deferred，ITER-04+，方案由 planner 设计，coder 勿自行引入）**：目标是超实时出片。硬性要求：**加速采集的渲染结果必须与实时采集等价可互换**（同一世界/轨迹/资源下逐帧内容一致或统计上不可区分，QA 工具可验证）。候选主路线 ReplayMod 离线渲染（record-once-render-N，顺带获得完美 N-way 对齐），spike 需验证：MC 版本兼容、Iris 光影渲染、HUD 保留方案、实测速度倍率、与实时采集的等价性对比。tick-rate 加速路线因 correctness 风险已排除。在此之前，采集管线里禁止引入任何时间缩放。
7. workspace 镜像目录改成 git clone/worktree（planner 处理）。

## 等待用户/管理员的事项

- ~~4090 headless Xorg~~ **已完成（2026-07-08）**：`mcdata-xorg` 服务运行中，`:77` = RTX 4090，smoke run 已验证（见"当前状态摘要"）。
- 远端大规模采集的可写大盘仍待解决（4090 `/home` 6.0T 只剩 209G，97% 使用）。短期用回传+purge 缓解；长时段采集建议直接用 l40s（自带 298T CephFS）。
- **8 卡 L40S 容器**：单卡验证已通过，T3 需要用户提供 8 卡环境（还是 `ssh l40s` 这种容器即可，要求 NVIDIA_VISIBLE_DEVICES 暴露 8 卡；graphics 能力不需要，脚本会自行补齐 X 模块）。
