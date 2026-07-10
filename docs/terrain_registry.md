# Terrain registry 与 navigation surface Phase 2

`configs/terrains.yml` 是 terrain family/instance 的 canonical registry。Phase 2 在 Phase 1 的
scene provenance 上增加了可内容寻址、可重新派生的 navigation surface 基础层，但尚未把它接入
runtime controller、pipeline 或 manifest。registry 只登记当前采集器真实能重建的 terrain；不会根据
profile 名、biome、天气或截图外观猜测新的 terrain。

两个纯读取入口分别是：

- `mcdata.terrain.load_terrain_registry()`：校验 registry、绑定配置和所有 provenance；
- `mcdata.navigation_surface_artifact.load_navigation_surface_artifact()`：从 declarative scene 重新派生
  canonical 2.5D surface，并核对 schema、统计与哈希。

两者都不启动游戏、不写文件、不修改外部状态。

## 当前 accepted 范围

accepted 集合仍只有一个 instance：

- family：`showcase_plaza_flat_v1`；
- instance：`showcase_plaza_flat_v1_mc26_2_seed1_render_matrix_base`；
- Minecraft `26.2`，world seed `1`，world profile `render_matrix_base`；
- declarative scene：`configs/scene.yml`，文件 SHA-256
  `eab271710e1dc321289e6d7cce81434d213c66b9240dcb0b8395c16344f8c957`，origin
  `[0, 64, 0]`；
- surface derivation artifact：`configs/navigation_surfaces/showcase_plaza_flat_v1.json`，文件
  SHA-256 `569a71f06d697185e36ce2dffa624570afc4c6975707b75720868997932baa84`；
- 派生 surface 内容 SHA-256
  `c6d0a6e6cda4df3bd75cf006002c98da917163a7989e545ae7028633f85be5fb`；
- registry terrain identity SHA-256
  `a4d834362e54b2e364b9ec1345bec62d18b50140d2f176f5b462c857ec94875b`；
- spawn `(0, 64, -14)`；probe bounds 为 x/z `[-16, 16]`、y `[63, 66]`；
- config evidence 绑定 `feedback_vanilla_1080p` 和 `feedback_roam_10min`。

artifact 不是 server probe，也不是 world snapshot。它明确声明
`derivation.policy=declarative_scene_flat_surface_v1`，引用 scene 的真实路径、文件哈希和 origin；loader
按 scene entry 的服务器执行顺序重放 `fill` / `setblock` / `replace` 语义，再派生全部节点。当前没有
immutable server-world export，所以 `immutable_world_snapshot.status` 仍严格为 `unavailable`，没有
填写虚构哈希。

当前 surface 有 1089 个 x/z 候选列，其中 825 个 traversable；3064 条有向边全部是 `walk`，
`jump_up=0`、`drop_down=0`。825 个可走列与原 flat planner 在相同 bounds 内的
`bounds - walk_obstacles(scene)` 完全相等，因此 Phase 2 没有改动现有 plaza route 行为。

## Canonical node 与 surface SHA

canonical schema 位于
`src/mcdata/schemas/canonical_navigation_surface.schema.json`。每个节点使用 Minecraft world block
坐标 `(x, feet_y, z)`，并记录：

- `support_block`：脚底下一格；
- `feet_block` / `head_block`：玩家两格占用空间；
- `headroom_blocks`：从 feet 开始连续的精确 `minecraft:air` 数，v1 范围为 0–2；
- `fluid` / `hazard`；
- `traversable` 与稳定排序的 `rejection_codes`。

v1 每个 `(x,z)` 最多一个 surface；同列出现两个 feet height 会直接拒绝。节点按
`(x,feet_y,z)` 稳定排序后进入 canonical JSON payload，payload 还绑定 terrain instance、精确
full-block support allowlist、coordinate frame、edge policy 和 `max_surfaces_per_xz=1`。UTF-8、compact
JSON、递归 key 排序后的 SHA-256 是 `surface_sha256`；它不包含自己，也不包含时间戳。

derivation artifact 使用独立 schema
`src/mcdata/schemas/navigation_surface.schema.json`。它保存 scene provenance、probe bounds、固定 feet Y、
allowlist，以及期望 node/edge 统计和 surface SHA。registry 再绑定 artifact 文件 SHA 和 surface SHA，
所以 scene 内容、artifact 内容、派生节点或 registry 任一漂移都不能只靠重命名蒙混通过。

