English | [简体中文](README.zh-CN.md)

# blender-tandem

Render a Blender animation on two Macs at once. Your main Mac renders from the first frame up, a second Mac renders from the last frame down, and they stop where they meet.

It is for one person with a main Mac and a spare one, for example a laptop on a Thunderbolt cable. There is no server and no shared disk, and nothing to install on the second Mac except Blender. While you render something else on the main Mac (a preview, another shot, a viewport), it steps aside and lets the second Mac carry on.

It is not a render farm. It handles exactly two Macs, and does not run on Linux or Windows.

## How it works

```
frame    1                                                    240
local    1 2 3 4 ... ──────────▶          ◀────────── ... 239 240    remote
                               they meet here
```

**Splitting the frames.** Before each frame, a machine writes the frame number to its cursor file. The cursors are exchanged every 10 seconds, and a machine stops when it reaches the other's cursor. You never tell the tool how fast each Mac is: the faster one simply gets further before they meet. At most one frame, the one where they meet, is rendered twice.

**Giving way.** The main Mac watches for other programs rendering on it (Blender by default). When one starts, the local worker stops: at once if its current frame still needs more than 20 seconds, otherwise after that frame. It exits, so the GPU and the memory are free. Once the other program has been quiet for 60 seconds, a new worker starts on the frames that are still missing. The second Mac is not affected. `--rush` turns this off.

**Starting the second Mac only when it helps.** The main Mac renders alone for 30 seconds. If, at the speed it showed, the rest would take it more than 2 minutes, the second Mac is started. If the main Mac is giving way when the job begins, the second Mac starts immediately.

**When the second Mac disappears.** If it is unreachable for about a minute, or produces no frame for 30 minutes, it is dropped and the main Mac renders what it left unfinished.

**A frozen, self-contained snapshot.** When a job starts, the `.blend` is copied into the job directory with its images, fonts, sounds and linked libraries packed in. Both Macs render that copy, so only the job directory has to travel, and editing the original afterwards does not disturb a running job.

## Requirements

- Two Macs. The tool uses `ps`, `caffeinate` and Metal.
- The same Blender version on both. This is checked before every job.
- Python 3.11 or later on the main Mac. No third-party packages.
- Key-based ssh from the main Mac to the second one.
- Optional: `ffmpeg` and `ffprobe` on the main Mac, for `--encode`.

## Install

```bash
uv tool install git+https://github.com/Shaojie66/blender-tandem
```

or

```bash
pipx install git+https://github.com/Shaojie66/blender-tandem
```

or run it from a clone without installing:

```bash
PYTHONPATH=src python3 -m blender_tandem status
```

## Quick start

**1. Prepare the second Mac.** Turn on Remote Login in System Settings, and install the same Blender version (copying `Blender.app` over is the simplest way). From the main Mac, this must work without a password prompt:

```bash
ssh second-mac true
```

**2. Tell the tool about it.** Create `~/.config/blender-tandem/config.toml`:

```toml
[remote]
host = "second-mac"
```

If the two Macs are joined by a Thunderbolt cable, add `interface = "bridge0"` to connect over the cable instead of the network.

**3. Check both machines.**

```bash
tandem status
```

**4. Render.**

```bash
tandem render scene.blend
```

Frames appear in `tandem-jobs/scene-<id>/frames/` next to the `.blend`. Without a `[remote]` section the same command renders on this Mac alone, and still gives way.

## Commands

### `tandem render`

```bash
tandem render scene.blend [--frames A-B] [--percent N] [--samples N] [--job-root DIR] [--name NAME]
                          [--local-only | --remote-only] [--rush] [--encode OUT.mp4 [--fps N] [--crf N]]
```

| Option | Meaning |
|---|---|
| `--frames A-B` | Frames to render. Default: the scene's frame range. |
| `--percent N` | Resolution percentage. Default: the scene's. |
| `--samples N` | Cycles samples. Default: the scene's. |
| `--job-root DIR` | Where job directories go. Default: `tandem-jobs/` next to the `.blend`. |
| `--name NAME` | Job name. Default: the `.blend`'s name. |
| `--local-only` | This Mac only. |
| `--remote-only` | The second Mac only. |
| `--rush` | Never give way to other programs on this Mac. |
| `--encode OUT.mp4` | When all frames are rendered, encode them to H.264 with ffmpeg. |
| `--fps N` | Frame rate for `--encode`. Default: the scene's. |
| `--crf N` | x264 quality for `--encode`. Default: 18. |

With Cycles, the worker enables every Metal device the Mac has (GPU and CPU). Other render engines use the scene's own settings.

Ctrl-C stops both machines at once. Finished frames are kept, and the same command resumes.

Only one job runs on a Mac at a time; a second `tandem render` waits for the first.

Exit code: 0 when every frame is rendered, 1 when some are missing, 130 when interrupted.

### `tandem status`

```bash
tandem status
```

Shows the Blender version on both Macs, how the second one is reached, and whether it is idle.

### `tandem jobs`

```bash
tandem jobs [DIR]
```

Lists the job directories under `DIR` (default: the current directory) with their frame count and size.

### Another config file

```bash
tandem --config other.toml render scene.blend
```

## Configuration

