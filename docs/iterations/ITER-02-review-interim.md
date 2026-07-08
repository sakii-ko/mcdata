# ITER-02 中期 Review：T0/T1/T2（planner，2026-07-08）

## 结论

- **T0：通过。** 四项 findings 修复到位（报告 `a938874`），dev_check 全绿。
- **T2：通过。** 逐条对照 PLAN 规格核验：`apply_display_override` 签名/位置、`--display/--server-port/--lane` 三参数（run 与 run-matrix 均有）、profile overlay、lane 对 run dir / server dir / trajectory 路径的命名、manifest `lane` 字段 + schema v2、ARCHITECTURE 同步、`matrix_shard.sh` 全部符合；并发隔离测试 planner 本机复跑通过；缺 instance 的 fail-fast 行为符合设计。Pillow 兼容 follow-up（`826a5e7`）处理得当。
- **T1：流程通过，数据验收不通过（部分）。** 详见下文。四路采集中 `matrix_textured` / `matrix_shader_high` / `matrix_night_complementary` 三路相互精确对齐（planner 逐帧目检 t=30s 同机位同位置），**`matrix_low` 一路中途偏航**：t≈12s 走进玻璃测试区，t≈30s 已沉入水下。该路数据不可用，3-way 必须重采。

## T1 失准的证据链与根因判断

1. QA 数据早有信号：`textured_vs_shader` NCC mean 0.78，而所有与 low 的对比降至 0.29–0.43 且出现负值——指向 low 单路异常（这正是 qa-compare 存在的意义，以后此模式=一路坏了）。
2. 三 run 的 trajectory sha256 完全一致、replay 46 事件全数按时投递（lateness ≤20ms）、GL renderer 均为 NVIDIA——输入意图与时序无差别。
3. 因此差异发生在游戏侧对相同输入的响应。matrix_low 是 rsync 后**第一次冷启动**（JVM/资源/chunk 全冷），warmup 固定秒数不足以保证输入就绪，早期转向的鼠标增量疑似被部分吞掉 → 恒定航向偏差 → 整条路径旋转 → 入水。此判断待 T1b 的位置探针数据最终确认。
4. 卫生问题：pulled 目录里有两个带空格的 stray 目录（`..._matrix_low runs`、`..._matrix_textured runs`），是远端手工命令缺引号所致；本地删除即可。T1 靠手工 python API 直调（绕过 Modrinth 故障）也是诱因——T1b 起一律走 CLI。

## Review Focus 的答复

- `--game-version` CLI 参数：**批准**，纳入 T1b。
- rsync 无 .git 导致 manifest 无 provenance：**批准**修复，纳入 T1b（`.sync_commit` 方案）。
- `matrix_shard.sh --no-bootstrap` fail-fast 策略：确认，T3 照此执行。

## 处置

T1b（修复+重采）已写入 PLAN.md，接口级规格由 planner 定死。T2 代码可直接用于 T3，无需返工。
