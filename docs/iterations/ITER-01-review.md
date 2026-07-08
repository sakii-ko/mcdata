# ITER-01 Review（planner，2026-07-08）

**结论：通过，merge 进 main，tag `iter-01-done`。**

## 验证过程（planner 独立复跑）

- `bash scripts/dev_check.sh`：33 passed，check_standards 0 failure / 2 warning（与 report 一致）。
- 逐任务核对 PLAN.md 验收标准：T0–T4 全部满足，证据齐全。
- 代码 review 覆盖：settings.py、manifest.py、runlog.py、qa/metrics.py、strategies.py diff、replay.py diff、cli.py diff、pipeline.py `launch_profile`、test_trajectory_contract.py。
- 产物审查：4 张轨迹图逐张目检（route 贴边、避禁区、途经点编号正确）；QA 样例 jpg 均 <300KB；compare 报告在两个真实 run 上 NCC 0.93–0.9997，对齐指标有效。
- 额外的真实环境证据：2026-07-08 planner 在 L40S 上用本分支 WIP 跑通完整采集，manifest.json 含完整 ffmpeg/ffprobe 信息，replay_log.jsonl 显示事件调度延迟约 60µs。

## 亮点

- golden 先提交、重构后提交的顺序执行到位（afcd5ba → 4d16070），git 历史本身构成行为等价证明。
- `launch_profile` 的 manifest 写在 `finally` 且带 `error` 字段——失败的 run 也有完整溯源。
- `_capture_manifest` 对 ffprobe 失败做了降级处理，不会在 finally 里掩盖原始异常。
- mid-iteration 规范变更（CaptureSettings 迁 settings.py、清 checker baseline）响应正确，且用独立 `[fix]` commit。
- `random_walk` 事件排序问题的处理方式正确：在纯函数边界修复 + 只更新对应 golden + report 里说明。

## 非阻塞 findings（记入 ITER-02 backlog，不要求本轮返工）

1. **trajectory 契约缺口**：waypoint 引入了新的 `{"pause": true}` 事件类型，replay 靠"无 key/无 mouse 字段则跳过"隐式兼容。需在 ARCHITECTURE.md 契约里补两条：`pause` 事件语义、"replay 对未知事件类型必须静默跳过"这一前向兼容规则。
2. **R16 部分未满足**：`write_run_manifest` 直接写目标文件，未走 `.tmp` + 原子 rename。低风险（run 结束才写一次），但规范明确点名 manifest，ITER-02 顺手改。
3. **R9 部分未满足**：xdotool 后端的输入注入失败仍然静默（`check=False` 无日志）；本轮只给 XTEST 后端加了 keycode warning。
4. pipeline.py 724 行 / `launch_profile` 178 行：report 已按 R19 说明理由（等观测/QA 就位后再拆），接受；分解任务保持在 backlog。
5. `settings._parse_bool` 对非法值静默取 False，可在 ITER-02 顺手改为报错，与其他 parser 一致。

## 其他

- 未配置 git remote 属预期（本地 NAS repo 即 canonical），不需要 push。
- 本 review 后 planner 将规划 ITER-02（真 GPU 3-way → 17 全矩阵 + 8 卡 L40S 扩展 + 数据回传纪律）。
