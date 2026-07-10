# Terrain registry Phase 1

`configs/terrains.yml` 是 terrain family/instance 的 canonical registry。Phase 1 只登记已经能被
当前采集器真实复现并探测的 terrain，不根据 profile 名、biome、天气或截图外观猜测 terrain。
加载入口是 `mcdata.terrain.load_terrain_registry()`；它没有启动游戏、写文件或修改外部状态的副作用。

## 当前 accepted 范围

accepted 集合只有一个 instance：

- family：`showcase_plaza_flat_v1`；
- instance：`showcase_plaza_flat_v1_mc26_2_seed1_render_matrix_base`；
- Minecraft `26.2`，world seed `1`，world profile `render_matrix_base`；
- declarative scene：`configs/scene.yml`，文件 SHA-256
  `eab271710e1dc321289e6d7cce81434d213c66b9240dcb0b8395c16344f8c957`，真实 origin
  `[0, 64, 0]`；
- spawn `(0, 64, -14)`；probe bounds 为 x/z `[-16, 16]`、y `[63, 66]`；
- config evidence 绑定 `feedback_vanilla_1080p` 和 `feedback_roam_10min`。

spawn 只保存 terrain anchor 的 x/y/z。yaw/pitch 和随路线变化的相机姿态属于 action instance，
不能因为初始朝向不同就把同一个 plaza 虚增成多个 terrain instance。

scene 中有两个可见水池、墙体、树和灯，但当前 navigator 只接受手工障碍图内的单层完整方块
表面。`liquid_traversal=false` 表示路线不能进入液体，不表示画面里没有水；同理，当前不支持台阶、
坡面、落差、跳沟、攀爬、游泳或 hazard-aware routing。这个 registry 没有宣称完成 2.5D。

## Identity 与 fail-closed 规则

每个 accepted instance 保存 `identity_sha256`。哈希 payload 包含 family/instance ID、MC 版本、
world seed/profile、scene/snapshot/surface provenance、spawn、probe bounds、capability limits 以及
profile/action config bindings；编码固定为 UTF-8、JSON compact、递归 key 排序。生命周期 `status`
和已保存的哈希值本身不进入 payload。配置中没有生成时间字段，所以相同事实得到相同哈希。

validator 会同时执行：

1. Draft 2020-12 schema 校验，所有 mapping 都拒绝 unknown fields；
2. 全 registry 的 family ID 和 instance ID 去重；
3. scene project-relative path、文件内容 SHA 和 scene 内真实 origin 核验；
4. 反查 profile 的 MC/seed/world profile/spawn，以及 action strategy 的 start、x/z bounds 和 y gate；
5. 重算并比较 terrain identity SHA。

任何缺字段、unknown/timestamp 字段、重复 ID、scene 内容或 origin 漂移、config binding 漂移、
identity hash 漂移都会拒绝整个 registry。当前 origin 的 Y=64 本身是被核验的真实值；validator
不会偷偷假定 scene 从世界坐标 `[0, 0, 0]` 开始。由于 Phase 1 的二维 planner 尚未正确平移
scene 的水平坐标，即使 scene 文件、registry origin 与 SHA 彼此一致，X 或 Z origin 非零仍会被
拒绝；这个限制不是 2.5D 支持声明。

## Snapshot / surface 保留位与 blocked candidates

schema 已为 `immutable_world_snapshot` 和 `navigation_surface` 保留严格状态：

- Phase 1 两项都必须是且只能是 `status: unavailable`，禁止附带任意假 SHA；
- Phase 1 的 `source_kind` 固定为 `declarative_scene`；
- schema 为未来 available artifact 保留了严格 SHA-256 shape，但 v1 不引用它。等真实 snapshot
  内容寻址、surface dump 和对应 verifier 落地后，必须升 schema 才能接受 snapshot-based terrain。

当前 plaza 由有内容哈希的 declarative scene 重建，因此可以 accepted；但服务器 world export 和
canonical navigation-surface dump 仍明确是 `unavailable`，没有填写虚构哈希。
`plains_riverbank_flat_v1` 目前只在 `blocked_candidates` 中保存 blocker codes，没有 instance、seed、
snapshot 或 surface claim。blocked candidate 的 schema status 固定为 `blocked`，不能仅改标签就通过
accepted validation。

Phase 1 的 accepted family/instance cardinality 和 ID 也被 schema 固定为当前 plaza；复制相同
scene、改名并重算 identity hash 不能制造第二个 accepted terrain。扩容必须伴随新的真实 provenance
和 schema 版本更新，不是向列表追加一个别名。

## 不是 terrain identity 的变化

noon、golden hour、midnight、rain、snowfall、固定 precipitation biome，以及不改变 route collision
footprint 的积雪/结冰 overlay 都是同一 terrain 上的 render/world-state endpoints。它们服务 prompt
edit pair，不新增 terrain family 或 instance。若未来 overlay 改变可行走拓扑，就不再满足这条排除
规则，必须产生新的有证据 terrain identity。
