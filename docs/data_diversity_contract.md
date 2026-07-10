# Terrain × Action × Render 数据多样性契约

状态：可执行的采集设计与当前覆盖审计。本文定义怎样扩展 pair data，但不把尚未实现的自然
地形导航写成已完成能力。训练侧的 base checkpoint 保持、跨风格与域外泛化验收见
[`training_generalization_contract.md`](training_generalization_contract.md)。

## 1. 三个正交维度

数据集的基本采集单元不是一个孤立 profile，而是
`(terrain_instance, action_instance, render_pair_recipe)`。三个维度的语义如下。

| 维度 | 定义 | pair 内是否允许变化 |
|---|---|---|
| `terrain_family` | 地貌/建筑拓扑的语义家族，例如河岸、森林步道、村庄、洞穴；不是材质包名 | 否 |
| `terrain_instance_id` | 某 family 的确定性实例：MC 版本、world seed、世界快照或 scene hash、静态 biome map、出生点与可行走表面共同决定 | 否 |
| `action_family` | 控制器与运动原语家族，例如 flat feedback waypoint navigator v1；不同随机 seed 是同一 family 的不同实例 | 否 |
| `action_instance_id` | 完整 trajectory JSON 的 SHA-256；包含 route seed、路线、控制参数和出生朝向 | 否 |
| `render_edit_axis` | prompt 声明的唯一视觉变化轴 | 只允许该轴变化 |
| `render_stack_id` | resource-pack 内容哈希及顺序、shader 内容哈希和 options、client render options 的规范哈希 | 依 edit axis 规则变化 |

`terrain_instance_id` 以后应由下面的规范记录计算，而不是由目录名猜测：

```json
{
  "terrain_family": "forest_trail_flat_v1",
  "mc_version": "26.2",
  "world_seed": 12345,
  "world_profile": "forest_trail_v1",
  "world_snapshot_or_scene_sha256": "...",
  "static_biome_map_sha256": "...",
  "navigation_surface_sha256": "...",
  "spawn": {"x": 0, "y": 64, "z": -14, "yaw": 0, "pitch": 18}
}
```

当前 dataset index v2 已保存 seed、scene、spawn、trajectory 和 render provenance，足以派生
大部分 ID；`terrain_family`、世界快照和 navigation surface 仍需在多地形实现时加入一个有哈希的
`crossing_plan.json`，不能靠 profile 前缀隐式表达。

## 2. 当前覆盖审计（2026-07-10）

### 2.1 Terrain / world

- 62 个 profile 全部使用 `world_seed: 1`；61 个标为 `render_matrix_base`，一个为
  `base_scene`。这些标签没有产生 61 个不同地形。
- 所有 run 都消费同一个 `configs/scene.yml`。当前 scene SHA-256 为
  `eab271710e1dc321289e6d7cce81434d213c66b9240dcb0b8395c16344f8c957`，包含 124 个数据条目，
  展开成 126 条需要 server receipt 的 fill/setblock 命令，并派生 429 个二维障碍格。
- scene 会清空 `x,z=-30..30` 上方空间、铺平完整地面，再建 showcase plaza、双水池、三面
  gallery、树、玻璃和灯。因此它有丰富的可见材料类别，但自然 world generation 基本被覆盖，
  当前 `terrain_family` 实际只有 `showcase_plaza_flat_v1` 一个。
- 静态 biome 状态只有三种：56 个 profile 不显式覆写 biome，4 个固定为 plains，2 个固定为
  snowy plains。后两者是天气 pair 的受控环境，不应被虚报成六个新地形。
- 时间配置为 noon 59、midnight 2、golden-hour tick `12000` 1；天气为 clear 60、rain 2。
  这是光照/天气 endpoint 覆盖，不是 terrain 覆盖。

### 2.2 Action

加入本轮 seed 402–406 后，配置共有 26 个 strategy：

