"""The second Mac as a render node: ssh for commands, rsync for the job files and the frames.

Only the job directory travels: <remote dir>/<job name>/ gets scene.blend, worker.py and the state files,
and its frames/ come back. Nothing else is assumed about the remote except ssh access and Blender.
"""
import os
import re
import shlex
import signal
import subprocess
import time

from .job import log, timings, write


def link_local(interface):
    """The other Mac's link-local IPv4 on `interface` (a Thunderbolt bridge has no DHCP), or None."""
    out = subprocess.run(["arp", "-an", "-i", interface], capture_output=True, text=True).stdout
    for line in out.splitlines():
        m = re.search(r"\((169\.254\.\d+\.\d+)\) at [0-9a-f]", line)
        if m and "permanent" not in line:   # "permanent" entries are this Mac's own addresses
            return m.group(1)
    return None


def host_key_name(host):
    """The name ssh files `host`'s key under, so the key still verifies when we connect by address."""
    out = subprocess.run(["ssh", "-G", host], capture_output=True, text=True).stdout
    conf = dict(l.split(None, 1) for l in out.splitlines() if " " in l)
    return conf.get("hostkeyalias") or conf.get("hostname") or host


def ssh_base(cfg):
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    ip = link_local(cfg.remote_interface) if cfg.remote_interface else None
    if ip:
        cmd += ["-o", f"HostName={ip}", "-o", f"HostKeyAlias={host_key_name(cfg.remote_host)}"]
    return cmd, ip


def shell_path(p):
    """A path for a remote shell command: ~/x and relative paths are taken from the remote home."""
    if p.startswith("~/"):
        p = p[2:]
    return shlex.quote(p) if p.startswith("/") else '"$HOME"/' + shlex.quote(p)


