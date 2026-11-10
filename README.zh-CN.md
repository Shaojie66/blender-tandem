[English](README.md) | 简体中文

# blender-tandem

用两台 Mac 同时渲一段 Blender 动画。主力机从第一帧往后渲，第二台从最后一帧往前渲，碰头结束。

它是给个人用的：手上有一台主力 Mac，还有一台闲着的，比如用雷雳线连着的笔记本。不需要服务端，不需要共享盘，第二台上除了 Blender 什么都不用装。你在主力机上渲别的东西时（预览、另一个镜头、视口），它会自动让开，第二台继续渲。

它不是渲染农场。只支持两台 Mac，不支持 Linux 和 Windows。

## 工作方式

```
帧       1                                                    240
本机     1 2 3 4 ... ──────────▶          ◀────────── ... 239 240    第二台
                               在这里碰头
```

**分帧。** 每台机器开渲一帧之前，先把帧号写进自己的游标文件。两边每 10 秒交换一次游标，谁走到对方的游标就停。你不用告诉工具两台机器各有多快：快的那台自然走得更远。最多只有碰头的那一帧会被渲两次。

**让位。** 主力机会留意本机上有没有别的程序在渲染（默认只看 Blender）。一旦有，本机的 worker 就停下：手上这帧预计还要 20 秒以上就立刻放弃，否则渲完这帧再停。worker 会直接退出，GPU 和内存都空出来。对方安静满 60 秒后，新的 worker 自动启动，接着渲还缺的帧。第二台不受影响。加 `--rush` 可以关掉让位。

**第二台只在有用时才启动。** 主力机先自己渲 30 秒。按这段时间的实际速度算，剩下的还要 2 分钟以上，才启动第二台。如果开渲时主力机正在让位，第二台立刻启动。

**第二台掉线。** 连续约一分钟连不上，或者 30 分钟没有出新帧，就放弃它，它没渲完的帧由主力机接手。

**冻结的自包含快照。** 作业开始时，`.blend` 会被复制到作业目录里，图片、字体、声音、链接的库都打包进去。两台机器渲的都是这份副本，所以只需要传作业目录；之后再改原文件，也不会影响正在渲的作业。

## 要求

- 两台 Mac。工具用到 `ps`、`caffeinate` 和 Metal。
- 两台装同一版本的 Blender，每次开渲前都会检查。
- 主力机上有 Python 3.11 或更高版本，不需要第三方包。
- 主力机能用密钥免密码 ssh 到第二台。
- 可选：主力机上有 `ffmpeg` 和 `ffprobe`，用于 `--encode`。

## 安装

```bash
uv tool install git+https://github.com/Shaojie66/blender-tandem
```

或者

```bash
pipx install git+https://github.com/Shaojie66/blender-tandem
```

也可以克隆后不安装，直接运行：

```bash
PYTHONPATH=src python3 -m blender_tandem status
```

## 快速开始

**1. 准备第二台。** 在系统设置里打开「远程登录」，装上同一版本的 Blender（把 `Blender.app` 直接拷过去最省事）。在主力机上，下面这条命令必须不弹密码提示就能执行：

```bash
ssh second-mac true
```

**2. 告诉工具第二台是谁。** 新建 `~/.config/blender-tandem/config.toml`：

```toml
[remote]
host = "second-mac"
```

两台 Mac 用雷雳线直连的话，再加一行 `interface = "bridge0"`，让它走线而不是走网络。

**3. 检查两台机器。**

```bash
tandem status
```

**4. 开渲。**

```bash
tandem render scene.blend
```

帧会出现在 `.blend` 旁边的 `tandem-jobs/scene-<id>/frames/` 里。没有配置 `[remote]` 时，同一条命令只在本机渲，让位照常生效。

## 命令

### `tandem render`

```bash
tandem render scene.blend [--frames A-B] [--percent N] [--samples N] [--job-root DIR] [--name NAME]
                          [--local-only | --remote-only] [--rush] [--encode OUT.mp4 [--fps N] [--crf N]]
```

