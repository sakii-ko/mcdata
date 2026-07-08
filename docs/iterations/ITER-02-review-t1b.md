# ITER-02 T1b Review（planner，2026-07-08）

## 结论：代码项通过，数据验收 FAIL，开 T1c

T1b 的七个实现项逐条符合规格（re_apply、位置探针、qa 位置门、`--game-version`、`sync_to_remote.sh` + `.sync_commit`、丢弃 run 规程、重采流程均执行）。commit 划分、Role trailer、report 质量继续保持高水准，尤其**报告如实写了 t=30 是 "water/kelp camera position"——这份诚实让 review 很快定位了问题**。

但重采的四路数据全部无效：

- 位置轨迹（`matrix_low_positions.jsonl`，四路一致）：idx0 起点正确 →idx1 (7.6,-12.0) 仍在路线上 → **idx2 (15.7, 0.3) 已越过理想路线 x≤12 的范围** → idx4 (20.8, 22.4) 走下海岛 → idx5 起 y 从 64 跌到 41，在天然海洋里下沉。四路偏差 0.42 格 = 四路犯了同一个错。
- 代表帧证实：t=30 四路都在深海海带林里。正确的 t=30 应为平台上（对照第一批 textured/shader/night 与 `docs/trajectories/ground_astar_loop.png`）。
- 失败模式：第一段路（向东至 (12,-11) 拐角）与第一次转向执行正确，**自第二个拐角起转向丢失/不足**，此后近似直线冲出场景。
- 第一批（无 T1b 改动）textured/shader/night 三路在同一轨迹下是正确的 ⇒ 回归由 T1b 批次引入（代码或批次条件），嫌疑面：capture 前二次 re_apply、replay 等待首个探针样本的门、探针命令本身。replay.py 未变、trajectory sha 一致、事件投递延迟 ≤20ms 已排除输入侧。

## 规格责任说明（planner 自查）

T1b 位置门由我定义为"四路相互对齐 ≤2.0 格"，未锚定 ground truth——这是规格缺口，使"集体跑偏"可以通过。T1c 引入**路线基准门**（每路与轨迹 JSON 推演的理想轨迹对照）作为系统性修复。执行侧的对应教训：代表帧要对照预期内容（路线可视化图）检查，不能只看四路互相一致；"在海底"与路线图明显不符时应当立即上报而不是继续走验收流程。

## 处置

- `docs/qa_samples/iter02_4090_3way/` 当前内容标记为无效数据，禁止引用；T1c 重采后整体替换。
- T1c 已写入 PLAN.md（规格 planner 定死）：步骤 0 先做零成本诊断（对比第一批与 T1b 批次的 pipeline.jsonl 时间线），再 A/B/C/D 四组隔离实验定位回归机制，路线基准门落地后全量重采。
