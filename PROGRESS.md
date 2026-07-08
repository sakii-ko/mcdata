# mcdata 工作进展交接

本文档记录当前 `mcdata` 仓库的任务目标、已经完成的实现、验证结果、远端机器状态、已知阻塞点和后续接手建议。目标是让后续同事不用翻完整对话，也能直接上手继续推进 Minecraft 渲染数据采集。

> **协作入口（2026-07-08 起）**：当前的执行计划在根目录 `PLAN.md`（planner 维护，coder 按其执行）；架构分层、依赖规则、数据契约与 git 约定在 `docs/ARCHITECTURE.md`。本文档保留为长期交接背景，iteration 级别的进展看 `docs/iterations/`。

## 任务目标

用户希望构建一个 Minecraft 游戏渲染数据采集仓库，核心需求如下：

1. 在 Linux 上跑通 Minecraft 完整渲染管线，优先 Java 版。Java 版可以直接在 Linux 原生运行，不需要 Proton。Bedrock 在 Linux 上反而会引入更多兼容层，不适合作为当前主线。
2. 能以最低渲染质量跑通完整流程，然后加载不同材质包、资源包、光影、水反、emissive 效果，采集多种渲染质量的视频数据。
3. 仓库结构需要分成三类职责：
   - action 策略：负责生成行动轨迹，后续可以接入 RL / MineRL / VPT / Voyager / MineDojo 等策略。
   - 渲染逻辑：负责 Minecraft instance、server、录制、启动和矩阵运行。
   - 材质/光影管理：负责下载并安装 resource pack / shader pack / mod。
4. 对同一个世界、同一条 action 轨迹，做 3-way 或更多 way 渲染差异采集。底层游戏内容和行动必须一致，只改变渲染质量、材质包、光影、水反等。
5. 录制要求：
   - 不录制加载流程。
   - 锁定 24fps。
   - 消除黑边。
   - 保留 HUD/物品栏等有用游戏 UI。
   - 去掉任务完成、进度提示、聊天安全提示等不需要的 toast/notification。
6. action 不要漂浮在空中，要在地面真实游走，并有一定规划路径算法。
7. 支持控制天气、时间、夜晚亮度。夜晚不能黑到不可用。
8. 本地 H100 不适合真实游戏渲染，4090/L40S 才是目标渲染设备。4090 通过 SSH 使用，长任务用 tmux 持久化。

## 当前仓库状态概览

当前仓库已经具备以下能力：

- 可以 bootstrap Minecraft Java + Fabric + Sodium/Iris profile。
- 可以启动本地 dedicated server，固定 seed，offline mode，让客户端直接进世界。
- 可以在 server 侧构建固定测试场景，控制天气、时间、玩家出生位置、gamerule。
- 可以在玩家进服、场景/天气/时间应用、warmup 完成后再开始录制，避免录到加载过程。
- 可以生成并回放 action trajectory，目前主线策略是地面 A* 规划路线。
- 可以通过 `run-matrix` 对同一世界、同一 action 轨迹运行多个渲染 profile。
- 已经扩展到 21 个 profile，其中 17 个 `matrix_` profile 用于渲染矩阵。
- 已经配置 18 个 asset set，覆盖 vanilla、高清材质、emissive、connected glass、多种 shader/water/reflection 组合。
- 已经添加 headless NVIDIA Xorg 探针、root/systemd 服务模板、L40S 容器模板。
- 已经添加规划式 `/tmp` 清理脚本，并把本机新生成大文件默认迁到大盘临时目录。

## 关键文件索引

主要入口：

- `src/mcdata/cli.py`: CLI 入口，包含 `doctor`、`bootstrap`、`run`、`make-trajectory`、`run-matrix`、`remote-command`。
- `configs/profiles.yml`: Minecraft profile、世界控制、录制参数、矩阵 profile。
- `configs/actions.yml`: action 策略配置。
- `configs/asset_sets.yml`: resource pack / shader pack / asset set 配置。
- `src/mcdata/render/pipeline.py`: bootstrap、启动、录制、run dir、action replay 协调。
- `src/mcdata/render/server.py`: dedicated server 管理、世界状态控制、测试场景构建。
- `src/mcdata/actions/strategies.py`: action trajectory 生成。
- `src/mcdata/actions/replay.py`: 通过 `xdotool` 或 XTEST 回放轨迹。
- `src/mcdata/packs.py`: mod / resource pack / shader pack 安装。
- `src/mcdata/modrinth.py`: Modrinth API 查询。
- `src/mcdata/net.py`: 下载重试和进度输出。
- `src/mcdata/render/options.py`: `options.txt` 和 Iris shader 配置生成。
- `src/mcdata/doctor.py`: 环境诊断，包含 OpenGL/NVIDIA/Xorg/tmp 权限检查。

