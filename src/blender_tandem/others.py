"""Is something else rendering on this Mac? Decided from the CPU time other processes used between calls."""
import os
import subprocess
import time


def cpu_seconds(t):
    """ps 'time' column, [D-][H:]MM:SS.ss -> seconds."""
    days, _, rest = t.rpartition("-")
    s = 0.0
    for part in rest.split(":"):
        s = s * 60 + float(part)
    return s + 86400 * int(days or 0)


def ps_snapshot():
    return subprocess.run(["ps", "-Ao", "pid=,pgid=,time=,comm="], capture_output=True, text=True).stdout


class Others:
    """Processes named in `names` that are not this job's own workers.

    busy() returns '' or a description of the busiest one. A process counts as rendering when it used at
    least `cpu` percent of one core since the previous call (measured 2026-11: a Cycles render uses
    500-1200, an open but untouched Blender window about 2). The answer stays set until `quiet` seconds
    after the last busy reading, so the gaps between someone's preview renders do not count as idle.
    """

    def __init__(self, names=("Blender",), cpu=50, quiet=60, ps=ps_snapshot, clock=time.time):
        self.names = {n.lower() for n in names}
        self.cpu, self.quiet, self.ps, self.clock = cpu, quiet, ps, clock
        self.prev, self.t, self.last_busy, self.who = {}, None, None, ""
        self.mine = set()   # process groups of this job's workers

    def busy(self):
        now = self.clock()
        if self.t is None or now - self.t >= 1:
            cur = {}
            for line in self.ps().splitlines():
                p = line.split(None, 3)
                if len(p) == 4 and os.path.basename(p[3]).lower() in self.names and int(p[1]) not in self.mine:
                    cur[p[0]] = (cpu_seconds(p[2]), os.path.basename(p[3]))
            if self.t is not None:
                use = [((cur[p][0] - self.prev[p][0]) / (now - self.t) * 100, p) for p in cur if p in self.prev]
                if use and max(use)[0] >= self.cpu:
                    pct, pid = max(use)
                    self.last_busy, self.who = now, f"{cur[pid][1]} pid {pid} at {pct:.0f}% CPU"
            self.prev, self.t = cur, now
        return self.who if self.last_busy is not None and now - self.last_busy < self.quiet else ""
