# PLAN.md — mcdata 协作计划

本文件是项目的执行计划与验收契约。自 2026-07-10 起由同一执行者直接负责计划、实现、
测试、真 GPU QA、视觉 review、merge 与文档收口，不再经过 planner → coder 人工交接。

配套文档：
- `docs/ARCHITECTURE.md` — 架构分层、依赖规则、数据契约、git 约定（**先读这个再动代码**）。
- `docs/CODE_STANDARDS.md` — 硬性代码规范（环境变量纪律、副作用分离、错误处理、日志要求、依赖纪律）。其中 [checker] 条目由 `scripts/check_standards.py` 机械执行，**任何时候都必须保持退出码 0**。
- `PROGRESS.md` — 长期交接文档，记录已完成能力和远端机器状态。
- `docs/iterations/ITER-NN-report.md` — iteration 的实现、运行与验收证据。
- `docs/iterations/ITER-NN-review.md` — 独立 review 结论；历史文件继续保留原角色署名。

## 执行流程

1. 在本文件写清任务、风险与可机械验证的完成定义。
2. 从 main 创建范围明确的分支；实现与测试使用独立、可回退的 commit，格式遵循
   `docs/ARCHITECTURE.md`。
3. 在本地测试通过后执行与风险相称的真 GPU E2E、离线 QA 和视觉目检；运行失败证据与
   accepted cohort 分目录保存，禁止拼接不同 commit/trajectory 的 run。
4. 报告必须记录代码/轨迹/world-state provenance、逐路 QA、跨路对齐、存储位置与已知例外。
5. review 完成后 `merge --no-ff`、push，并按 iteration/dataset milestone 打 tag。

## 项目北极星目标（不变部分，摘自 PROGRESS.md）

对同一个 Minecraft 世界、同一条 action 轨迹，采集 N-way 渲染差异视频数据集：底层世界内容与行动完全一致，只改变材质包/光影/水反/emissive 等渲染质量。当前配置 19 个 matrix profile；其中 18 个 noon profile 构成固定 seed / scene / world-state / action 的严格 N-way 主矩阵，`matrix_night_complementary` 保持相同 seed / scene / action、仅将时间改为 midnight，作为独立受控补充变体。录制 24fps、无黑边、保留 HUD、无干扰 toast、不录加载过程；action 在地面真实游走（A* 规划）；支持天气/时间/夜晚亮度控制。最终渲染设备是远端 4090 / L40S（GPU-backed X display），本机只能做逻辑验证和 Xvfb 软渲染 smoke test。

数据可用性的根基是**可复现与可验证**：每个 run 必须能从 manifest 完整还原（哪个 profile、哪些资源、哪条轨迹、什么世界状态），每批数据必须有自动 QA 证据（帧率/尺寸/黑边/对齐）。
同一 strict N-way cohort 必须具有相同 trajectory SHA-256、world seed/state、scene、
Minecraft 版本、采集规格和代码 commit；有意改变天气/时间的 profile 必须归入单独
world-state variant，不与 rendering-only cohort 混称。

## 当前状态摘要

- 代码链路（bootstrap → server/scene → launch → join-gate → warmup → capture → replay）已跑通，见 PROGRESS.md。
- **原硬阻塞已解除（2026-07-08）**：管理员已在 4090 安装 `mcdata-xorg` systemd 服务，`DISPLAY=:77` 为 NVIDIA RTX 4090（direct rendering: Yes，驱动 550.67）。planner 已用 baseline 代码在真 GPU 上完成 20s smoke run（`matrix_low` + `ground_astar_loop`）：capture.mp4 1280x720 / 24fps / 480 帧整，Sodium 识别到 NVIDIA 适配器，56 mods 正常加载，run 结束无残留进程。远端 run dir：`/home/lyf/mcdata/runs/20260708T052709Z_matrix_low`。
  - 注意：GPU 0 与其他用户的训练任务共享（约 9GB 显存被占）。ITER-02 大规模采集前评估换空闲卡（重装服务改 `MCDATA_GPU_INDEX` 即可）。
- **项目北极星采集已闭环（2026-07-10）**：L40S 单卡串行完成 clean commit
  `dbca539` 的 19 路全矩阵；18 路 noon strict cohort 与 1 路 midnight variant 分组明确。
  19/19 单路 QA、153 对 strict compare、171 对全量诊断、资源/光影 runtime audit 和人工
  visual review 全部通过。批次由确定性 dataset index + `SHA256SUMS` 固化（dataset ID
  `sha256:3e99daab…d1dc9`，本地 `runs/remote_l40s/accepted_full19_dbca539/`），详见
  `docs/iterations/ITER-02-t3-report.md`。
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

T1 链条根治 P3–P10 八层根因，四路对齐 0.216–0.475 格；T2 多实例并行化就绪；T4 架构清账完成。T3 已于 2026-07-10 在单张 L40S 上串行完成 19 路全矩阵（多卡只影响吞吐，不影响 correctness），验收与三轮 rejected-cohort 根因链见 `docs/iterations/ITER-02-t3-report.md`。其余明细见 `docs/iterations/ITER-02-report.md` / `ITER-02-review*.md`。