`~/.config/blender-tandem/config.toml`, or the file named by `TANDEM_CONFIG` or `--config`. Every setting is optional. [`examples/config.toml`](examples/config.toml) lists them all.

| Setting | Default | Meaning |
|---|---|---|
| `[local] blender` | `/Applications/Blender.app/Contents/MacOS/Blender` | Blender on this Mac. |
| `[remote] host` | none | ssh host or alias of the second Mac. Without it, this Mac renders alone. |
| `[remote] blender` | same path as local | Blender on the second Mac. |
| `[remote] dir` | `tandem-jobs` | Where job directories go on the second Mac, relative to its home. |
| `[remote] interface` | none | Network interface of a direct cable, e.g. `bridge0`. The second Mac is then reached at its link-local address on that interface. |
| `[remote] busy_file` | none | An extra marker file kept on the second Mac while it renders, for other tools to check. |
| `[remote] before` | none | A shell command run on the second Mac before each render, e.g. to free memory. |
| `[remote] start_after` | 30 | Seconds this Mac renders alone before deciding whether to start the second one. |
| `[remote] min_job` | 120 | Start the second Mac only if this Mac alone would still need longer than this. |
| `[remote] sync` | 10 | Seconds between exchanges of frames and cursors. |
| `[remote] stall` | 1800 | Reachable but no new frame for this long: the second Mac is dropped. |
| `[yield] processes` | `["Blender"]` | Program names this Mac gives way to. |
| `[yield] cpu` | 50 | Percent of one core that counts as rendering. |
| `[yield] quiet` | 60 | Seconds of quiet before this Mac's worker comes back. |
| `[yield] abort_frame` | 20 | Drop the frame in progress if it still needs longer than this; otherwise finish it first. |

Environment variables `TANDEM_BLENDER`, `TANDEM_REMOTE` and `TANDEM_REMOTE_BLENDER` override the file.

"Rendering" is judged by CPU time: a program counts when it used at least `cpu` percent of one core between two readings. A Cycles render measured 500 to 1200; an open Blender window that nobody touches, about 2.

## Job directories

```
<job-root>/<name>-<id>/
  scene.blend          the self-contained snapshot
  worker.py            the worker script both Macs run
  job.json             source file, quality, scene facts, files that could not be packed
  frames/f_NNNN.png    16-bit PNG (RGBA when the scene's film is transparent)
  local.cur remote.cur stop local.hold      state files
  local.log remote.log
```

`<id>` is a hash of the `.blend` and of `--percent` and `--samples`. So:

- The same command on an unchanged scene resumes the same job and renders only the missing frames.
- A changed scene or quality gets a new job directory.

A frame is written under a temporary name and renamed when it is complete, so a frame file that exists is whole.

On the second Mac the job lives in `~/tandem-jobs/<name>-<id>/`.

The tool never deletes a job directory, on either Mac. Each holds a copy of the scene and a full set of frames, so they add up; `tandem jobs` shows what is there, and removing them is up to you.

### Files that cannot be packed

Blender cannot pack every kind of external file into a `.blend`: Alembic and USD caches stay outside, and depending on the Blender version so do image sequences, movies or volumes. If anything is left outside after packing, the job renders on this Mac only and says which file was the reason.

A file that Blender itself cannot find (a broken path) is a different case. It is missing on both Macs alike, so the job still uses both, and a warning names the file.

In both cases the snapshot is made again on the next run, so once the path is fixed the same command picks it up.

## Use from Python

```python
from blender_tandem import render

result = render("scene.blend", frames="1-240", percent=50, samples=64)
print(result.job)       # the job directory
print(result.missing)   # frames that were not rendered, [] when complete
```

`render()` takes the same choices as the command line (`job_root`, `name`, `mode="both" | "local" | "remote"`, `rush`) and never encodes. `result.scene` holds the scene's frame range, fps, engine and resolution; `result.seconds` the seconds per frame on each machine.

## What to expect

Measured in November 2026 with an M4 Max MacBook Pro (48 GB) and an M3 MacBook Air (16 GB) on a Thunderbolt cable, Blender 5.2.3, Cycles on Metal, a scene of 4.9 million polygons at 3840×1608:

| One frame | M4 Max | M3 Air |
|---|---|---|
| Full resolution, 128 samples | 137 s | 422 s |
| Half resolution, 64 samples | 15 s | 42 s |

- The speed ratio is about 3 to 1, so both together finish about 25% sooner than the fast Mac alone. The larger gain is that the fast Mac is free whenever you need it, and the job still moves.
- The same frame rendered on the two Macs had an SSIM of 0.998 to 0.999. No jump is visible where the two halves meet.
- The first render after installing Blender compiles the Metal kernels, about 160 s on the M3 Air. That happens once. After that the second Mac delivered its first frame 15 to 35 s after starting.

## Limits

- Two Macs: one local, one remote.
- macOS only.
- Frames are always 16-bit PNG.
- Giving way looks at CPU use. GPU use by other programs cannot be read without elevated rights.
- Both Macs need the same Blender version.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

The tests use only the standard library and do not need Blender: the scheduler is driven with fake workers that follow the same file protocol as the real one. `tests/make_scene.py` builds a small animated scene for trying the tool by hand; see the top of that file.

## License

MIT. See [LICENSE](LICENSE).
