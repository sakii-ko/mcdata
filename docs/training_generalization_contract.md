# Prompt-edit 训练与泛化评测契约

状态：设计契约，尚未实现训练器或模型。本文只规定后续训练实现必须消费什么数据、保留什么
能力以及怎样验收；它不表示当前采集仓库已经训练出模型，也不表示已经实现 Minecraft（MC）
以外的泛化。

## 1. 目标与非目标

目标模型接收 source 图像或视频片段和自然语言 prompt，输出保持原内容、相机运动与时序，只
完成指定视觉 edit 的结果。首批受控轴为 `material_style`、`shader_quality`、`time_of_day`、
`weather` 和 `snow_weather`。训练必须同时追求：编辑成功、内容保持、视频时序稳定、未见材质/
shader/组合泛化，以及原始 base checkpoint 能力保持。

这里的“保留 base 能力”有两个不同强度：

1. **强保证**：base 参数逐字节冻结，edit branch 可被完全旁路；在相同输入、seed、精度和
   sampler 下，旁路输出必须与原 base 在数值容差内相同。
2. **工作模式保持**：edit branch 开启时，通用 prompt、identity/no-op prompt 和非 MC 输入也
   不出现显著退化。这只能通过 replay、distillation、loss 和评测来约束，不能由“用了 LoRA”
   自动推出。

MC-only pair 可以检验和提升 MC 内的组合泛化，但不能证明模型会编辑自然图像。要声称 MC 外
编辑能力提高，训练集必须加入有合法来源的通用编辑 pair；只用 base 伪标签主要用于防遗忘，
不能替代真实的域外编辑监督。

## 2. 模型无关的参数契约

本契约不绑定 diffusion、flow matching、autoregressive 或某个训练框架。任一实现都必须满足
下面的行为接口：

- base checkpoint 只读加载并记录不可变 ID、文件哈希、精度和推理配置；训练产物只保存
  LoRA/adapter 或独立 residual edit branch。默认禁止把 adapter merge 回唯一一份 base。
- edit 计算可写成概念式 `F_edit = F_base + g(x, p, a) * Delta(x, p, a)`；`Delta` 可以位于
  latent、denoiser、attention 或输出空间，但必须零初始化或以其他方式保证训练开始时近似
  base。`a` 是可选 edit-axis condition，不能替代自然语言 prompt。
- 必须暴露 `adapter_scale=0` 或等价的 exact-bypass 路径。空 prompt、明确 no-op prompt 和
  OOD fallback 可以使用该路径；自动 gate 不是唯一入口，调用方必须能显式选择 base。
- 推荐把 residual 分成共享 trunk 与按 axis 条件化的低秩分支。单轴样本只更新其声明轴和
  共享部分；组合 edit 通过多个轴的条件组合，而不是把材质、时间和天气暗中编码成一个类别。
- checkpoint 必须绑定 base 哈希、adapter 哈希、训练 dataset ID 列表、split recipe 哈希、
  代码 commit、随机 seed 和完整 metric card。缺任何一项的 checkpoint 不得进入候选比较。
- 若以后尝试 full fine-tuning，必须作为单独实验，并通过本文全部 retention gate；它不能覆盖
  冻结 base + residual 的基准线。

## 3. 消费 dataset index v2

训练入口消费已打包数据根目录中的 `dataset_index.json`，而不是扫描 profile 名或猜测文件名。
读取顺序必须 fail closed：

1. 执行并通过 `sha256sum -c SHA256SUMS`；验证 index `schema_version == 2`。正式训练只接受
   `status == "accepted"`；`automated_pass` 只可用于不发布的开发 smoke。
2. 通过 `pairs[*].source_episode` / `target_episode` 关联 `episodes[*].episode_id`，再从 episode
   的 `video.path`、`positions.path`、`trajectory.path` 和 manifest artifact 取数据。路径始终
   相对 dataset 根目录，训练代码不得硬编码 run 目录布局。