| action 类别 | 数量 | 当前用途与限制 |
|---|---:|---|
| `feedback_roam` | 6 | seeds 401–406；604 秒目标、两格净空、闭环、在线位置/yaw 反馈；正式长录首选 |
| open-loop `roam` | 6 | seeds 101–106；约 57–73 秒；确定但长时会累积误差 |
| scripted `astar_walk` | 5 | showcase、材质、水面、玻璃、灯光定点巡游；约 34–59 秒 |
| fixed-camera `look_scan` | 3 | pan/look-dev legacy；不能代表游走数据 |
| calibration/scripted/scene probe | 3 | 标定和 QA，不进入普通训练分布 |
| grid/random/external hook | 3 | legacy 或未接 policy；未通过当前场景的正式 pair 验收 |

六条长时 feedback 路线共用同一 scene、出生点、bounds、controller 参数、两格净空和
`target_duration_sec=604`，只改变显式 seed。它们是一个 `action_family` 下的六个
`action_instance`，不是六种控制器。

动作难度另按 taxonomy v1 解耦成四个累积 bucket，而不是从 strategy 名猜测：

| bucket | planned capabilities | accepted episode 的最低真实证据 |
|---|---|---|
| `l1` | navigation（移动/相机） | replay event 或 feedback control 必须观察到实际移动；只有 fixed pan 不合格 |
| `l1_l2` | L1 + deliberate jump | 显式语义标注且实际 dispatch 的 Space tap |
| `l1_l2_l3` | L1+L2 + deterministic block placement | 最高等级 placement 执行证据 |
| `l1_l2_l3_l4` | L1+L2+L3 + controlled combat | 最高等级 combat 执行证据 |

现有 A*/roam/feedback trajectory 在没有声明时兼容推导为 L1。navigator 的 stuck recovery 会
执行 Space + S，但只进入独立的 `controller_recovery_counts`，绝不把 episode 升成 L2。当前
placement/combat 只有保留的契约事件名，replay 会跳过并标为 `unsupported_contract_only`；在
真实执行器落地前，L3/L4 数据必须保持为空。dataset index 的 `action_buckets` 只保存每组精确、
稳定排序的 episode ID 和数量，不复制 capture；训练可以先用 L1，再逐步调整四组采样比例。

新 run manifest 从 v3 开始必须显式保存 action curriculum。已经完成的 v2 capture 不重录、不
伪造 manifest claim：若原 manifest 没有该字段，dataset loader 只能用完整 trajectory 与
replay/navigation evidence 重新归纳，并把 episode 标成 `derived_legacy_replay`；有字段的 v2 和
所有 v3 都标 `manifest` 且必须与重算结果一致。这个兼容口只针对 v2，v3 缺 claim 一律拒绝。

### 2.3 Render

配置层有 33 个 resource-pack asset 定义、8 个 shader family、46 个 asset set；62 个 profile
引用其中 45 个 asset set。覆盖同时包含 vanilla、写实 PBR/POM、强风格材质、无 shader、Solas、
Unbound、Bliss、BSL 等组合，以及 noon/golden hour/midnight/rain/snow endpoint。

这些数字只表示候选配置，不表示 accepted 数据量。资源 metadata、游戏版本、runtime activation、
单路 QA、pair invariant 和视觉 review 任一失败，都必须保留为 rejected evidence，不计入覆盖率。
当前结构明显是 render 广、action 在单一平面上开始扩展、terrain 最弱。

## 3. Pair 内锁定契约

每个 `render_pair_recipe` 展开为 source/target 两个 endpoint。两端必须锁定：

- Minecraft 版本、capture commit、world seed、world/scene snapshot、静态 biome map；
- 出生点、相机初始姿态、`terrain_instance_id`；
- strategy 类型、route seed、完整 trajectory SHA、controller 参数、`action_instance_id`；
- capture size/fps/duration、HUD 规则和 alignment mode；
- 未由 `render_edit_axis` 声明的所有材质、shader、client option、时间和天气状态。

当前允许的单轴规则为：