新增运维脚本：

- `scripts/mcdata_env.sh`: 设置大盘临时目录和输出目录。
- `scripts/clean_tmp_plan.sh`: 计划式清理 `/tmp`。
- `scripts/headless_xorg_nvidia.sh`: 普通用户可尝试启动/探测 headless NVIDIA Xorg。
- `scripts/install_headless_xorg_service.sh`: root 安装 systemd headless Xorg 服务。
- `docker/l40s.Dockerfile`: L40S 容器基础镜像模板。
- `docker/l40s-run.example.sh`: L40S 容器运行命令模板。
- `docs/headless_gpu.md`: headless GPU / 4090 / L40S 说明。

## action 策略实现

配置文件是 `configs/actions.yml`，实现文件是 `src/mcdata/actions/strategies.py`。

已有策略：

- `idle_pan`: 只做视角扫描。
- `look_scan`: 更长的视角扫描。
- `scene_probe`: 原先用于探测场景的动作。
- `walk_grid`: 网格巡逻。
- `random_walk`: 随机游走。
- `ground_astar_loop`: 当前主线策略，地面 A* 规划路线。
- `rl_placeholder`: 为后续外部 RL / agent policy 预留的 hook。

`ground_astar_loop` 是根据用户反馈重点实现的。它不再让玩家漂浮在空中，而是在 y=64 的地面岛上真实行走。配置如下：

- start: `[0, -14]`
- goals: 绕场景一圈后回到起点。
- bounds: `[-14, 14, -14, 14]`
- blocked_rects:
  - `[-14, -2, -5, 7]`: 水面测试区。
  - `[5, -2, 14, 7]`: 玻璃测试区。
- blocked: 光源/测试方块点位，避免路径撞上火把、灯、熔岩等。
- `turn_px_per_degree: 6.0`
- `seconds_per_block: 0.32`
- `look_pitch_px: 30`

实现细节：

- `_astar_walk` 会把 `blocked_rects` 展开成 blocked grid points。
- `_astar` 在 2D x/z 网格上做 Manhattan A*。
- `_route_segments` 会把连续同方向移动合并，避免每走一格都重新按键。
- 转向用鼠标相对移动模拟，180 度转向会拆成两次 90 度，降低突兀程度。
- 输出 trajectory JSON，包含 `route` 和 `events`。

生成轨迹命令：

```bash
PYTHONPATH=src python3 -m mcdata.cli make-trajectory ground_astar_loop --root . --out runs/trajectories/ground_astar_loop.json
```

使用大盘临时目录时：

```bash
source scripts/mcdata_env.sh
PYTHONPATH=src python3 -m mcdata.cli make-trajectory ground_astar_loop --root . --out "$MCDATA_OUTPUT_DIR/trajectories/ground_astar_loop.json"
```

已验证 `ground_astar_loop` 会生成 46 个事件，路线约 133 个 grid cells，并能在地面岛稳定游走。

## 渲染管线实现

主文件是 `src/mcdata/render/pipeline.py`。

主要流程：

1. `bootstrap_profile(root, profile_name)`
   - 根据 profile 解析 Minecraft 版本。
   - Fabric profile 会安装 Fabric API、Sodium、Iris、ModMenu、NoChatReports 等 mod。
   - 根据 asset set 下载 resource pack / shader pack。
   - 写 `options.txt` 和 `config/iris.properties`。
   - 用 PortableMC dry run 安装 launcher assets。

2. `launch_profile(...)`
   - 创建 run dir 和 metadata。
   - 可选启动本地 Minecraft dedicated server。
   - 启动 Minecraft client 并自动连接 `127.0.0.1:<server_port>`。
   - 等 server log 出现 `<username> joined the game`。
   - 调用 `apply_join_state` 应用天气/时间/玩家传送。
   - warmup `capture_ready_delay_sec` 秒。
   - 聚焦窗口，按需隐藏 HUD。当前默认 `MCDATA_HIDE_HUD=0`，即保留 HUD/物品栏。
   - 之后再启动 ffmpeg x11grab 录制，避免录到加载流程。
   - 录制开始后才释放 action replay thread。

