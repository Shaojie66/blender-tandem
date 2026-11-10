"""Command line and the render() entry point.

  tandem render scene.blend [--frames A-B] [--percent N] [--samples N] [--job-root DIR] [--name NAME]
                            [--local-only | --remote-only] [--rush] [--encode out.mp4 [--fps N] [--crf N]]
  tandem status             Blender on both Macs, whether the remote is reachable and free
  tandem jobs [DIR]         job directories under DIR: frames and size (nothing is ever deleted)
"""
import argparse
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field

from . import __version__, config, job
from .encode import encode
from .job import log
from .others import Others
from .remote import Remote
from .scheduler import Scheduler, fmt


@dataclass
class Result:
    job: str                 # the job directory; frames are in <job>/frames/f_NNNN.png
    frames: list             # the frames that were asked for
    missing: list            # those of them that are still not rendered
    scene: dict              # frame range, fps, engine, resolution of the source scene
    note: str = ""           # why frames are missing, when they are
    seconds: dict = field(default_factory=dict)   # role -> seconds per frame rendered in this run


def render(blend, frames=None, percent=None, samples=None, job_root=None, name=None, mode="both", rush=False,
           cfg=None):
    """Render `blend` on this Mac and the configured remote Mac. Returns a Result; never encodes.

    frames   'A-B' (None: the scene's own range)        percent / samples   None: the scene's own settings
    job_root where job directories go (None: tandem-jobs/ next to the .blend)
    mode     'both', 'local' or 'remote'                rush   True: the local Mac never gives way
    """
    cfg = cfg or config.load()
    src = os.path.abspath(blend)
    if not os.path.isfile(src):
        raise SystemExit(f"no such file: {src}")
    if not os.path.exists(cfg.blender):
        raise SystemExit(f"Blender not found at {cfg.blender} (set [local] blender or TANDEM_BLENDER)")
    others = None if rush or mode == "remote" else Others(cfg.yield_processes, cfg.yield_cpu, cfg.yield_quiet)
    if others:
        others.busy()            # first reading; the second one, in the scheduler, shows who is rendering
    primed = time.time()
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name or os.path.splitext(os.path.basename(src))[0])
    held = job.lock()
    job.lock_note(held, f"{name} pid {os.getpid()} since {time.strftime('%H:%M:%S')}")

    root = os.path.abspath(job_root or os.path.join(os.path.dirname(src), "tandem-jobs"))
    jobdir, meta = job.snapshot(cfg.blender, src, root, name, percent, samples)
    flist = job.parse_frames(frames, meta["scene"])
    for f in job.STATE:
        job.write(os.path.join(jobdir, f), "")      # cursors and flags of an earlier run mean nothing now

    remote, why = None, ""
    if mode != "local":
        unpacked = meta["external"] + meta["missing"]
        if not cfg.remote_host:
            why = "no remote machine configured"
        elif unpacked:
            why = f"the scene reads {len(unpacked)} file(s) that cannot be packed into the snapshot, e.g. {unpacked[0]}"
        else:
            remote = Remote(cfg, jobdir)
            why = remote.check(job.blender_version(cfg.blender))
        if why:
            remote = None
            if mode == "remote":
                raise SystemExit(f"[tandem] remote not usable: {why}")
            log(f"remote not usable ({why}); rendering everything here")
    if remote:
        remote.pull(cursor=False)                   # frames it finished in an earlier, interrupted run
    todo = job.missing(jobdir, flist)
    log(f"job {jobdir}: {len(flist) - len(todo)}/{len(flist)} frames already there, {len(todo)} to render"
        + (f" at {percent}%" if percent else "") + (f" {samples} samples" if samples else "")
        + {"both": ", local + remote" if remote else ", local only", "local": ", local only", "remote": ", remote only"}[mode])

    time.sleep(max(0.0, 1.5 - (time.time() - primed)))
    sched = Scheduler(jobdir, flist, cfg, job.LocalWorker(cfg.blender, jobdir, percent, samples), remote, others, mode,
                      percent, samples)

    def interrupted(*_):
        log("interrupted: stopping both machines (finished frames are kept; the same command resumes)")
        sched.interrupt()
        sys.exit(130)

    previous = {s: signal.signal(s, interrupted) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        left = sched.run() if todo else []
    finally:
        for s, h in previous.items():
            signal.signal(s, h)
    held.close()
    return Result(jobdir, flist, left, meta["scene"], sched.note if left else "",
                  {r: job.timings(jobdir, r, sched.offsets[r]) for r in ("local", "remote")})


def status(cfg):
    version = job.blender_version(cfg.blender)
    print(f"local   {version or 'no Blender'}   {cfg.blender}")
    if not cfg.remote_host:
        print("remote  not configured ([remote] host in ~/.config/blender-tandem/config.toml, or TANDEM_REMOTE)")
        return 0
    r = Remote(cfg, "status")
    how = f"{r.address} on {cfg.remote_interface}" if r.address else "by name"
    reachable, busy, rversion = r.facts()
    if not reachable:
        print(f"remote  {cfg.remote_host} ({how}): unreachable over ssh")
        return 1
    print(f"remote  {rversion or 'no Blender'}   {cfg.remote_host} ({how}): {'busy with: ' + busy if busy else 'idle'}")
    if rversion and version and rversion != version:
        print("        versions differ: the remote will not be used")
    return 0


def jobs(top):
    found = []
    for d, dirs, files in os.walk(top):
        if "job.json" in files and "scene.blend" in files:
            dirs[:] = []
            try:
                meta = json.load(open(os.path.join(d, "job.json")))
            except ValueError:
                continue
            if meta.get("tool") != "blender-tandem":
                continue
            size = sum(os.path.getsize(os.path.join(p, f)) for p, _, fs in os.walk(d) for f in fs)
            fdir = os.path.join(d, "frames")
            n = len([f for f in os.listdir(fdir) if f.startswith("f_")]) if os.path.isdir(fdir) else 0
            found.append((meta.get("created", ""), n, size, d))
    for created, n, size, d in sorted(found):
        print(f"{created}  {n:5d} frames  {size / 1e6:8.0f} MB  {d}")
    print(f"{len(found)} job directories, {sum(f[2] for f in found) / 1e9:.1f} GB. Remove the ones you no longer need yourself.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tandem", description="Render a Blender animation on two Macs at once.")
    ap.add_argument("--version", action="version", version=f"blender-tandem {__version__}")
    ap.add_argument("--config", help="config file (default ~/.config/blender-tandem/config.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render a .blend")
    r.add_argument("blend")
    r.add_argument("--frames", help="A-B (default: the scene's frame range)")
    r.add_argument("--percent", type=int, help="resolution percentage (default: the scene's)")
    r.add_argument("--samples", type=int, help="Cycles samples (default: the scene's)")
    r.add_argument("--job-root", help="where job directories go (default: tandem-jobs/ next to the .blend)")
    r.add_argument("--name", help="job name (default: the .blend's name)")
    which = r.add_mutually_exclusive_group()
    which.add_argument("--local-only", action="store_true", help="this Mac only")
    which.add_argument("--remote-only", action="store_true", help="the remote Mac only")
    r.add_argument("--rush", action="store_true", help="never give way to other programs rendering on this Mac")
    r.add_argument("--encode", metavar="OUT.mp4", help="encode the frames with ffmpeg when all are rendered")
    r.add_argument("--fps", type=float, help="frame rate for --encode (default: the scene's)")
    r.add_argument("--crf", type=int, default=18, help="x264 quality for --encode (default 18)")
    sub.add_parser("status", help="both machines at a glance")
    j = sub.add_parser("jobs", help="list job directories")
    j.add_argument("dir", nargs="?", default=".")
    a = ap.parse_args(argv)
    if a.cmd == "jobs":
        return jobs(a.dir)
    cfg = config.load(a.config)
    if a.cmd == "status":
        return status(cfg)

    t0 = time.time()
    res = render(a.blend, a.frames, a.percent, a.samples, a.job_root, a.name,
                 "local" if a.local_only else "remote" if a.remote_only else "both", a.rush, cfg)
    parts = [f"{role} {len(t)} frames avg {sum(t) / len(t):.1f}s" for role, t in res.seconds.items() if t]
    if res.missing:
        log(f"stopped with {len(res.missing)} of {len(res.frames)} frames missing ({res.missing[0]}..{res.missing[-1]}): "
            f"{res.note or 'see the logs in ' + res.job}. The same command resumes.")
        return 1
    log(f"done in {fmt(time.time() - t0)}: {len(res.frames)} frames in {os.path.join(res.job, 'frames')}"
        + ("   " + "   ".join(parts) if parts else ""))
    if a.encode:
        log(f"encoded {encode(res.job, res.frames, a.encode, a.fps or res.scene['fps'], a.crf)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