| `render_edit_axis` | 唯一允许差异 | 必须继续锁定 |
|---|---|---|
| `material_style` | resource-pack 内容及顺序 | shader、shader options、时间、天气、biome |
| `shader_quality` | 无 shader ↔ 一个已 runtime 激活的 shader stack | resource pack、时间、天气、biome |
| `time_of_day` | noon ↔ golden hour 或 midnight | pack、shader 及 options、天气、biome |
| `weather` | 非雪地 clear ↔ rain | pack、shader 及 options、时间、biome |
| `snow_weather` | 固定 snowy biome 下 clear ↔ snowfall | pack、shader 及 options、时间、同一 snowy biome map |

强调日光、黄昏暖光、亮月光、雨雾和雪景时，单轴 pair 的首选做法是固定同一 shader preset，
只改变可声明的 time/weather state，让 renderer 自然响应。若为每种天气额外调曝光、雾量、月光
强度或色温，这就是 `time_of_day + shader_preset` 的 compound edit；它可以作为单独的
`lighting_art_direction` 实验，但禁止混入上述单轴训练集。

闭环反馈两端共享同一计划 route 和 policy，但渲染负载可能触发不同的微纠偏，因此只能标为
`policy_aligned_rendering_matrix`，并用 positions 做时间对齐。没有 ReplayMod 式一次导航、多次
离线重渲染前，不得宣称 byte-identical action。

## 4. 稀疏 balanced crossing

不做 `terrain × action × render` 全笛卡尔积。调度器选择一组 cell，每个 cell 都必须完整渲染
对应 pair 的全部 endpoint，禁止只留下 source 或 target。

首批有至少六个可用 terrain 后，采用下列确定性不完全区组：

1. anchor action `seed401` 在每个 terrain 上覆盖所有适用的 edit axis，提供跨地形公共基准；
2. 每个 terrain 再循环分配两个 seeds 402–406：一个覆盖 material/shader，一个覆盖
   time/weather；terrain 序号的偏移按稳定排序循环，使五个非 anchor seed 的 terrain degree
   相差不超过 1；
3. snow axis 只进入已有固定 snowy-biome endpoint 的 eligible terrain；缺少合法 endpoint 时记
   `not_applicable`，不能拿普通 rain 冒充 snow；
4. 每个新 render family 先与 anchor terrain/action 相交，再由当前 degree 最低的 terrain/action
   各补一个 cell；
5. 所有选择写入稳定排序、无当前时间字段的 `crossing_plan.json`，计划哈希进入 dataset lineage。

最低覆盖目标如下；达不到时批次可以用于开发，但不能称为 balanced release：

| 指标 | release 下限 |
|---|---:|
| 每个 terrain 的不同 action instance | ≥3 |
| 每个非 anchor action seed 覆盖的 terrain degree 差 | ≤1 |
| 每个适用 edit axis 覆盖的 terrain family | ≥3 |
| 每个 render family 的 action instance | ≥3 |
| 完整 source/target endpoint closure | 100% |
| accepted cell 的 pair invariant pass | 100% |
| 单个 terrain、action seed 或 render family 占全部训练窗口 | ≤20% |

在只有一个 terrain family 的当前阶段，这些 release 条件明确不成立。新增六条 feedback route
解决的是 action seed 单点问题，不解决 terrain 泛化。

## 5. Group closure 与防泄漏 split

先建立关系图，再对连通分量分 split。以下任一关系都会把样本闭包到同一 group：

- 同一 source/target pair、同一 episode 或同一视频的相邻/重叠窗口；
- 同一 `terrain_instance_id`、world seed、scene/snapshot 或其重新编码版本；
- 同一 `action_instance_id`，以及由它派生的反向 prompt、paraphrase、抽帧和增广；
- 同一录制 endpoint 的压缩版、裁剪版、不同分辨率或 teacher cache；
- compound tuple 与其全部单轴中间 endpoint。

核心 train/dev/test 约 70/15/15 按 group 分配，绝不按帧随机切。挑战集再施加更强 family closure：