3. `run-matrix`
   - 默认 profiles: `matrix_low,matrix_textured,matrix_shader_high`
   - 默认 strategy: `ground_astar_loop`
   - 对所有 profile 复用同一个 trajectory JSON。
   - 各 `matrix_` profile 都使用同一个 `world_profile: render_matrix_base` 和同一个 `server_port: 25570`，保证世界底层内容一致。

录制细节：

- 默认 `capture_fps: 24`。
- ffmpeg 参数使用 `-framerate <fps>`，默认 libx264、yuv420p、veryfast。
- `MCDATA_CAPTURE_SIZE=1280x720` 可覆盖录制/启动尺寸。
- `_capture_input` 优先用 `xwininfo -name Minecraft` 获取窗口位置并裁切窗口，避免录到黑边；找不到窗口时 fallback 到 display capture。
- `MCDATA_CAPTURE_DESKTOP=1` 可强制录整个 display。

## 世界和场景控制

主文件是 `src/mcdata/render/server.py`。

server 设置：

- `online-mode=false`，离线本地 dedicated server。
- `gamemode=creative`
- `difficulty=peaceful`
- `allow-flight=true`
- `spawn-protection=0`
- `server-ip=127.0.0.1`
- 固定 `level-seed`，默认 `world_seed: 1`。

`configs/profiles.yml` 的默认世界状态：

```yaml
world_state:
  time: "noon"
  weather: "clear"
  weather_duration_sec: 999999
  player:
    x: 0
    y: 64
    z: -14
    yaw: 0
    pitch: 18
  gamerules:
    command_block_output: false
    keep_inventory: true
    send_command_feedback: false
    show_death_messages: false
  scene:
    enabled: true
    origin: [0, 64, 0]
```

场景构建逻辑：

- 清空玩家附近空域。
- 建立 dirt 支撑层、grass top、smooth stone 测试地面。
- 左侧水面测试区，底部 blue concrete。
- 右侧 glass 测试区，底部 white concrete。
- 放置 oak leaves、white concrete 背景墙。
- 放置 torch、lantern、redstone_torch、lit redstone_lamp、lava、sea_lantern、glowstone、beacon。
- 放置 oak log/leaves、polished deepslate/glass marker。

这个场景用于触发：

- 水反和折射。
- 玻璃/connected glass。
- emissive/glow 方块。
- torch / redstone / lava / beacon 等光源效果。
- 天空和夜晚光照变化。

天气/时间控制：

- `_time_weather_commands` 生成 `time set ...` 和 `weather ...`。
- server 启动后应用一次，玩家进服后再应用一次，避免进服过程中状态被覆盖。
- `matrix_night_complementary` 使用 `world_state.time: midnight`，并设置 `gamma: "1.0"` / `brightness: "1.0"`，用于夜晚补充采集。

## UI/提示控制

主文件是 `src/mcdata/render/options.py`，mod 配置在 `configs/profiles.yml`。

用户要求保留物品栏/HUD，但不要任务完成、进度提示、聊天安全提示等干扰。当前处理：

- 默认不按 F1，即保留 HUD/物品栏。
- `options.txt` 中写入：
  - `chatVisibility:2`
  - `joinedFirstServer:true`
  - `notificationDisplayTime:1.0`
  - `pauseOnLostFocus:false`
  - `showAutosaveIndicator:false`
  - `showSubtitles:false`
  - `skipMultiplayerWarning:true`
  - `tutorialStep:none`
- Fabric profiles 加入：
  - `advancementdisable`
  - `no-chat-reports`

NoChatReports 是为了解决右上角 “Chat messages can't be verified” 一类 toast。已经在光影样例里验证右上角提示消失。

## 材质包/光影管理

配置文件是 `configs/asset_sets.yml`，下载实现是 `src/mcdata/packs.py`、`src/mcdata/modrinth.py`、`src/mcdata/net.py`。

下载来源：