## ITER-03：程序化漫游 + 场景单一来源 + 矩阵扩容 —— ✅ 已完成（2026-07-10）

分支名：`iter/03-roaming-scene`（从 main 切）。顺序 T0 → T1 → T2；其后 T3 全矩阵也已闭环。

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

## ITER-04：反馈式长时漫游 + 高辨识度 PBR 视觉 —— 🚧 进行中（2026-07-10）

分支：`iter/04-feedback-roam-visuals`。

用户验收指出两项实质问题：上一轮 10 分钟预览只有固定左右 pan，不是自动寻路游走；
Faithful 32x + 默认 Complementary 的材质差异和水反质量也不足。此前被拒绝的 16 圈长
A* 证明，重复开环键鼠轨迹会累积亚格误差，最终撞入水池或跌出平台；本轮禁止恢复该方案，
也禁止用隐藏 teleport 冒充真实行走。

### T0 — 无 teleport 的反馈式 waypoint navigator

1. 新增 `feedback_roam` 策略：固定 seed 采样可达目标并用 A* 生成路线；轨迹记录 route、
   goals 和控制参数，计划时长至少 604 秒，障碍净空至少两格。
2. render 层新增在线 navigator：以服务端位置/yaw 探针为反馈，逐段追踪 route waypoint，
   只通过真实键鼠输入行走和转向；支持横向偏差纠正、卡住释放/重对准/有限重试。
3. fail closed：位置响应过期、y 不在 63..66、偏离路线超过硬阈值、连续卡住恢复失败时，
   立即释放移动键并使 run 失败；禁止静默站住直到视频结束。
4. 每个 run 保存结构化 navigation log 和实际输入；同一 N-way cohort 共享相同规划轨迹
   SHA，但报告明确区分“同一反馈策略/路线”与旧式“逐字节相同开环输入”。
5. QA 新增路线覆盖、移动占比、停滞、恢复次数、最大偏差和跨档位置对齐；600 秒 smoke
   必须持续移动，不能只在前一分钟完成路线后静止。

### T1 — 高辨识度材质、PBR 与水反

1. 用户于 2026-07-10 明确把**真实感**设为最高优先级；性能只保留“可稳定录制”的底线，
   禁止为提高帧率主动关闭 LabPBR/POM、材质反射、高质量阴影、水面反射/折射或体积光。
2. 公开可直接执行的 look-dev 材质行至少覆盖：Legendary RT 128x、ModernArch 128x、
   Optimum Realism 64x、Patrix 32x 完整组件（Basic + Addon + Bonus + Models）和 Stylista
   128x；另保留 vanilla 参考行。全部使用官方声明 26.2 的文件，并安装 Continuity、Polytone、
   Entity Model Features、Entity Texture Features 的相同 mod 超集。
3. 光影列至少覆盖 Solas 3.7、Complementary Unbound 5.8 和 Bliss 2.1.2；每套选项必须从
   实际 ZIP 提取后固定最高写实档（LabPBR/POM、阴影、世界/屏幕空间反射、水体和体积光），
   仅关闭会破坏运动数据的 DoF/motion blur。配置 sidecar、实际 active shader 日志和代表帧
   共同作为证据，不能只证明 ZIP 被选中。
4. 正式长录前用同一世界、路线和时间充分渲染 look-dev 矩阵。输出至少两张高清 grid：
   主 grid 比较材质行 × 无光影/统一写实光影；光影 grid 在同一材质上横向比较候选 shader。
   每个候选同时保留近景材质、场景全景、水面反射和运动帧，不能只选单张有利机位。
5. 真正的终极档为合法授权的 512x/1024x 材质与 KappaPT；当前链路不得绕过付费授权。
   在用户提供文件前先完成公开 128x/64x 候选 grid。若最终需要正版本地 ZIP，必须显式记录
   许可证、SHA-512、文件大小与版本，不得伪造公开下载 URL 或把资产提交/重新分发。
6. 丰富 showcase 场景的石材、木材、植被、玻璃、水体、发光和 PBR 可见表面；扩大安全
   地面和路线边界，避免高质量 pack 只拍到单调白色测试地板。
7. 三档 profile 保持同一出生点、世界状态、反馈路线和采集规格，只改变资源/光影；正式
   预览目标 1920x1080、24fps（L40S Xorg 同步切换到 1080p）。

### T2 — Prompt-edit pair 数据轴

1. 数据集的一等单位从“若干独立 profile”提升为显式 edit pair：每条记录包含自然语言
   `prompt`、`source_episode`、`target_episode`、唯一 `edit_axis` 及机器校验过的 invariants。
