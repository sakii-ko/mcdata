# mcdata 代码规范

由 planner 维护。这是**硬性规范**：标注 `[checker]` 的条目由 `scripts/check_standards.py` 机械执行（dev_check.sh 的一部分，违规即红）；标注 `[review]` 的条目在 merge review 时人工执行，违规打回。规范挡路时的正确动作是在 iteration report 里提出修改申请，而不是绕过。

分层与依赖的总体规则见 `docs/ARCHITECTURE.md`；本文档是函数/文件级别的行为约束。

## 1. 配置与环境变量纪律

- **R1** 所有运行期可调参数的正源是 `configs/*.yml` 或 Settings dataclass。环境变量只允许作为部署级 override（换机器/换盘/换 display 才需要动的东西），不允许承载业务逻辑开关。`[review]`
- **R2** `os.environ` / `os.getenv` 只允许出现在三个边界文件里：`paths.py`、`doctor.py`（诊断天然要看环境）、`settings.py`（各 Settings dataclass 的 `from_env` 构造器）。其余任何 src 文件禁止直接读环境变量——需要的值在边界解析一次，显式传参进去。`[checker]`
- **R3** 新增环境变量必须：以 `MCDATA_` 为前缀、登记进本文档末尾的注册表、在 `from_env` 里集中解析并整体写入 run manifest。未登记的 `MCDATA_*` 变量视为违规。`[review]`
- **R4** 禁止在 Python 里写死本可配置的业务常量（场景坐标、端口、路径、阈值）。判断标准：换一个 profile/场景就要改代码的值，就该进 yml 或函数参数。`[review]`

## 2. 副作用纪律

- **R5** 纯逻辑（路径规划、事件生成、manifest 构建、QA 指标）所在函数不得碰文件系统、网络、subprocess、环境变量、当前时间。I/O 收敛在薄 wrapper（`generate_strategy`、`write_run_manifest` 这一层）。新代码的纯函数部分必须可以在无网络、无 X、无游戏的环境里单测。`[review]`
- **R6** 时间戳/随机数是隐藏输入：需要时间用参数传入或在最外层取一次；随机必须显式 `random.Random(seed)`，禁止模块级 `random.xxx()` 全局态。`[review]`
- **R7** 禁止模块级可变全局状态。常量表和纯函数注册表（如 `STRATEGY_BUILDERS`）允许，但注册表内容必须在 import 时就确定。`[review]`

## 3. 错误处理与日志

- **R8** 禁止静默失败。每个 `except` 要么重新 raise，要么记录带上下文的日志后走明确的降级路径。裸 `except Exception: pass/continue` 必须附带一行注释说明为什么可以忽略。`[review]`
- **R9** `subprocess` 调用默认 `check=True`；用 `check=False` 必须显式处理 returncode（记日志或分支），不允许发完就不管。输入注入类调用（xdotool/XTEST）失败至少要 warning 一次。`[review]`
- **R10** run 期间的关键节点（server start、player join、warmup、capture start/stop、replay start/end、进程退出码、terminate）必须走 `RunLogger` 落到 `pipeline.jsonl`，禁止只 print。新增 pipeline 阶段时同步补日志点。`[review]`
- **R11** 用户可见的报错信息必须包含：出错的对象（哪个 profile/文件/命令）+ 下一步排查入口（日志路径）。参考现有风格：`raise TimeoutError(f"... ; see {log_path}")`。`[review]`

## 4. 依赖纪律

- **R12** 模块间 import 遵守 ARCHITECTURE.md 的白名单表。`[checker]`
- **R13** 新增第三方依赖需要 planner 批准（在 report 里说明用途和替代方案）。优先级：stdlib > 已有依赖 > 新增轻依赖。numpy 能做的不引 scipy/opencv/skimage。`[review]`
- **R14** 函数内 lazy import 只允许两种理由：打破循环依赖（须注释说明）、可选依赖（Xlib/matplotlib 这类）。不允许用 lazy import 隐藏白名单违规。`[checker]`（lazy import 同样计入白名单检查）

## 5. 路径与文件产出

- **R15** src 内禁止出现字面量绝对路径（含 `/tmp`）。所有路径经 `ProjectPaths` 或参数传入；临时文件用 `MCDATA_TMP_ROOT` 派生目录。`[checker]`
- **R16** 可能被并发读取的 JSON 产物（manifest、trajectory）先写 `.tmp` 再原子 rename（`download_file` 已是此模式，向它看齐）。`[review]`
- **R17** 仓库不入库大文件：视频、jar、pack、世界存档禁止 commit；文档配图单张 <300KB。golden/fixture JSON 允许。`[review]`

## 6. 代码形态

- **R18** 所有公开函数带完整 type hints，文件头 `from __future__ import annotations`（与现有代码一致）。`[review]`
- **R19** 单文件超过 600 行、单函数超过 80 行时，report 里必须给出不拆分的理由。`[checker]`（超限警告，累犯打回）
- **R20** 注释只写代码本身表达不了的约束（为什么这样做、外部系统的怪癖），不写"下一行在干什么"。与现有代码的低注释密度保持一致。`[review]`
- **R21** bash 脚本必须 `set -euo pipefail`（被 source 的脚本如 `mcdata_env.sh` 除外），并通过 `bash -n`。`[checker]`

## 7. 测试与契约

- **R22** 每个新纯函数在同一批 commit 里带单元测试；改动会影响既有行为的，必须先有测试锁定旧行为（golden）再改，或在测试里证明新旧等价。`[review]`
- **R23** trajectory JSON / manifest schema / run dir 布局是对外契约：变更需要 bump `schema_version`、更新 ARCHITECTURE.md、经 planner 批准。`[review]`
- **R24** `cli.py` 保持薄分发：新子命令 = 参数解析 + 调一个模块函数，不写业务逻辑。`[review]`

## MCDATA_* 环境变量注册表

| 变量 | 用途 | 解析位置 |
|---|---|---|
| `MCDATA_TMP_ROOT` | 大盘临时目录根 | `mcdata_env.sh` / `scripts/*` |
| `MCDATA_MAIN_DIR` / `MCDATA_WORK_DIR` / `MCDATA_OUTPUT_DIR` | launcher/instances/runs 目录 | `paths.py` |
| `MCDATA_CAPTURE_SIZE` | 录制/启动分辨率覆盖 | `settings.py`（T2 迁入） |
| `MCDATA_CAPTURE_FPS` | 录制帧率覆盖 | 同上 |
| `MCDATA_CAPTURE_DESKTOP` | 强制录整个 display | 同上 |
| `MCDATA_CAPTURE_READY_DELAY` | join 后 warmup 秒数覆盖 | 同上 |
| `MCDATA_HIDE_HUD` | 录制前按 F1 隐藏 HUD | 同上 |
| `MCDATA_VIEW_SETTLE_SEC` | 聚焦后静置秒数 | 同上 |
| `MCDATA_HEADLESS_DISPLAY` / `MCDATA_GPU_INDEX` / `MCDATA_XORG_SIZE` / `MCDATA_XORG_SERVICE_NAME` | headless Xorg 脚本 | `scripts/*.sh`（不进 Python） |

新增变量在此登记（R3）。