- 当前主要走 Modrinth API。
- `latest_project_version` 会取匹配 game version / loader 的最新版本。
- Sodium 只允许 release，避免 beta 版本和 Iris 不兼容。
- 其他 mod 允许 release/beta。
- `download_file` 支持 retry、`.tmp` 临时文件、每 32MB 进度输出，失败会清理 `.tmp`。

当前 asset sets 共 18 个：

- `vanilla`
- `faithful_bsl`
- `complementary_high`
- `complementary_emissive`
- `barebones_fast`
- `default_hd_bsl`
- `default_hd128_bliss`
- `dramatic_solas`
- `faithful_sildurs`
- `emissive_makeup`
- `patrix_unbound`
- `better_leaves_solas`
- `default3d_miniature`
- `simplista_unbound`
- `stylista_bliss`
- `realiscraft_bsl`
- `glowing_ores_unbound`
- `connected_glass_bsl`

覆盖的 shader packs：

- Complementary Reimagined
- Complementary Unbound
- BSL
- Bliss
- Solas
- Sildur's Vibrant
- MakeUp Ultra Fast
- Miniature Shader

覆盖的 resource packs / effects：

- Faithful 32x
- Default HD 64x/128x
- Dramatic Skys
- Fresh Animations / Extensions / Emissive
- Visual Enchantments
- Vanilla Glowing Ores
- Emissive_TXF
- Patrix 32x
- Better Leaves
- 3D Default
- Simplista
- Stylista
- RealisCraft demo
- New Glowing Ores
- Subtly Glowing Ores
- Fusion Connected Glass
- Midnighttigger's default connected textures

## Profile 矩阵

`configs/profiles.yml` 当前 profile 总数 21。主线矩阵 profile 共 17 个：

- `matrix_low`: vanilla resources，无 shader，低质量 baseline。
- `matrix_textured`: Faithful 32x + BSL。
- `matrix_shader_high`: Faithful 32x + Fresh Animations + Complementary Reimagined。
- `matrix_night_complementary`: midnight + Complementary + emissive/dynamic lights。
- `matrix_default_hd_bsl`
- `matrix_default_hd128_bliss`
- `matrix_dramatic_solas`
- `matrix_faithful_sildurs`
- `matrix_emissive_makeup`
- `matrix_patrix_unbound`
- `matrix_better_leaves_solas`
- `matrix_default3d_miniature`
- `matrix_simplista_unbound`
- `matrix_stylista_bliss`
- `matrix_realiscraft_bsl`
- `matrix_glowing_ores_unbound`
- `matrix_connected_glass_bsl`

注意：这些 profile 通过 `world_profile: render_matrix_base` 共享 server/world。这样不同渲染质量下，世界底层内容和 action 一致。默认 `run-matrix` 只跑前三个 profile，但可以通过 `--profiles` 显式传入更多。

示例：

```bash
source scripts/mcdata_env.sh
PYTHONPATH=src python3 -m mcdata.cli run-matrix \
  --profiles matrix_low,matrix_textured,matrix_shader_high \
  --strategy ground_astar_loop \
  --duration 60
```

运行全部矩阵时可以传：

```bash
PROFILES=matrix_low,matrix_textured,matrix_shader_high,matrix_night_complementary,matrix_default_hd_bsl,matrix_default_hd128_bliss,matrix_dramatic_solas,matrix_faithful_sildurs,matrix_emissive_makeup,matrix_patrix_unbound,matrix_better_leaves_solas,matrix_default3d_miniature,matrix_simplista_unbound,matrix_stylista_bliss,matrix_realiscraft_bsl,matrix_glowing_ores_unbound,matrix_connected_glass_bsl
source scripts/mcdata_env.sh
PYTHONPATH=src python3 -m mcdata.cli run-matrix --profiles "$PROFILES" --strategy ground_astar_loop --duration 60
```

## 临时目录和大盘输出

用户要求新的内容写到 `/root/mas/bigdata1/tmp`。当前机器上实际存在的是：

- 可用大盘：`/root/nas/bigdata1/tmp`
- 不存在：`/root/mas/bigdata1/tmp`

尝试创建 `/root/mas -> /root/nas` 软链失败，因为当前用户没有 `/root` 写权限。因此当前实现采用 `scripts/mcdata_env.sh` 自动选择：