3. 保留 `pair_id`、`prompt`、`edit_axis`、`axis_values` 及全部 `invariants` 作为 lineage。
   训练时不得自行重判一个 compound 差分为单轴 pair，也不得使用 `accepted != true` 的 episode。
4. `strict_rendering_matrix` 可按相同 timestamp/帧号取样；`policy_aligned_rendering_matrix` 必须
   使用 positions 的时间插值做对齐，并丢弃超过该批 QA 阈值的窗口，不能假装逐帧 open-loop
   对齐。`world_state_variant` 只有在对应 pair invariants 通过时才可跨 cohort 配对。
5. source、target、同一 pair 的所有帧和同一轨迹的相邻窗口必须进入同一 split。禁止把一段
   10 分钟视频随机切帧后分散到 train/test。

训练加载器生成的派生记录至少包含：`dataset_id`、`pair_id`、`direction`、source/target episode
ID、时间窗、prompt、`edit_axis`、`axis_values`、alignment mode 和 invariant hashes。这个训练
view 是派生产物，不回写或放宽采集侧 pair schema。

## 4. Factorized 训练配方

### 4.1 单轴、双向与 prompt

- 受控单轴 pair 始终是主监督。一个 batch 中不能把未声明的材质、shader、时间或天气变化
  当作 target 噪声吞掉。
- 每个可逆 pair 同时训练正向和反向；反向记录交换 source/target，并使用人工审阅过的反向
  prompt，例如 “remove the shader while keeping the scene unchanged”。采集 schema 当前只允许
  no-shader 到 shader 的 `shader_quality` 声明，所以反向样本只存在于带原 `pair_id` lineage 的
  训练 view，不能伪造成新的 accepted pair。
- 每个 canonical prompt 准备描述相同目标的未见 paraphrase。至少一半 prompt 描述视觉属性
  而不是 pack/shader 品牌名；品牌名 prompt 不超过该轴样本的 25%，避免模型只记产品标签。
  test prompt 的措辞不得出现在训练中。
- 双轴训练只使用确实录制了 source 和最终 target、且每个中间单轴变化都可验证的 tuple；没有
  target endpoint 时禁止用链式模型输出冒充 ground truth。compound tuple 记录在独立训练/
  评测 recipe 中，不得塞进当前单轴 `edit_pairs.json`。

### 4.2 默认采样混合

在每 1,000 个优化样本的滑动窗口内使用下面的初始配方，并记录实际计数：

| bucket | 比例 | 作用 |
|---|---:|---|
| MC canonical 单轴正向 | 45% | 学习受控 edit |
| MC 单轴反向 | 15% | 避免只会固定方向或固定 target |
| identity/no-op（MC 与通用域各半） | 10% | 抑制无关改写和 adapter 漂移 |
| frozen-base replay/distillation | 15% | 保留 base 的通用响应分布 |
| 有合法来源的通用编辑 pair | 15% | 训练 MC 外编辑能力 |

MC edit bucket 内各 axis 的样本量控制在平均值的 ±10%，每个可逆轴的正反方向落在 40:60 到
60:40 之间，并限制单个 episode 的窗口上限。若尚无通用编辑 pair，最后 15% 改为 base replay
和通用 no-op；该 run 只能声称“域外能力保持”，不能声称“域外编辑能力提升”。

有真实 endpoint 的双轴样本最多替换 5% canonical MC bucket，且总的单轴监督仍须占全部 edit
监督的至少 80%。先证明单轴可分解，再增加组合比例。

### 4.3 Condition dropout 与 loss

- 视觉 source condition 不做随机 dropout。结构化 axis tag 以 50% 概率 dropout，使自然语言
  prompt 仍是充分条件，避免部署时依赖采集元数据。
- 支持 classifier-free guidance 的后端可使用 10% 文本 condition dropout，但必须采用该后端
  明确定义的 unconditional 训练目标；不能把“prompt 被丢弃、target 仍随意改变”解释成 no-op。
  no-op 必须由独立 identity 样本监督。