| 选项 | 含义 |
|---|---|
| `--frames A-B` | 要渲的帧。默认用场景自己的帧范围。 |
| `--percent N` | 分辨率百分比。默认用场景自己的。 |
| `--samples N` | Cycles 采样数。默认用场景自己的。 |
| `--job-root DIR` | 作业目录放在哪。默认是 `.blend` 旁边的 `tandem-jobs/`。 |
| `--name NAME` | 作业名。默认是 `.blend` 的文件名。 |
| `--local-only` | 只用本机。 |
| `--remote-only` | 只用第二台。 |
| `--rush` | 本机不给别的程序让位。 |
| `--encode OUT.mp4` | 所有帧渲完后，用 ffmpeg 编成 H.264。 |
| `--fps N` | `--encode` 的帧率。默认用场景自己的。 |
| `--crf N` | `--encode` 的 x264 画质。默认 18。 |

引擎是 Cycles 时，worker 会启用这台 Mac 的全部 Metal 设备（GPU 和 CPU）。其他引擎按场景自己的设置渲。

Ctrl-C 会让两台机器立刻停下。已渲完的帧保留，重跑同一条命令就是续渲。

一台 Mac 同时只跑一个作业，第二个 `tandem render` 会等第一个结束。

退出码：0 表示所有帧都渲完，1 表示还有帧没渲完，130 表示被中断。

### `tandem status`

```bash
tandem status
```

显示两台 Mac 的 Blender 版本、第二台是怎么连上的、它是否空闲。

### `tandem jobs`

```bash
tandem jobs [DIR]
```

列出 `DIR`（默认当前目录）下的作业目录，以及各自的帧数和占用空间。

### 换一个配置文件

```bash
tandem --config other.toml render scene.blend
```

## 配置

配置文件是 `~/.config/blender-tandem/config.toml`，也可以用环境变量 `TANDEM_CONFIG` 或 `--config` 指定别的文件。所有设置都可以不写。完整样例见 [`examples/config.toml`](examples/config.toml)。

| 设置 | 默认值 | 含义 |
|---|---|---|
| `[local] blender` | `/Applications/Blender.app/Contents/MacOS/Blender` | 本机的 Blender。 |
| `[remote] host` | 无 | 第二台的 ssh 主机名或别名。不写就只用本机。 |
| `[remote] blender` | 和本机相同的路径 | 第二台的 Blender。 |
| `[remote] dir` | `tandem-jobs` | 作业目录在第二台上的位置，相对它的家目录。 |
| `[remote] interface` | 无 | 直连线的网络接口，比如 `bridge0`。设置后通过这个接口上的链路本地地址连接第二台。 |
| `[remote] busy_file` | 无 | 第二台渲染期间额外保留的一个标记文件，供别的工具检查。 |
| `[remote] before` | 无 | 每次开渲前在第二台上执行的一条 shell 命令，比如用来释放内存。 |
| `[remote] start_after` | 30 | 本机先自己渲多少秒，再决定要不要启动第二台。 |
| `[remote] min_job` | 120 | 本机自己还需要超过这么多秒，才启动第二台。 |
| `[remote] sync` | 10 | 两边交换帧和游标的间隔，单位秒。 |
| `[remote] stall` | 1800 | 第二台连得上但这么久没出新帧，就放弃它。 |
| `[yield] processes` | `["Blender"]` | 本机给哪些程序让位。 |
| `[yield] cpu` | 50 | 占用单核的百分之多少算在渲染。 |
| `[yield] quiet` | 60 | 对方安静多少秒后，本机的 worker 回来。 |
| `[yield] abort_frame` | 20 | 手上这帧还要超过这么多秒就放弃，否则先渲完。 |

环境变量 `TANDEM_BLENDER`、`TANDEM_REMOTE`、`TANDEM_REMOTE_BLENDER` 会覆盖配置文件里的值。

