"""A job directory: the frozen scene, the frames, the state files both machines read, the logs.

    <root>/<name>-<id>/
        scene.blend      self-contained snapshot of the source .blend
        worker.py        copy of the worker both machines run
        job.json         source, quality, scene facts, files that could not be packed
        frames/f_NNNN.png
        local.cur remote.cur stop local.hold      state (see worker.py)
        local.log remote.log
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py")
LOCK = "~/.cache/blender-tandem/lock"
STATE = ("local.cur", "remote.cur", "stop", "local.hold")


def log(msg):
    print(f"[tandem] {msg}", flush=True)


def sha(path, extra=""):
    h = hashlib.sha1(extra.encode())
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()[:8]


def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def read(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def frame_path(job, f):
    return os.path.join(job, "frames", f"f_{f:04d}.png")


def missing(job, frames):
    return [f for f in frames if not os.path.exists(frame_path(job, f))]


def parse_frames(spec, scene):
    """'A-B' or 'N' -> list of frames; None -> the scene's own range."""
    if spec is None:
        return list(range(scene["frame_start"], scene["frame_end"] + 1, scene.get("frame_step", 1)))
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", spec.strip())
    if not m or (m.group(2) and int(m.group(2)) < int(m.group(1))):
        raise SystemExit(f"--frames {spec}: expected A-B or a single frame number")
    return list(range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1))


def timings(job, role, offset=0):
    """Seconds per frame from a worker's log, reading from byte `offset` (logs are appended across runs)."""
    try:
        with open(os.path.join(job, f"{role}.log"), errors="replace") as fh:
            fh.seek(offset)
            return [float(m.group(1)) for l in fh if (m := re.match(rf"\[tandem\] {role} frame \d+ ([\d.]+)s", l))]
    except OSError:
        return []


def log_size(job, role):
    p = os.path.join(job, f"{role}.log")
    return os.path.getsize(p) if os.path.exists(p) else 0


def lock():
    """One tandem job at a time on this Mac (the GPU is what they would fight over). Returns the open file."""
    path = os.path.expanduser(LOCK)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        log(f"waiting for the running job to finish: {fh.read().strip()}")
        fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def lock_note(fh, text):
    fh.seek(0)
    fh.truncate()
    fh.write(text)
    fh.flush()


def blender_version(blender):
    try:
        out = subprocess.run([blender, "--version"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.splitlines()[0].strip() if out.strip() else ""


class LocalWorker:
    """The worker on this Mac: one Blender process per start(), in its own process group."""

    def __init__(self, blender, job, percent, samples):
        self.blender, self.job, self.percent, self.samples = blender, job, percent, samples
        self.proc = None

    def start(self, frames):
        cmd = ["caffeinate", "-i", self.blender, "-b", os.path.join(self.job, "scene.blend"),
               "--python-exit-code", "1", "-P", os.path.join(self.job, "worker.py"), "--", "render", self.job,
               "local", str(self.percent or "-"), str(self.samples or "-"), ",".join(str(f) for f in frames)]
        self.proc = subprocess.Popen(cmd, stdout=open(os.path.join(self.job, "local.log"), "a"),
                                     stderr=subprocess.STDOUT, start_new_session=True)
        return self.proc.pid   # also its process group

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_code(self):
        return None if self.proc is None else self.proc.poll()

    def kill(self):
        if self.running():
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()


def snapshot(blender, src, root, name, percent, samples):
    """Freeze `src` into <root>/<name>-<id>/scene.blend and return (job_dir, meta).

    <id> hashes the .blend and the quality, so the same command resumes the same job and a changed scene
    or quality gets a new directory. Retries when the .blend is rewritten while it is being frozen.
    """
    key = f"tandem1:{percent}:{samples}:"   # bump the tag when the snapshot format changes
    for _ in range(3):
        before = sha(src, key)
        job = os.path.join(root, f"{name}-{before}")
        scene, meta_path = os.path.join(job, "scene.blend"), os.path.join(job, "job.json")
        if os.path.exists(meta_path) and '"blender-tandem"' not in read(meta_path):
            raise SystemExit(f"{job} exists but was not made by blender-tandem; use another --name or --job-root")
        frozen = os.path.exists(scene) and os.path.exists(meta_path)
        if frozen:
            try:    # a snapshot that left files outside is made again: the path may have been fixed since
                old = json.loads(read(meta_path))
                frozen = not (old.get("external") or old.get("missing"))
            except ValueError:
                frozen = False
        if not frozen:
            os.makedirs(os.path.join(job, "frames"), exist_ok=True)
            tmp = os.path.join(job, ".scene_tmp.blend")
            log(f"freezing {src} -> {scene}")
            r = subprocess.run([blender, "-b", src, "--python-exit-code", "1", "-P", WORKER, "--", "snapshot", tmp],
                               capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(tmp):
                raise SystemExit(f"snapshot failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
            if sha(src, key) != before:
                log("the .blend changed while it was being frozen, retrying")
                continue
            lines = r.stdout.splitlines()
            facts = next((json.loads(l[15:]) for l in lines if l.startswith("[tandem] SCENE ")), None)
            if facts is None:
                raise SystemExit(f"snapshot printed no scene facts:\n{r.stdout[-2000:]}")
            os.replace(tmp, scene)
            write(meta_path, json.dumps({
                "tool": "blender-tandem", "source": os.path.abspath(src), "id": before, "percent": percent,
                "samples": samples, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "scene": facts,
                "external": [l[18:] for l in lines if l.startswith("[tandem] EXTERNAL ")],
                "missing": [l[17:] for l in lines if l.startswith("[tandem] MISSING ")]}, ensure_ascii=False, indent=1))
        shutil.copyfile(WORKER, os.path.join(job, "worker.py"))   # both machines run this tool version's worker
        with open(meta_path) as fh:
            return job, json.load(fh)
    raise SystemExit("the .blend keeps changing; wait for whatever is writing it")