- 编辑监督 loss 由后端原生生成/重建 loss、prompt/edit success loss、内容结构保持 loss 和视频
  temporal loss 组成。对全局光照、材质和天气 edit，普通像素差不能单独充当内容 loss；应使用
  几何/边缘、特征对应、光流或可见区域一致性。
- identity/no-op loss 要求输出接近 source，同时使 residual/gate 接近零。它覆盖空 prompt、
  “keep unchanged”、不支持的 edit 和通用非 MC 输入。
- replay/distillation 固定一个 frozen-base teacher，在相同 source、prompt、noise/seed 和 sampler
  条件下约束 student 的原生预测量或最终输出。teacher cache 必须记录 base 哈希和 seed；禁止
  用正在更新的 student 自蒸馏。
- 通用 replay prompt 集应覆盖 base 原有的生成、编辑、风格、局部保持和拒绝/no-op 行为。
  仅优化平均 distillation loss 不够，必须保留按域和 prompt 类型分片的 retention 指标。

loss 权重由首轮 calibration 决定，但必须在看 test 结果前冻结；checkpoint 选择采用第 7 节的
多目标 gate，不允许只按 MC edit loss 选最低点。

## 5. 防泄漏 split 与挑战集

先生成有哈希的 `split_recipe.json`，再启动训练。核心 train/dev/test 建议按 group 约 70/15/15
分配；比例可以因 family 数量取整，但禁止为凑比例拆 group。group closure 至少包含同一
pair、episode、世界 seed、scene、trajectory、相邻时间窗和派生反向/paraphrase。

除核心 test 外，固定以下不进入训练的挑战集：

| 挑战集 | 隔离规则 | 回答的问题 |
|---|---|---|
| held-out material family | 整个材质家族及其分辨率/版本均不在 train | 能否泛化到未见材质语言和纹理统计 |
| held-out shader family | 整个 shader 家族及 preset 均不在 train | 能否泛化到未见光照、水反和体积效果 |
| unseen renderer combination | pack 与 shader 各自在 train 出现，但该二元组合不出现 | 能否组合已知因素而非记 profile |
| held-out world/seed | seed、scene 和 route family 整组隔离 | 是否只记住 showcase 几何和镜头 |
| held-out time/weather combination | 单独的时间和天气状态在 train 可见，组合 endpoint 只在 test | 能否组合黄昏/月光与雨雪氛围 |

材质与 shader 的 `family_id` 由人工维护的 split recipe 显式给出，并绑定实际资源哈希；不得在
训练时临时按文件名模糊匹配。unseen combination 的两个组件都必须在 train 有足够单独支持，
否则它应归类为 family OOD 而不是 composition OOD。

所有实验使用相同 split，至少跑 3 个训练 seed，报告均值、标准差和最差 seed。test 只在配置、
loss 权重和 checkpoint 选择规则冻结后运行一次；迭代调参只看 dev。

## 6. MC 外三级 OOD

域外能力按距离分层报告，不能合成一个好看的平均数：

1. **OOD-1：其他 voxel/block game 或独立 voxel render。** 几何与 MC 接近，但材质、UI、
   光照器不同。可以首先检验材质、昼夜和天气语义迁移。
2. **OOD-2：stylized low-poly 游戏/渲染。** 保留明确的 3D 几何与相机运动，但不再有 block
   网格；重点检验内容和时序保持。
3. **OOD-3：natural images/videos。** 摄影图像、真实天气、室内外照明及人物/物体边界。这是
   最远分布，必须独立评测安全性和身份/结构保持。

每一级都准备 identity/no-op、base protected prompt 和真实 source/target edit 三类集合，并按
内容来源/许可证分组。MC-only 模型只允许报告 OOD retention；只有加入通用编辑监督、在冻结
challenge set 上显著超过 frozen base 且通过保持 gate 后，才能报告对应级别的 edit
generalization。自然图像上的少数精选成功案例不能替代集合指标。

## 7. 指标与 go/no-go

所有分数先在 dev 上校准到 `[0, 100]`（越高越好），并在 metric card 中冻结计算方法、模型
版本和阈值。自动语义分数必须用至少 200 个样本的人评校准；最终同时报告自动指标和盲评，
不得只报 CLIP 相似度。

