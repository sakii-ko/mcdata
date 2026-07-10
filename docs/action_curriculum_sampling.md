# Action curriculum sampling plan

`dataset_index.json` 是采集证据的正源；训练阶段不应该复制视频或根据 profile 名猜动作难度。
`mcdata curriculum-plan` 从一个 **accepted、schema v2** 的 index 派生轻量 JSON schedule，采样单位
固定为 edit pair。每条 pair 的 source/target 必须由 episode 的
`action_curriculum.bucket` 证明属于同一个 bucket，否则整个计划失败。

四个 bucket 的含义与采集契约一致：

| bucket | 能力 |
|---|---|
| `l1` | navigation + camera |
| `l1_l2` | L1 + deliberate jump |
| `l1_l2_l3` | L1–L2 + deterministic block placement |
| `l1_l2_l3_l4` | L1–L3 + controlled combat |

## 生成计划

每个 `--ratio` 必须显式写出 bucket，四项必须都是有限非负数，且十进制和精确为 1：

```bash
mcdata curriculum-plan runs/accepted/dataset_index.json \
  --out runs/plans/stage_l1_l2_epoch000.json \
  --stage stage_l1_l2 \
  --ratio l1=0.70 \
  --ratio l1_l2=0.30 \
  --ratio l1_l2_l3=0 \
  --ratio l1_l2_l3_l4=0 \
  --epoch 0 \
  --samples 4096 \
  --seed 20260710
```

推荐的逐级实验形状可以是 `1/0/0/0`、`0.7/0.3/0/0`、`0.5/0.3/0.2/0`、
`0.4/0.3/0.2/0.1`；这些只是训练实验参数，不是采集器硬编码的默认值。任何正比例 bucket
在过滤后没有 accepted edit pair 都会失败，因此 L3/L4 数据尚未真正通过执行与证据闸门时，
不能提前生成假计划。

`--samples` 是该 epoch 展开的 pair 样本数，不是 episode 或 frame 数。整数计数由最大余数法得到；
余数相同时按固定顺序 `l1, l1_l2, l1_l2_l3, l1_l2_l3_l4` 决胜。桶内先完整遍历一次
eligible pair，耗尽后才进入下一轮 SHA-256 permutation。全局 schedule 也由 dataset ID、stage、
epoch、seed、bucket 和 ordinal 的 SHA-256 排序产生。实现不读取当前时间，也不依赖 Python 的
`random` 算法，因此相同输入会逐字节产生相同 JSON。

## 显式 pair 过滤

本工具不假设仓库已经存在 train/validation/test split，也不会从目录、profile 或 prompt 猜 split。
如果上游已经得到一组 pair ID，可以逐项传入：

```bash
mcdata curriculum-plan runs/accepted/dataset_index.json \
  --out runs/plans/train_stage_l1_epoch000.json \
  --stage train_stage_l1 \
  --ratio l1=1 --ratio l1_l2=0 --ratio l1_l2_l3=0 --ratio l1_l2_l3_l4=0 \
  --epoch 0 --samples 4096 --seed 17 \
  --allow-pair-id pair-0123456789abcdef \
  --allow-pair-id pair-fedcba9876543210
```

allowlist 必须非空、无重复且全部存在于源 index。计划会在
`policy.pair_filter={mode: explicit_pair_ids, pair_ids: [...]}` 中保存精确、排序后的 lineage；
过滤掉某个正比例 bucket 的全部 pair 会失败。需要长期维护 split 时，应由单独的、同样绑定
`dataset_id` 的 split artifact 负责，再把其中的 pair IDs 显式传给这里。

## 产物与训练读取

计划遵循 [`curriculum_plan.schema.json`](../src/mcdata/schemas/curriculum_plan.schema.json)，包含：

- 源 `dataset_id`、源 `dataset_index.json` 文件 SHA-256 和 accepted 状态；
- stage、epoch、sample count、seed、完整 ratios 和固定 policy 名；
- 每桶精确 sample count 与全部 eligible pair IDs；
- 展开后的稳定 `schedule[{sample_index, bucket, pair_id}]`；
- 对除自身外全部规范 JSON 内容计算的 `plan_id=sha256:...`。

训练 loader 按 schedule 的 `pair_id` 回查原始 `dataset_index.json.pairs`，再从 pair 的
`source_episode` / `target_episode` 回查 episode artifact；计划本身不复制视频、frame 或 run 目录。
启动训练前应同时核对源文件 SHA、index 内部 `dataset_id` 和 plan 的 `plan_id`。修改 ratio、allowlist、
stage、epoch、seed、sample count、源 index 内容或源文件字节都会得到不同 plan ID；只改变输出路径不会。