「在渲染」是按 CPU 时间判断的：两次采样之间，一个程序用掉了至少 `cpu`% 的单核时间，就算在渲染。实测一次 Cycles 渲染是 500 到 1200，开着没人动的 Blender 窗口大约是 2。

## 作业目录

```
<job-root>/<name>-<id>/
  scene.blend          自包含的快照
  worker.py            两台机器实际运行的 worker 脚本
  job.json             源文件、画质、场景信息、打包不了的文件
  frames/f_NNNN.png    16 位 PNG（场景开了透明胶片时是 RGBA）
  local.cur remote.cur stop local.hold      状态文件
  local.log remote.log
```

`<id>` 是 `.blend` 的内容加上 `--percent`、`--samples` 算出的哈希。所以：

- 场景没变，重跑同一条命令就是续渲，只渲缺的帧。
- 场景或画质变了，会进一个新的作业目录。

每帧先写到临时文件名，渲完再改名，所以存在的帧文件一定是完整的。

第二台上，作业在 `~/tandem-jobs/<name>-<id>/`。

工具从不删除作业目录，两台机器上都不删。每个目录里有一份场景副本和一整套帧，会越攒越多；`tandem jobs` 能看到都有哪些，清理由你自己决定。

### 打包不了的文件

不是所有外部文件 Blender 都能打包进 `.blend`：Alembic 和 USD 缓存打包不了，图片序列、视频、体积能不能打包取决于 Blender 版本。打包之后只要还有文件留在外面，这个作业就只在本机渲，并且会说明是哪个文件导致的。

Blender 自己都找不到的文件（路径失效）是另一种情况。两台机器上同样缺这个文件，所以照常两台一起渲，同时打印警告指出是哪个文件。

## 在 Python 里调用

```python
from blender_tandem import render

result = render("scene.blend", frames="1-240", percent=50, samples=64)
print(result.job)       # 作业目录
print(result.missing)   # 没渲完的帧，全部渲完时是 []
```

`render()` 的选项和命令行一致（`job_root`、`name`、`mode="both" | "local" | "remote"`、`rush`），它不做编码。`result.scene` 是场景的帧范围、帧率、引擎和分辨率，`result.seconds` 是每台机器每帧的耗时。

## 实际效果

2026 年 11 月实测。机器是 M4 Max MacBook Pro（48 GB）和 M3 MacBook Air（16 GB），雷雳线直连，Blender 5.2.3，Cycles 走 Metal，场景 490 万面，分辨率 3840×1608：

| 一帧 | M4 Max | M3 Air |
|---|---|---|
| 全分辨率，128 采样 | 137 秒 | 422 秒 |
| 半分辨率，64 采样 | 15 秒 | 42 秒 |

- 两台的速度比大约是 3 比 1，所以两台一起渲，比快的那台单独渲早完成 25% 左右。更大的好处是：快的那台随时可以空出来给你用，作业照样在往前走。
- 同一帧在两台机器上渲出来，SSIM 是 0.998 到 0.999。两段的接缝处看不出跳变。
- 装好 Blender 后第一次渲染要编译 Metal 内核，M3 Air 上大约 160 秒，只发生一次。之后第二台从启动到出第一帧是 15 到 35 秒。

## 限制

- 只支持两台 Mac：一台本机，一台远程。
- 只支持 macOS。
- 输出固定是 16 位 PNG。
- 让位只看 CPU 占用。别的程序占了多少 GPU，不提权读不到。
- 两台 Mac 的 Blender 版本必须一致。

## 开发

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

测试只用标准库，不需要 Blender：调度器是用假 worker 驱动的，假 worker 遵守和真 worker 相同的文件协议。`tests/make_scene.py` 会生成一个小的动画场景，用来手动试用，用法见文件开头。

## 许可证

MIT，见 [LICENSE](LICENSE)。