class Remote:
    def __init__(self, cfg, job):
        self.cfg, self.job, self.host = cfg, job, cfg.remote_host
        self.name = os.path.basename(job)
        self.ssh, self.address = ssh_base(cfg)
        rdir = cfg.remote_dir[2:] if cfg.remote_dir.startswith("~/") else cfg.remote_dir
        self.rsync_job = f"{self.host}:{rdir}/{self.name}"      # rsync takes relative paths from the home
        self.sh_job = shell_path(f"{rdir}/{self.name}")
        self.busy = [shell_path(f"{rdir}/.busy")] + ([shell_path(cfg.remote_busy_file)] if cfg.remote_busy_file else [])
        self.proc = None
        self.fails = 0              # consecutive failed exchanges
        self.count, self.slowest, self.last_progress = 0, 0.0, time.time()
        self.offset = 0             # where this run starts in remote.log

    def sh(self, script, timeout=30):
        try:
            return subprocess.run(self.ssh + [self.host, "sh -c " + shlex.quote(script)],
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(script, 255, "", "timeout")

    def rsync(self, args, timeout=3600):
        try:
            return subprocess.run(["rsync", "-a", "-e", " ".join(self.ssh)] + args,
                                  capture_output=True, text=True, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return 255

    def facts(self):
        """(reachable, busy text, Blender version line) in one round trip."""
        r = self.sh(f"cat {' '.join(self.busy)} 2>/dev/null; echo ---; "
                    f"{shlex.quote(self.cfg.remote_blender)} --version 2>/dev/null | head -1", timeout=60)
        if r.returncode == 255 or "---" not in r.stdout:
            return False, "", ""
        busy, _, version = r.stdout.partition("---")
        busy = " ".join(busy.split())
        return True, busy if len(busy) <= 100 else busy[:100] + "...", version.strip()

    def check(self, local_version):
        """'' when the remote can render this job, else why not."""
        reachable, busy, version = self.facts()
        if not reachable:
            return f"{self.host} unreachable over ssh"
        if not version:
            return f"no Blender at {self.cfg.remote_blender} on {self.host}"
        if version != local_version:
            return f"Blender versions differ: here '{local_version}', {self.host} '{version}'"
        return f"busy with: {busy}" if busy else ""

    def pull(self, cursor=True):
        """The remote's finished frames (and its cursor) -> here. Frames already here are never replaced."""
        rc = self.rsync(["--ignore-existing", "--exclude", ".tmp_*", f"{self.rsync_job}/frames/",
                         os.path.join(self.job, "frames") + "/"])
        if cursor:
            self.rsync([f"{self.rsync_job}/remote.cur", self.job + "/"])
        return rc

    def push_state(self):
        """The local cursor -> remote, so its worker knows where to stop."""
        # --checksum: two cursors of the same length written within one second look alike to rsync otherwise
        return self.rsync(["--checksum", os.path.join(self.job, "local.cur"), f"{self.rsync_job}/"])

    def start(self, percent, samples, frames):
        """Send the job over and start the remote worker on `frames`."""
        self.sh(f"mkdir -p {self.sh_job}/frames")
        t = time.time()
        files = ["scene.blend", "worker.py", "local.cur", "remote.cur", "stop"]   # remote.cur and stop are empty
        if self.rsync([os.path.join(self.job, f) for f in files] + [f"{self.rsync_job}/"]) != 0:
            return False
        log(f"job sent to {self.host} in {time.time() - t:.0f}s" + (f" (via {self.address})" if self.address else ""))
        note = shlex.quote(f"blender-tandem {self.name}")
        script = "".join(f"echo {note} > {b}; " for b in self.busy)
        script += f"trap 'rm -f {' '.join(self.busy)}' EXIT; "
        if self.cfg.remote_before:
            script += f"( {self.cfg.remote_before} ) >/dev/null 2>&1; "
        script += (f"cd {self.sh_job} && caffeinate -i {shlex.quote(self.cfg.remote_blender)} -b scene.blend "
                   f"--python-exit-code 1 -P worker.py -- render \"$PWD\" remote {percent or '-'} {samples or '-'} "
                   + ",".join(str(f) for f in frames))
        self.offset = os.path.getsize(self._log()) if os.path.exists(self._log()) else 0
        self.count, self.slowest, self.last_progress, self.fails = 0, 0.0, time.time(), 0
        self.proc = subprocess.Popen(self.ssh + [self.host, "sh -c " + shlex.quote(script)],
                                     stdout=open(self._log(), "a"), stderr=subprocess.STDOUT, start_new_session=True)
        return True

    def _log(self):
        return os.path.join(self.job, "remote.log")

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def exchange(self):
        """One exchange of cursors and frames; keeps track of whether the remote is still producing."""
        self.push_state()
        self.fails = 0 if self.pull() == 0 else self.fails + 1
        n = len(timings(self.job, "remote", self.offset))
        if n != self.count:
            now = time.time()
            if self.count:
                self.slowest = max(self.slowest, now - self.last_progress)
            self.count, self.last_progress = n, now

    def dead(self):
        if self.fails >= 6:
            return "unreachable for a minute"
        if time.time() - self.last_progress > max(self.cfg.remote_stall, 4 * self.slowest):
            return "no new frame for too long"
        return ""

    def stop(self, kill=True):
        """End the remote worker: after its current frame, or at once with kill=True (that frame is lost)."""
        flag = os.path.join(self.job, ".remote_stop")   # the local `stop` file would stop the local worker too
        write(flag, "1")
        self.rsync([flag, f"{self.rsync_job}/stop"], timeout=30)
        if kill:
            # [w] keeps the pattern from matching the shell that runs this very command. The busy markers
            # are removed here as well: a killed shell does not always get to run its EXIT trap.
            pat = shlex.quote(f"[w]orker.py -- render [^ ]*/{self.name} remote")
            clean = "; ".join(f"grep -qF {shlex.quote(self.name)} {b} 2>/dev/null && rm -f {b}" for b in self.busy)
            self.sh(f"pkill -f {pat}; sleep 2; {clean}")
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=20 if kill else None)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGTERM)