1. 如果 `MCDATA_TMP_ROOT` 已设置且可写，使用它。
2. 尝试 `/root/mas/bigdata1/tmp/mcdata`。
3. 尝试 `/root/nas/bigdata1/tmp/mcdata`。
4. fallback 到 `$PWD/.mcdata/tmp`。

该脚本会导出：

- `MCDATA_TMP_ROOT`
- `TMPDIR`
- `XDG_CACHE_HOME`
- `MCDATA_OUTPUT_DIR`
- `MCDATA_MAIN_DIR`
- `MCDATA_WORK_DIR`

`src/mcdata/paths.py` 已修改为读取这些环境变量，所以 launcher、instances、runs 都可以放到大盘。

本机已验证：

```bash
source scripts/mcdata_env.sh
```

输出：

```text
MCDATA_TMP_ROOT=/root/nas/bigdata1/tmp/mcdata
MCDATA_OUTPUT_DIR=/root/nas/bigdata1/tmp/mcdata/runs
```

并成功生成：

```text
/root/nas/bigdata1/tmp/mcdata/runs/trajectories/ground_astar_loop.json
```

## 4090 远端状态

远端 host alias：`4090`

远端项目路径：

```text
/home/lyf/mcdata
```

远端用户：

```text
lyf
```

远端 GPU：

- 8 张 RTX 4090。
- Driver: 550.67。

远端当前显示状态：

- 曾有 `Xvfb :99`，但它是 Mesa `llvmpipe` 软件渲染，只能做 automation/window/capture smoke test，不能验证真实 shader/water/reflection 性能。
- `DISPLAY=:0` 存在 lightdm/root Xorg，但当前用户没有 Xauthority，`glxinfo` 报 `Authorization required`。
- 当前用户 `lyf` 没有免密 sudo。
- 当前用户 `lyf` 不在 `video` / `render` 组。
- `/etc/X11/Xwrapper.config` 是 `allowed_users=console`。
- 普通 SSH 用户启动 headless NVIDIA Xorg 失败：
  - `parse_vt_settings: Cannot open /dev/tty0 (Permission denied)`
  - `systemd-logind: failed to take device /dev/dri/card*: Operation not permitted`

这说明 4090 上当前阻塞点是权限/登录会话，不是代码问题、也不是物理屏幕问题。

## /tmp 清理记录

4090 上 `/tmp` 一开始满盘，导致 Xorg 连 lock/socket 都无法创建：

```text
/dev/nvme1n1p2 938G used, 0 available
```

已做规划式清理：

- 使用 `find` + `lsof` 盘点。
- 只清理 `lyf` 用户拥有的临时文件/目录。
- 只清理无进程打开句柄的候选。
- 清理模式包括 `magick-*`、`tmp*`、`offload_*`、`hf_cache`、`torchinductor_*` 等明显临时缓存。
- 没有动其他用户文件。
- 没有杀进程。

最终结果：

```text
/tmp 所在根分区: 938G total, 453G used, 438G available, 51% used
```

脚本化入口：

```bash
cd /home/lyf/mcdata
MCDATA_TMP_ROOT=/dev/shm scripts/clean_tmp_plan.sh plan
MCDATA_TMP_ROOT=/dev/shm scripts/clean_tmp_plan.sh apply
```

注意：`plan` 会列出候选、大小、打开句柄和最大路径；`apply` 会再次检查打开句柄后再删除。

## Headless NVIDIA Xorg

无物理屏幕不是根本问题。Minecraft Java/LWJGL 需要的是一个 GPU-backed OpenGL display。可选方案：

1. root/systemd 启动 headless NVIDIA Xorg。
2. VirtualGL/TurboVNC。
3. 容器里或宿主机上启动 Xorg，并把 Unix socket 暴露给容器。

普通用户可尝试：

```bash
cd /home/lyf/mcdata
MCDATA_TMP_ROOT=/home/lyf/mcdata/.mcdata/tmp \
MCDATA_HEADLESS_DISPLAY=:77 \
MCDATA_GPU_INDEX=0 \
scripts/headless_xorg_nvidia.sh probe
```

在当前 4090 上仍会失败，原因是权限。

需要管理员在 4090 上执行：

```bash
cd /home/lyf/mcdata
sudo MCDATA_GPU_INDEX=0 MCDATA_HEADLESS_DISPLAY=:77 scripts/install_headless_xorg_service.sh
```

