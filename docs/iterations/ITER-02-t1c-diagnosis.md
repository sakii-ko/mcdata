# T1c 根因诊断：X server 残留按键污染（planner，2026-07-08）

## 结论

**主因（P3，已铁证确认）：replay 截断导致 XTEST 按键状态残留在 X server 上，跨 run 污染。**

- 轨迹时长 52.09s。凡 capture duration 短于它（10s 丢弃 run、20s/30s smoke/诊断 run），或 probe 门把 replay 释放推迟致其尾部越过 capture 结束（T1b 批次的 60s run），teardown 都会在按键 hold 中途杀掉 replay 线程——**排队中的 KeyRelease 永远不会发出**。
- XTEST 注入的按键状态属于 X server（不属于客户端进程），跨进程存活。下一个 run 的新 Minecraft 窗口获得焦点后立即继承"w 按下"，玩家在 warmup 期间就自行走动，replay 开始后所有几何全错。
- 直接证据：t1craw 诊断 run 中 join-tp 成功落点 (0.5,64,-13.5)（server.log），但 replay 释放时刻玩家已在 (2.38,64,8.70)（正南方向 = yaw 0 的 w 方向）；run 结束后查询 X server keymap，**keycode 25（'w'）处于按下状态**，当时无任何 replay 在跑。
- 该机制完美复盘全部历史：
  - 第一批 run1 坏、run2-4 好：ITER-01 的 20s smoke 截断播种污染 → run1 中招；run1 自身跑完整 52s 轨迹（60s capture），key-up 全部发出 → 自愈 → 后三路干净且正确。
  - T1b 批次四路全坏：10s 丢弃 run 播种 + probe 门偏移 2–10s 使每个 run 的 replay 尾部（恰是收尾 key-up 密集段）越过 60s capture 被截断 → **逐 run 传染**。
  - T1c A 组坏：30s 必截断，链条延续。
- **我在 T1b 规定的"10s 丢弃 run"规程每次都在播种污染——planner 规格 bug，正式撤销。**

## 次要观察

- P1（join-tp 竞态）不成立：tp 有 server.log 回执且落点正确；warmup 期漂移由 P3 解释。T1b 的 capture 前 re_apply 保留（它仍是 t=0 状态一致性的保险）。
- P2（指针边缘停靠/转向截断）：sidecar 观测到 gameplay 期指针 66% 时间停在 x=0，但该 run 本身被 P3 污染，尚不能归因；待 P3 修复后用干净 run 复测（诊断工具 `scripts/pointer_probe.py` 已入库）。GPU 竞争（邻居任务 8.8GB、util 50%+）作为背景风险继续观察。
- rawMouseInput 硬化：manually 在 4090 matrix_low 实例开启过，未见副作用；正式改入 options.py（见 T1c 步骤 3）。

## 遗留现场状态

- 4090 X server keymap 已由 planner 清理（释放 keycode 25），当前干净。
- 4090 `matrix_low` 实例的 options.txt 被 planner 手动加了 `rawMouseInput:true`——下次 bootstrap 会按 options.py 正式规则重写。
- `pointer_probe.py` 临时副本在 4090 项目根目录，可删（repo 版本在 scripts/）。