- held-out terrain：整个 `terrain_family`、它的 seeds、scene 变体和路线都不进 train；
- held-out action：至少一个 route seed，进一步评测时留出完整 controller/action family；
- held-out render：整个材质或 shader family 及版本都不进 train；
- unseen crossing：terrain、action、render family 分别在 train 出现，但指定组合只在 test；
- time/weather composition：单轴状态在 train 出现，组合 endpoint 只在 test。

同一个 world seed 出现在不同 scene family 时默认仍闭包；只有证明 scene/world snapshot 完全独立，
并在 split recipe 中显式给出原因后才可拆分。所有 split 输出 group 成员和 recipe SHA，泄漏计数
必须为 0。

## 6. 覆盖与质量指标

每次 accepted dataset 构建都报告原始计数和 degree 分布，不能只报总视频小时数：

- terrain：family/instance/seed/biome 数、每 family seeds、自然/建筑/水体/植被/洞穴占比；
- action：family/instance 数、trajectory unique rate、路线长度、独立格数、转向数、yaw/pitch
  histogram、移动/停留比例、两两 route transition 重合度；
- render：材质与 shader family 数、分辨率档、style/realism 标签、每 edit axis pair 数；
- crossing：各维 degree 的 min/median/max、最大/最小非零 degree 比、eligible cell 覆盖率；
- pair：endpoint closure、invariant pass、alignment pass、orphan endpoint、rejected reason；
- split：group 数、family 分布、重复 artifact hash 和 leakage count。

动作多样性不能只靠不同 seed 名称。每条正式 route 必须 trajectory SHA 唯一、闭环、无障碍、
达到时长；同时报告路径 transition/转向和视角分布。terrain 小到所有路线必然经过相同区域时，
cell-set Jaccard 偏高可以接受，但路线顺序、目标序列和视角统计仍必须互异。

## 7. 首批 terrain 候选

先扩充“视觉类别不同但可由当前二维 policy 安全通过”的平坦/单层走廊，再做真正高差地形。

| 优先级 | `terrain_family` 候选 | 主要视觉内容 | 当前 policy |
|---|---|---|---|
| A0 | `showcase_plaza_flat_v1` | 建筑立面、水池、玻璃、木石金属、灯光、树 | 已支持 |
| A1 | `plains_riverbank_flat_v1` | 草地、河流、泥土、芦苇/花、远景地貌 | 需新 scene/snapshot；可铺安全走廊 |
| A1 | `forest_trail_flat_v1` | 橡木/白桦/樱花林、林下植被、斑驳日光 | 需清理头部空间和树根障碍 |
| A1 | `coast_boardwalk_flat_v1` | 沙滩、海面、码头、湿地边缘、水反 | 需实体护栏和两格净空 |
| A1 | `village_courtyard_flat_v1` | 房屋、道路、农田、室外建筑语义 | 先只走室外单层路线 |
| A1 | `snowfield_frozen_lake_flat_v1` | 积雪、冰面、针叶林、snow weather | 需固定 snowy biome 与防滑/边界验证 |
| A1 | `lush_cave_gallery_flat_v1` | 洞穴石材、发光植被、地下水面、低光 | 需人工单层 gallery 和稳定 headroom |
| B | `desert_badlands_trail_v1` | 沙漠、红沙、岩层、峡谷 | 高差版等待 height-aware policy |
| B | `mountain_waterfall_v1` | 山坡、雪峰、瀑布、远景体积光 | 阻塞 |
| B | `dripstone_deepslate_cave_v1` | 钟乳石、矿物、狭窄洞穴、垂直支路 | 阻塞 |
| B | `swamp_mangrove_v1` | 浅水、根系、雾、密集植被 | 阻塞 |
| B | `interior_stairs_multilevel_v1` | 室内、门、楼梯、层间照明 | 阻塞 |

每个 family 至少准备两个 world seed/snapshot 才能称为 terrain family 泛化；只换 biome tint、
时间或天气不计作新 terrain instance。A1 中的安全走廊必须保留足够自然远景，不能再次把整个
可视区域铺成同一种测试平面。

## 8. 真实 blocker：当前 navigator 不是任意自然地形导航器