然后普通用户验证：

```bash
export DISPLAY=:77
glxinfo -B
PYTHONPATH=src python3 -m mcdata.cli doctor
```

`glxinfo -B` 必须显示 NVIDIA vendor/renderer。如果是 `llvmpipe` 或 `softpipe`，只能算 smoke test，不能用于最终高质量 shader/water/reflection 数据。

## L40S 容器判断

Minecraft Java 可以在 L40S 上跑，不需要 Proton，不需要容器套容器。关键是容器必须具备图形能力：

- NVIDIA Container Toolkit。
- `NVIDIA_DRIVER_CAPABILITIES=all` 或至少包含 `graphics,display`。
- 容器可见 `/dev/nvidia*`。
- 有 GPU-backed Xorg/Wayland display。
- 有 Java、Python、ffmpeg、xdotool、glxinfo、X11 工具。
- 足够 `/dev/shm`，建议 `--shm-size=16g`。

仓库提供：

- `docker/l40s.Dockerfile`
- `docker/l40s-run.example.sh`

推荐模式是宿主机/root 先启动 NVIDIA Xorg，然后容器挂载 `/tmp/.X11-unix`：

```bash
docker run --rm -it \
  --gpus '"device=0"' \
  --ipc=host \
  --shm-size=16g \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e DISPLAY=:77 \
  -e MCDATA_TMP_ROOT=/workspace/tmp/mcdata \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$PWD:/workspace/mcdata" \
  -v "/root/nas/bigdata1/tmp:/workspace/tmp" \
  -w /workspace/mcdata \
  mcdata-l40s:latest bash
```

如果平台允许 privileged container，也可以容器内启动 Xorg：

```bash
docker run --rm -it \
  --gpus '"device=0"' \
  --privileged \
  --ipc=host \
  --shm-size=16g \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e MCDATA_TMP_ROOT=/workspace/tmp/mcdata \
  -v "$PWD:/workspace/mcdata" \
  -v "/root/nas/bigdata1/tmp:/workspace/tmp" \
  -w /workspace/mcdata \
  mcdata-l40s:latest bash -lc 'scripts/headless_xorg_nvidia.sh start && export DISPLAY=:77 && mcdata doctor'
```

## 已录制/验证过的样例

本地样例视频路径：

```text
runs/screen_recordings/matrix_low_ground_astar_final_20260707T173901/capture.mp4
runs/screen_recordings/matrix_low_ground_astar_stable_24fps_20260707T170812/capture.mp4
runs/screen_recordings/matrix_emissive_makeup_nochat_20260707T171753/capture.mp4
```

验证过：

- 低画质地面 A* 样例：1280x720，24fps，10s，240 frames。
- 更长地面 A* 样例：24fps，24s，576 frames。
- 光影/no-chat-toast 样例：1280x720，24fps，6s，144 frames。
- `ffprobe` 确认帧率和尺寸正确。
- 右上角聊天安全提示已通过 NoChatReports 消除。
- 录制逻辑不录加载过程。
- HUD/物品栏保留。
- 远端无残留 Minecraft/ffmpeg/Xorg 采集进程。

注意：这些样例主要验证自动化、录制、action、资源加载链路。由于当前 4090 缺少可用 NVIDIA-backed display，不能把 Xvfb/llvmpipe 下的 shader 表现当作最终质量验证。

## 常用命令

安装本地开发依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

环境检查：

```bash
PYTHONPATH=src python3 -m mcdata.cli doctor
```

使用大盘临时目录：

```bash
source scripts/mcdata_env.sh
```

生成轨迹：

```bash
PYTHONPATH=src python3 -m mcdata.cli make-trajectory ground_astar_loop --root . --out "$MCDATA_OUTPUT_DIR/trajectories/ground_astar_loop.json"
```

bootstrap 单个 profile：

```bash
PYTHONPATH=src python3 -m mcdata.cli bootstrap --profile matrix_low
```

跑单个 profile：

```bash
PYTHONPATH=src python3 -m mcdata.cli run \
  --profile matrix_low \
  --with-server \
  --replay-actions \
  --capture \
  --strategy ground_astar_loop \
  --duration 60
```

跑 3-way matrix：

