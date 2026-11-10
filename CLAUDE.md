# blender-tandem

两台 Mac 一起渲一段 Blender 序列的命令行工具：主力机从第一帧往后渲，另一台（ssh 能连上的 Mac）从最后一帧往前渲，碰头结束。
主力机上有别的程序在渲染时自动让位。没有服务端，没有共享盘，零依赖。

范围只到两台 Mac。不做：多于两台的节点、Linux / Windows、图形界面。有人提需求先问用户，不顺手加。

## 目录约定
- `src/blender_tandem/`  代码。一个模块一件事：
  - `cli.py` 命令行和 `render()` 入口　`config.py` 配置　`job.py` 快照、作业目录、缺帧、整机锁
  - `scheduler.py` 调度状态机　`others.py` 本机其他进程占用检测　`remote.py` ssh / rsync　`encode.py` ffmpeg
  - `worker.py` 在 Blender 里跑的那一半（`snapshot` / `render`）
- `tests/`  标准库 `unittest`。`tests/make_scene.py` 生成测试用的小场景，不提交 `.blend`
- `examples/`  配置样例
- 渲染产物、作业目录、`.blend` 一律不进 git（见 `.gitignore`）

## 代码约定
- 零第三方依赖，Python ≥ 3.11（`tomllib`）
- `worker.py` 只能用 Blender 自带的库，不能 import 包里其他模块：它会被单独拷到远程机上运行
- 两个角色固定叫 `local` 和 `remote`。作业目录里的状态文件是两台机器之间唯一的协议，改名或改含义要同时改 `worker.py`、`scheduler.py` 和 README：
  - `local.cur` / `remote.cur`  各自认领到的帧号　`stop`  非空则两边渲完手上这帧就停　`local.hold`  非空则本机让位
  - `frames/f_NNNN.png`  渲完才改名到位，存在即完整；`.tmp_*` 是没渲完的
- 调度逻辑写在 `Scheduler` 里，靠注入的 worker / 时钟来测；不要把逻辑写回脚本顶层
- 仓库里不出现任何一台真实机器的主机名、用户名、家目录路径、IP
- 注释和文档字符串用英文，写清「为什么」

## 验证
```bash
PYTHONPATH=src python3 -m unittest discover -s tests            # 每次改完都跑
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup -P tests/make_scene.py -- /tmp/tandem-cube.blend
PYTHONPATH=src python3 -m blender_tandem render /tmp/tandem-cube.blend --local-only   # 单机
PYTHONPATH=src python3 -m blender_tandem status                 # 两台机器的状态
```
动了调度、让位或远程同步，要用真场景两台机器实测一遍（两台都出帧、接缝连续、Ctrl-C 两边都停、远程掉线后本机补帧）。

## 文档
- `README.md`（英文）和 `README.zh-CN.md`（中文）按用户规则交 Codex 子智能体写，写完逐条核对命令能跑、数字和实测一致
- 改了命令行或配置项，两份 README 和 `examples/config.toml` 同步改

## 红线（用户全局规则）
删除文件或 git 历史、`git push`、rebase、改仓库可见性、打 tag 发版、全局安装，先问用户。
仓库目前是私有的，改公开由用户自己操作。
