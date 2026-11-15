"""Record one real two-Mac run for the README animation:

    python3 demo/record.py WORK_DIR [frames] [seconds_per_frame]

Renders the test scene (tests/make_scene.py) on both Macs through the normal command line, starts another
Blender render on this Mac part-way so that the local worker gives way, and writes demo/run.json: every
line the tool printed with the second it appeared, and for every frame who rendered it and when.
Nothing in run.json is made up; make_svg.py only draws it. The one change to the printed lines is that
what identifies the two machines is taken out: directories, the ssh host name, the address.

It waits until neither Mac is rendering, because the run is only worth showing when the speeds are the
machines' own. WORK_DIR gets the scene and the job directories and can be deleted afterwards.
"""
import datetime
import fcntl
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
from blender_tandem import config, job                 # noqa: E402
from blender_tandem.cli import render                  # noqa: E402
from blender_tandem.others import Others               # noqa: E402
from blender_tandem.remote import Remote               # noqa: E402

PERCENT = 400            # the test scene is 320x180; this makes it 1280x720
OTHER_SECONDS = 40       # how long the other render on this Mac lasts
OTHER_AFTER = 2          # start it once the remote has finished this many frames


def say(msg):
    print(f"[record] {msg}", flush=True)


def idle(cfg, remote):
    """Nothing is rendering on either Mac and no tandem job holds the lock."""
    with open(os.path.expanduser(job.LOCK), "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "a tandem job is running here"
    others = Others(cfg.yield_processes, cfg.yield_cpu, 5)
    others.busy()
    time.sleep(3)
    if who := others.busy():
        return f"{who} on this Mac"
    r = remote.sh(" || ".join(f"test -e {b}" for b in remote.busy))
    if r.returncode == 255:
        return "the second Mac is unreachable"
    return "the second Mac is busy" if r.returncode == 0 else ""


def calibrate(cfg, scene, work, target):
    """Samples at which this Mac needs about `target` seconds a frame: two short local runs, a line through them."""
    seen = {}
    for samples in (32, 256):
        res = render(scene, "1-4", PERCENT, samples, os.path.join(work, "calib"), f"calib{samples}", "local", True, cfg)
        seen[samples] = statistics.median(res.seconds["local"][1:])    # the first frame also loads the kernels
        say(f"{samples} samples: {seen[samples]:.2f}s a frame")
    per_sample = (seen[256] - seen[32]) / (256 - 32)
    if per_sample <= 0:
        return 256
    return max(8, min(4096, round((target - (seen[32] - per_sample * 32)) / per_sample)))


def clock_offset(remote):
    """Seconds the second Mac's clock is ahead of this one; its frames keep its own modification times."""
    t = time.time()
    r = remote.sh("perl -MTime::HiRes=time -e 'printf \"%.3f\", time'")
    return float(r.stdout) - (t + time.time()) / 2 if r.returncode == 0 and r.stdout else 0.0


def main():
    work = os.path.abspath(sys.argv[1])
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 96
    target = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
    os.makedirs(work, exist_ok=True)
    cfg = config.load()
    scene = os.path.join(work, "scene.blend")
    subprocess.run([cfg.blender, "-b", "--factory-startup", "-P", os.path.join(ROOT, "tests", "make_scene.py"),
                    "--", scene, str(count)], check=True, stdout=subprocess.DEVNULL)
    remote = Remote(cfg, os.path.join(work, "x"))

    waited = time.time()
    while why := idle(cfg, remote):
        say(f"waiting ({why}), {int((time.time() - waited) / 60)} min so far")
        time.sleep(60)
    samples = calibrate(cfg, scene, work, target)
    say(f"recording {count} frames at {PERCENT}% {samples} samples")

    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
    t0 = time.time()
    say(f"started at {t0:.3f}")          # with this and the job directory the record can be rebuilt by hand
    run = subprocess.Popen([sys.executable, "-u", "-m", "blender_tandem", "render", scene, "--percent", str(PERCENT),
                            "--samples", str(samples), "--job-root", os.path.join(work, "jobs")],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines, other, jobdir = [], [], ""
    for line in run.stdout:
        t = time.time() - t0
        line = line.rstrip()
        print(f"{t:7.1f}  {line}", flush=True)
        lines.append([round(t, 1), line])
        if m := re.search(r"job (.+?): \d+/\d+ frames", line):
            jobdir = m.group(1)
        m = re.search(r"remote at (\d+)", line)
        if m and not other and int(m.group(1)) <= count - OTHER_AFTER:
            # Another Blender render on this Mac, as someone checking a preview would start one.
            heavy = "import bpy; s = bpy.context.scene; s.cycles.samples = 1024; s.frame_end = 100000"
            p = subprocess.Popen([cfg.blender, "-b", scene, "--python-expr", heavy,
                                  "-o", os.path.join(work, "other", "f_"), "-a"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            other.append(round(t, 1))

            def end(p=p):
                os.killpg(p.pid, signal.SIGTERM)
                other.append(round(time.time() - t0, 1))
            threading.Timer(OTHER_SECONDS, end).start()
    rc = run.wait()
    if rc or not jobdir or len(other) != 2:
        sys.exit(f"[record] not usable: exit {rc}, job {jobdir!r}, other render {other}")

    offset = clock_offset(remote)
    cells = {}
    for role in ("local", "remote"):
        with open(os.path.join(jobdir, f"{role}.log"), errors="replace") as fh:
            for m in re.finditer(rf"^\[tandem\] {role} frame (\d+) ([\d.]+)s", fh.read(), re.M):
                f, took = int(m.group(1)), float(m.group(2))
                if f in cells:
                    continue     # the frame where they met was rendered twice; the local copy is the one kept
                done = os.path.getmtime(job.frame_path(jobdir, f)) - t0 - (offset if role == "remote" else 0)
                cells[f] = [f, role, round(done, 1), round(took, 1)]
    if sorted(cells) != list(range(1, count + 1)):
        sys.exit(f"[record] frames missing from the logs: {sorted(set(range(1, count + 1)) - set(cells))}")

    def chip(out):
        return out.strip().replace("Apple ", "")

    def public(s):
        """A printed line without what identifies these two machines: the work directory, the home directory,
        the ssh host name (shown as the one the README's example uses) and the address the job travelled over."""
        s = s.replace(work + os.sep, "").replace(home, "~")
        s = re.sub(r"\b" + re.escape(str(cfg.remote_host)) + r"\b", "second-mac", s)
        return re.sub(r"\b(\d+\.\d+)\.\d+\.\d+\b", r"\1.x.x", s)
    home = os.path.expanduser("~")
    out = {
        "recorded": datetime.date.today().isoformat(),
        "frames": count, "percent": PERCENT, "samples": samples, "resolution": [320 * PERCENT // 100, 180 * PERCENT // 100],
        "seconds": lines[-1][0], "blender": job.blender_version(cfg.blender),
        "local": chip(subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout),
        "remote": chip(remote.sh("sysctl -n machdep.cpu.brand_string").stdout),
        "other": other,
        "lines": [[t, public(s)] for t, s in lines],
        "cells": [cells[f] for f in sorted(cells)],
    }
    text = json.dumps(out, indent=1)
    private = "|".join([r"/Users/", r"\d+\.\d+\.\d+\.\d+", r"\b" + re.escape(str(cfg.remote_host)) + r"\b",
                        re.escape(os.path.basename(home))])
    if m := re.search(private, text.replace(out["blender"], "")):
        sys.exit(f"[record] a path, an address, the host name or the user name is still in the record "
                 f"(at character {m.start()}); not written")
    if not any("gives way" in s for _, s in lines) or not any("resumes" in s for _, s in lines):
        say("warning: the local worker did not give way and come back in this run")
    with open(os.path.join(HERE, "run.json"), "w") as fh:
        fh.write(text + "\n")
    say(f"wrote demo/run.json: {count} frames in {out['seconds']:.0f}s, local "
        f"{sum(c[1] == 'local' for c in out['cells'])}, remote {sum(c[1] == 'remote' for c in out['cells'])}")


if __name__ == "__main__":
    main()