```bash
PYTHONPATH=src python3 -m mcdata.cli run-matrix \
  --profiles matrix_low,matrix_textured,matrix_shader_high \
  --strategy ground_astar_loop \
  --duration 60
```

远端同步：

```bash
rsync -az --delete --exclude .venv --exclude .mcdata --exclude runs ./ 4090:/home/lyf/mcdata/
```

远端检查无残留：

```bash
ssh 4090 'pgrep -au lyf -f "mcdata.cli|portablemc|ffmpeg|minecraft_server|fabric:26.2|java.*minecraft|Xorg :7[789]" || true'
```

## 验证命令

当前已运行过：

```bash
python3 -m compileall src
bash -n scripts/headless_xorg_nvidia.sh scripts/install_headless_xorg_service.sh scripts/clean_tmp_plan.sh scripts/mcdata_env.sh docker/l40s-run.example.sh
```

远端也同步并运行过脚本语法检查。

## 已知问题和风险

1. 4090 当前不能由普通 SSH 用户启动 GPU-backed Xorg。
   - 需要管理员启动 systemd Xorg 服务，或把用户加入 `video/render` 并处理 Xwrapper/logind/tty 权限。

2. 远端 `/home` 仍然较满。
   - `/tmp` 已清出 438G。
   - `/home/lyf/mcdata/.mcdata/tmp` 所在 `/home` 约有 210G 可用，但仍 97% 使用。
   - 真正大规模采集最好挂载大盘目录或配置远端可写 NAS。

3. 本机用户没有权限创建 `/root/mas` 软链。
   - 当前脚本会自动使用 `/root/nas/bigdata1/tmp/mcdata`。
   - 如果团队强依赖 `/root/mas/bigdata1/tmp`，需要管理员创建软链或目录。

4. `Xvfb` / `llvmpipe` 不能作为真实 shader 质量验证。
   - 可以验证窗口、录制、action、mod/resource/shader 启动。
   - 不能验证 4090/L40S 上的真实水反、阴影、emissive 性能和视觉效果。

5. `brightness` 选项在不同 Minecraft 版本里可能不一定被完全识别。
   - `gamma: "1.0"` 已写入。
   - 夜晚最终效果仍应在 NVIDIA-backed display 上肉眼抽帧确认。

6. `rl_placeholder` 只是预留 hook。
   - 当前尚未真正接入 MineRL/VPT/Voyager/MineDojo。
   - 下一步如果需要学习型策略，应新增 adapter，输出同样格式的 trajectory JSON。

## 下一步建议

优先级最高：

1. 让管理员在 4090 或 L40S 宿主机启动 NVIDIA-backed Xorg。
   - 4090 推荐执行 `scripts/install_headless_xorg_service.sh`。
   - 验证 `DISPLAY=:77 glxinfo -B` 是 NVIDIA renderer。

2. 在真 GPU display 上跑最小 3-way matrix：

```bash
source scripts/mcdata_env.sh
export DISPLAY=:77
PYTHONPATH=src python3 -m mcdata.cli run-matrix \
  --profiles matrix_low,matrix_textured,matrix_shader_high \
  --strategy ground_astar_loop \
  --duration 60
```

3. 抽帧检查：
   - 无黑边。
   - 不录加载过程。
   - HUD/物品栏保留。
   - 无任务完成/聊天安全提示。
   - 三个 profile 中 action 和世界内容对齐。
   - shader profile 有水反、光照、emissive 差异。

4. 扩展全 17 个 matrix profile 采集。

5. 接入更强 action 策略。
   - 短期可新增更多 scripted/A* route，覆盖水边、玻璃边、光源近景、夜晚场景。
   - 中期接 MineRL/VPT/Voyager/MineDojo，把外部 policy 输出统一转成现有 trajectory JSON。

6. 完善数据 manifest。
   - 每个 run 记录：profile、asset set、shaderpack、resourcepacks、mods、world seed、world state、trajectory hash、ffprobe 元数据、git commit/dirty 状态。

## 接手重点

如果只做一件事：先解决 NVIDIA-backed display。代码链路、资源矩阵、action、录制控制都已经基本可用；当前真正阻塞最终高质量采集的是 4090 当前用户权限不足，无法启动或访问真 GPU OpenGL display。

解决 display 后，直接按 `run-matrix` 跑 3-way，再扩到 17 profile 全矩阵。