当前 planner 的状态是 `(x,z)`，A* 只有四邻域水平移动；障碍也是二维 footprint。反馈控制虽然
读取 `y`，但只把 `63 <= y <= 66` 当 fail-closed 安全门。它不知道每格 floor height、头部净空、
台阶/坡度、可接受跌落、液体、岩浆、门、梯子、藤蔓或需要 jump/swim 的动作，也不会在三维
地图上重规划。因此它能验证当前单层 plaza，却不能安全覆盖任意山地、自然洞穴、河流或建筑
楼梯。把 `y` 阈值放宽不会解决规划问题，只会把跌落录进数据。

解除 blocker 至少需要：

1. 可复现的 `(x,y,z)` navigation surface：每个节点记录支撑块、floor height、两格 headroom、
   液体/火焰/仙人掌/粉雪等 hazard、边缘跌落和世界快照哈希；
2. height-aware 邻接与 cost：平走、上/下台阶、jump、swim、门/梯子等动作原语有明确能力边界；
3. 在线 policy 使用完整 xyz/yaw，跟踪三维 corridor，发现动态阻塞时有限重规划，并对 fall、
   submerge、stuck 和错误楼层 fail closed；
4. QA 新增 vertical route completion、最大 step/fall、headroom/hazard violation、jump/swim
   事件配对和计划/实际三维偏差；
5. 每个新 terrain 先做短 smoke 和路径可视化，再做 604 秒单路稳定性验证，最后才进入 pair。

完成上述实现和真 GPU 验收前，A1 只能使用人工验证的单层安全 corridor，B 类保持 blocked。

## 9. 已落地的首批 action crossing

`feedback_roam_10min` 保持原名和 seed 401，不破坏已有 CLI/视频合同；新增 seeds 402–406。
六条路线均为同一配置模板派生，compact evidence 对 pretty-printed、sorted trajectory JSON（末尾
一个换行）计算 SHA-256：

| strategy | seed | blocks | duration (s) | unique cells | turns | trajectory SHA-256 |
|---|---:|---:|---:|---:|---:|---|
| `feedback_roam_10min` | 401 | 2636 | 610.579 | 425 | 253 | `8a7d585e…418e3156` |
| `feedback_roam_10min_seed402` | 402 | 2634 | 610.115 | 407 | 250 | `015d9ba6…7c1a1f42` |
| `feedback_roam_10min_seed403` | 403 | 2660 | 616.138 | 411 | 251 | `1cbcdc70…b6068708e` |
| `feedback_roam_10min_seed404` | 404 | 2620 | 606.873 | 430 | 261 | `c39b9772…1dedcf1a` |
| `feedback_roam_10min_seed405` | 405 | 2622 | 607.336 | 413 | 241 | `29a163c3…a0b3849` |
| `feedback_roam_10min_seed406` | 406 | 2622 | 607.336 | 412 | 249 | `00745f40…58987ffd` |

测试机械保证：配置除 seed 外相同、seeds 完整且不重复、trajectory 确定且互异、路线闭合、
时长覆盖 604 秒、两格障碍净空、边界/scene 障碍零交集、至少 350 个独立格和 200 次转向。
新增五条长路线只保存 64-byte hash evidence，不复制约五份一万行以上的无价值 golden JSON。

## 10. 推进顺序

1. 用六条 feedback instances 在现有 plaza 上建立 action seed crossing，先验证长时稳定性；
2. 实现 terrain registry、snapshot/scene provenance 和 `crossing_plan.json`；
3. 交付 A1 的至少六个单层安全 terrain，每个先通过 scene/path/smoke/604s gate；
4. 按第 4 节生成 sparse balanced pair cells，完成 source/target closure 和防泄漏 split；
5. 单独实现 height-aware map/policy 后再开放 B 类自然高差地形；
6. 训练按固定 split 比较 frozen base、MC residual、retention replay 和通用 edit pairs，分别报告
   held-out terrain/action/render 以及 MC 外 OOD，不用 held-in 风格样例代替泛化结论。