## Fail-closed block policy

一个节点只有在 support 精确属于受审 full-cube allowlist、feet/head 都是精确
`minecraft:air`、且三格没有 fluid/hazard 时才 traversable。以下情况一律拒绝：

- `mcdata:unknown` 或 declarative scene 没有覆盖的格；
- slab、stair、door、trapdoor、ladder、fence、wall 等非完整方块，即使调用方恶意把它加入 allowlist；
- water、lava、bubble column 或 `waterlogged=true`；
- lava、fire、magma、cactus、powder snow 等 hazard；
- feet/head 为 cave air、void air、植物或任意非精确 air；
- support 不在精确 allowlist。

这个策略有意保守。它不把未知碰撞箱、液体或危险方块解释成“可能能走”，也不从 block 名猜测
完整方块。新增 support 必须经过碰撞语义 review，并产生新的 surface SHA。

## Edge primitives、能力 gate 与稳定 A*

邻接只考虑水平 Manhattan distance 为 1 的两个 traversable column。方向边由 feet height 差唯一决定：

| `target.feet_y - source.feet_y` | primitive | 必需 action capability |
|---:|---|---|
| `0` | `walk` | `navigation` |
| `+1` | `jump_up` | `navigation + deliberate_jump`（L2） |
| `-1` | `drop_down` | `navigation` |
| `<-1` 或 `>+1` | 无边，拒绝 | — |

因此 L1 不能偷偷穿过 +1 step；只有累计能力包含 `deliberate_jump` 的 L2+ 查询才能使用
`jump_up`。blocked headroom、fluid 或 hazard node 不会收到任何邻接。A* 的 node/edge 排序、heap key、
同成本更新规则均固定；同一 surface/start/goal/capability set 不依赖输入 node 顺序，返回相同路线。

单测中的 `synthetic_step_course_unit_fixture` 仅用来证明 walk/jump/drop、L2 gate、落差拒绝、headroom、
hazard 和 tie-break；它没有 artifact、terrain provenance 或 registry entry，不能算 accepted terrain。

## Scene origin 坐标语义

scene entry 坐标都是相对 `scene.origin`，surface 节点、障碍和 action bounds 都是 world 坐标。
`scene_commands()` 原先已经对 x/y/z 做平移；Phase 2 同步修正 `walk_obstacles()` 的 x/z 平移，surface
derivation 也使用三轴 world 坐标。非零 x/z origin 的 synthetic test 会验证这一点，不再以“只支持
origin x/z=0”的 blanket rejection 掩盖坐标错误。这里没有扩展 feedback runtime controller；未来接入
多 terrain 时仍必须把 spawn、bounds、route 和 surface 使用同一 world frame 做端到端验证。

## Identity、blocked candidates 与边界

每个 accepted instance 的 `identity_sha256` 绑定 family/instance ID、MC 版本、world seed/profile、
scene/snapshot/surface provenance、spawn、probe bounds、capability limits 以及 profile/action config
bindings。生命周期 `status` 和已保存 identity hash 本身不进入 payload。validator 会执行：

1. Draft 2020-12 registry、artifact 与 canonical surface schema；
2. family/instance ID 和每列 surface 唯一性；
3. project-relative path、scene/artifact 文件 SHA、scene origin、派生 surface SHA；
4. artifact terrain ID、scene provenance、bounds、feet Y 与 registry 的交叉绑定；
5. profile 的 MC/seed/world profile/spawn 与 action 的 start/bounds/y gate；
6. terrain identity SHA 重算。

`plains_riverbank_flat_v1` 仍只在 `blocked_candidates` 中，没有 instance、snapshot 或 surface claim。
Phase 2 交付的是受控 2.5D 基础模型，不是 natural terrain acceptance：没有真实 world snapshot/probe、
多高度 canonical artifact、runtime controller wiring 和 604 秒验证前，河岸、山地、洞穴、液体、坡面、
攀爬、游泳都保持 blocked。registry 的 current plaza capability 也只列 `edge_primitives=[walk]`，不会
因为通用模块能表示 jump/drop 就虚报当前 terrain 含有垂直边。

noon、golden hour、midnight、rain、snowfall、固定 precipitation biome，以及不改变 collision footprint
的积雪/结冰 overlay 仍是同一 terrain 上的 render/world-state endpoint，不新增 terrain family 或
instance。若 overlay 改变可行走拓扑，就必须生成新的 surface evidence 与 terrain identity。
