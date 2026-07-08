# ITER-02 总验收（planner，2026-07-09）

## 结论：通过并 merge（T3 除外，为待外部依赖的运行任务），tag `iter-02-done`

## T4 验收（本次）

planner 独立复验：dev_check 全绿（85 tests）；`t4verify` 60s run 路线门 PASS（max dev 0.959 / yaw 3e-5，NVIDIA renderer）；golden/轨迹/QA 样例零变更（行为不变证据成立）；七个 commit 步步独立可回退。

结构核验：
- `launch_profile` 269 行 → **61 行协调器**，`RunOptions/RunPlan/RunState` 显式 dataclass + 12 个阶段函数（全部 <60 行，`_plan_run` 76 行可接受）；
- `server.py` 512 → 183，拆出 `scene.py`（180）/`probe.py`（173），职责单一；
- 门逻辑归位 `qa/route.py`（report.py 487→282，只剩组装）；
- 测试按特性拆分；run-matrix 循环下沉 pipeline，CLI 回归薄分发（R24）；
- `notificationDisplayTime` 收到文档最小值 0.5，帧复验无 toast。

**风格/架构评估**：本轮质量高。pipeline.py 文件行数因显式化涨到 1342（R19 文件警告仍在）——内部已是小单元集合，属"大文件、好结构"；文件级拆分（plan/phases 分文件）不作为义务，留待 ITER-03 顺路处理（若触碰该文件）。除此无问题。

## ITER-02 整体清单

- T0 review findings ✓；T1 链条（a–h，P3–P10 八层根因全根治，四路对齐 0.216–0.475 格）✓；T2 多实例并行化 ✓；T4 架构清账 ✓。
- **T3（8 卡全矩阵）为运行型任务，等用户提供容器后从 main 直接执行**（代码与 shard 脚本就绪，流程：每卡 `l40s_container_gpu_display.sh` → `matrix_shard.sh` 分片 → qa → 归档回传）。
- 遗留观察：coder 报告已跑 `collab_handoff.sh` 但 planner 信箱未见其 T4 消息（本次经用户转达）；下次交接留意 handoff 的 notify 输出是否成功。