2. 第一批 edit axis 至少覆盖：`material_style`（写实与强风格化材质切换）、`shader_quality`
   （无光影→高质量光影）、`time_of_day`（noon↔midnight）、`weather`（clear↔rain）和
   `snow_weather`（固定雪地 biome 下 clear↔snow）。同一 pair 只能改变声明的轴；时间、天气、
   biome 或材质的复合变化必须拆成多个 pair，或明确标成 compound edit，不能混入单变量集。
3. source/target 共享 Minecraft/commit/scene/trajectory/spawn/capture 规格。反馈导航 pair 使用同一
   policy/route 并通过时间对齐位置 QA；未来 ReplayMod 离线重渲染后升级为逐帧严格对齐。
4. 风格化 look-dev 与写实 look-dev 分开出 grid，避免“最高写实度”单一评分压掉有价值的
   艺术风格。候选必须有清晰 label、授权状态和兼容性状态；runtime 失败或只提供 demo 的包
   保留为 rejected evidence，不进入 accepted pair。
5. `dataset-index` 后续新增 pair manifest 与 fail-closed gate：拒绝 source/target 缺失、声明轴
   与实际差异不符、跨档轨迹/世界漂移、同一 episode 同时扮演冲突 target，或 prompt 未绑定
   明确 edit axis 的批次。

### 风险与完成定义

- 反馈控制会让各档实际微调输入不再逐字节相同；该批次只能在跨档位置对齐通过后标为
  `policy_aligned_rendering_matrix`，不得冒充旧的 strict open-loop cohort。长期逐帧严格方案
  仍是“导航录制一次 + ReplayMod 离线重渲染”。
- Legendary RT / Patrix / Optimum / Stylista 的 256x–1024x 版本以及 KappaPT 需要用户提供
  合法付费资产；本轮先自动使用作者公开文件完成 grid，不绕过授权，也不把公开低分辨率版本
  冒充“绝对顶配”。
- 完成定义：本地 dev_check 0 failure；真 GPU 先通过 60 秒三档 smoke，再通过至少一条
  600 秒反馈漫游稳定性验证；最终三档 10 分钟视频均为 1080p/24fps/14400 帧，移动占比、
  route、y 范围、跨档对齐、资源 runtime、shader 选项、自动 QA 和人工视觉 review 全部 PASS。

## Backlog（ITER-02+，暂不执行）

1. ~~launch_profile 编排层分解~~ ✅ ITER-02 T4 完成（RunPlan/RunState + 阶段函数）。pipeline.py 文件级拆分（plan/phases 分文件）为可选 polish，触碰该文件时顺路。
3. 轨迹相机契约从像素改成角度（`yaw_deg`/`pitch_deg`），px-per-degree 换算下沉到 replay 层并按 profile 标定；向后兼容旧字段。
4. XTEST keycode 表补全（渲染机已实际使用 XTEST backend；表仍只覆盖基础键位）。
5. 外部 policy adapter（MineRL/VPT/Voyager）：`external` 类型对接，输出统一 trajectory JSON。
6. ~~数据集打包器：扫描 runs 目录 → 汇总 episode 索引（manifest 聚合 + QA 通过标记）。~~ ✅ 2026-07-10 完成：fail-closed cohort/资源/QA/视觉验收、schema 与全文件 `SHA256SUMS`。
5b. **录制→轨迹转换管线**：捕获一段真人/智能体的输入流转成 trajectory JSON 再逐 profile 重放——接入 Baritone/VPT/MineRL 的通用入口（ITER-04 起）。
6b. **仿真/渲染加速（deferred，ITER-04+，方案由 planner 设计，coder 勿自行引入）**：目标是超实时出片。硬性要求：**加速采集的渲染结果必须与实时采集等价可互换**（同一世界/轨迹/资源下逐帧内容一致或统计上不可区分，QA 工具可验证）。候选主路线 ReplayMod 离线渲染（record-once-render-N，顺带获得完美 N-way 对齐），spike 需验证：MC 版本兼容、Iris 光影渲染、HUD 保留方案、实测速度倍率、与实时采集的等价性对比。tick-rate 加速路线因 correctness 风险已排除。在此之前，采集管线里禁止引入任何时间缩放。
7. workspace 镜像目录改成 git clone/worktree（planner 处理）。

## 等待用户/管理员的事项

- ~~4090 headless Xorg~~ **已完成（2026-07-08）**：`mcdata-xorg` 服务运行中，`:77` = RTX 4090，smoke run 已验证（见"当前状态摘要"）。
- 远端大规模采集的可写大盘仍待解决（4090 `/home` 6.0T 只剩 209G，97% 使用）。短期用回传+purge 缓解；长时段采集建议直接用 l40s（自带 298T CephFS）。
- **8 卡 L40S 容器（可选吞吐优化）**：不再阻塞数据正确性或 T3；单卡 accepted cohort 已完成。未来若需放量，仍可让容器暴露 8 卡并按 lane 分片，现有 `matrix_shard.sh`/server-port/display 隔离契约无需改动。