- **Edit success (ES)**：prompt 方向、目标 axis 属性和禁止变化属性分别评分；强风格、日光、
  黄昏、亮月光、雨雾、雪景各自成 slice。
- **Content preservation (CP)**：结构/边缘、特征对应、相机几何和未指定语义保持。对视频还要
  检查 source/target 对齐窗口，避免把导航漂移算作模型编辑。
- **Temporal preservation (TP)**：基于 source 光流的 warp consistency、静态区域 flicker、
  长片段身份漂移和切换突变；雨雪粒子等合理随机运动使用单独 mask/区域规则。
- **Base retention (BR)**：adapter-on 与 frozen base 在通用 protected suite 上的质量比、同
  seed teacher divergence、identity/no-op drift，以及 exact-bypass 数值一致性。
- **Generalization (GEN)**：第 5、6 节每个 challenge slice 的 ES/CP/TP，外加组合 edit 的
  joint success；不得用 held-in 平均分代替。

首个实现采用以下默认发布 gate；若 metric calibration 表明单位不适用，只能在第一次训练前
修改并提交新的 recipe 哈希：

1. 数据 gate：所有训练数据 index 为 v2、checksum 通过、状态 `accepted`，且无 split group
   泄漏；否则不训练。
2. base gate：exact-bypass 在确定性设置下与 base 最大绝对误差不超过 `1e-6`；adapter-on 的
   BR 总分不低于 base 的 98%，任一 protected slice 不低于 95%；identity/no-op 的 CP/TP 均
   不得比 base 低超过 2 分。
3. MC gate：held-in ES 至少比 frozen base 高 5 分；CP 和 TP 各自不得比 frozen base 低超过
   2 分。held-out world/seed 的 ES 至少达到 held-in 的 90%。
4. family gate：held-out material 与 held-out shader 各自的 ES 至少达到 held-in 的 85%，且
   均比 frozen base 高至少 3 分；CP/TP 继续满足 2 分退化上限。
5. composition gate：unseen renderer 和 time/weather 组合的 joint ES 不低于对应两个单轴 ES
   较小值减 10 分；禁止变化轴的保持分不低于 95。
6. OOD gate：每一级 adapter-on BR 不低于该级 frozen base 的 95%。只有某级真实 edit 集的 ES
   比 frozen base 高至少 3 分，同时 CP/TP 不低超过 2 分，才可声称该级“编辑泛化提升”。
7. 稳定性 gate：上述 gate 必须由 3 个 seed 的均值通过，且最差 seed 不得越过阈值 2 分以上。

任何 base protected slice 明显倒退、依赖 exact pack 名才能工作、只在一个 world/seed 成功，
或 MC 外仅展示 cherry-picked 样例，都判为 no-go。失败模型可以保留为实验产物，但不得成为
默认 checkpoint。

## 8. 最小实验序列与声明边界

按同一 base、数据 split、训练步数和 seed 比较以下 ablation：

1. frozen base（B0，不训练）；
2. B0 + MC-only residual/LoRA；
3. B0 + MC pairs + identity + base replay/distillation；
4. B0 + 上述全部 + 有合法来源的通用编辑 pairs；
5. 可选 factorized axis branch 与 shared-only adapter 对比。

第 2 项回答小 adapter 是否足够，第 2→3 项量化防遗忘机制，第 3→4 项才检验 MC 外编辑监督的
增益。最终 checkpoint 必须在 ES、CP/TP、BR、GEN 的 Pareto 可行集中选择；任一硬 gate 未通过
时，不得用另一项高分抵消。

当前仓库已经能产出经过机器校验的 dataset index v2 pairs，这是该契约的数据入口；目前没有
训练实现、通用编辑数据、三层 OOD challenge set 或通过上述 gate 的 checkpoint。因此现阶段
可以开始设计训练器和准备 split/评测资产，但不能把“跨未见 MC 风格泛化”或“MC 外泛化”标成
已完成。
