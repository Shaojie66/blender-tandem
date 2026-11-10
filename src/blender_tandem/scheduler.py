"""Who renders what, decided one step at a time.

The local Mac walks the missing frames upwards, the remote Mac walks them downwards, and each stops when
it reaches the other's cursor. Around that the scheduler decides four things:

  when to start the remote   only once the local Mac has rendered for a while and is clearly not going to
                             finish soon (or cannot render at all right now)
  when the local Mac gives   while another program is rendering on it; its worker ends and a new one
  way                        starts when that program has been quiet for a while. The remote keeps going.
  what happens when the      its unfinished frames go to the local Mac
  remote disappears
  when to stop               every frame is there, or nobody is left who could render the rest

The workers and the clock are passed in, so the logic can be tested without Blender (tests/).
"""
import os
import time

from .job import frame_path, log, log_size, missing, read, timings, write


def fmt(seconds):
    s = int(seconds)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


class Scheduler:
    def __init__(self, job, frames, cfg, local, remote=None, others=None, mode="both",
                 percent=None, samples=None, clock=time.time, sleep=time.sleep, log=log):
        """mode: 'both', 'local' or 'remote'. `remote` None = no usable remote. `others` None = never give way."""
        self.job, self.frames, self.cfg = job, frames, cfg
        self.local, self.others = local, others
        self.remote = remote if mode != "local" else None
        self.use_local = mode != "remote"
        self.percent, self.samples = percent, samples
        self.clock, self.sleep, self.log = clock, sleep, log
        self.todo = missing(job, frames)
        self.local_on = self.remote_live = self.remote_started = self.holding = False
        self.local_fails, self.local_since = 0, None
        self.note = ""            # why frames were left unrendered, if they were
        self.offsets = {r: log_size(job, r) for r in ("local", "remote")}
        self.shown = None
        self.held_by = set()

    def p(self, name):
        return os.path.join(self.job, name)

    # ---- one decision round ------------------------------------------------------------------------
    def step(self):
        """Returns '' to keep going, 'done' when every frame is there, 'stuck' when nobody can go on."""
        now = self.clock()
        ended = self.remote_live and not self.remote.running()
        if ended:
            self.remote.pull()            # the frames it finished after the last exchange
            self._remote_off()
        left = missing(self.job, self.todo)
        if ended:
            cur = read(self.p("local.cur"))
            beyond = [f for f in left if not cur or f > int(cur)]   # more than the frame local is on
            if beyond:                    # it did not get as far as the local cursor: it crashed or was killed
                self.log(f"the remote worker ended early with {len(beyond)} frames left"
                         + ("; this Mac renders them" if self.use_local else ""))
        if not left:
            return "done"
        if self.local_on and not self.local.running():   # it met the remote, gave way, or failed
            self.local_on = False
            self.local_fails = self.local_fails + 1 if self.local.exit_code() else 0
            if self.local_fails >= 2:
                self.use_local = False
                self.note = f"the local worker failed twice, see {self.p('local.log')}"
                self.log(self.note)

        hold = self.others.busy() if self.others and self.use_local else ""
        who = hold.split(" at ")[0]                 # the program without its changing CPU figure
        if hold and self.holding and who not in self.held_by:
            self.log(f"local still waiting ({hold})")   # a long wait should say what it is waiting for
        self.held_by = self.held_by | {who} if hold else set()   # each program is named once per wait
        if hold and not self.holding:
            self.holding = True
            write(self.p("local.hold"), hold)
            if self.local_on and self.frame_time_left() > self.cfg.yield_abort:
                self.local.kill()
                self.local_on = False
                self._rewind_cursor()
                self.log(f"local gives way at once, dropping its frame ({hold})")
            else:
                self.log(f"local gives way ({hold})" + (" after its current frame" if self.local_on else ""))
            if self.remote_live:
                self.remote.push_state()
        elif self.holding and not hold:
            self.holding = False
            write(self.p("local.hold"), "")   # a worker still on its last frame simply carries on
            self.log("local resumes")

        if self.remote and not self.remote_started and self.want_remote(now, left, hold):
            self.remote_started = True
            write(self.p("remote.cur"), "")
            if self.remote.start(self.percent, self.samples, left[::-1]):
                self.remote_live = True
                self.log(f"remote started on {len(left)} frames")
            else:
                self.note = "could not send the job to the remote"
                self.log(self.note + "; rendering everything here")

        if self.use_local and not self.local_on and not hold:
            cur = read(self.p("remote.cur")) if self.remote_live else ""
            mine = [f for f in left if f < int(cur)] if cur else left   # the remote has its cursor and above
            if mine:
                group = self.local.start(mine)
                self.local_on = True
                if self.local_since is None:
                    self.local_since = now
                if self.others:
                    self.others.mine.add(group)
        if not self.local_on and not self.remote_live and not self.use_local:
            return "stuck"
        return ""

    def want_remote(self, now, left, hold):
        """Start the remote? Yes if the local Mac cannot render now; otherwise only after it has rendered
        for a while and, at the speed it showed, would still need a long time for what is left."""
        if not self.use_local or hold:
            return True
        if self.local_since is None or now - self.local_since < self.cfg.remote_after:
            return False
        done = len(timings(self.job, "local", self.offsets["local"]))
        per_frame = (now - self.local_since) / done if done else float("inf")
        return len(left) * per_frame > self.cfg.remote_min

    def frame_time_left(self):
        """Seconds the local frame in progress still needs, judging by the frames before it."""
        t = timings(self.job, "local", self.offsets["local"])
        if not t or not read(self.p("local.cur")):
            return float("inf")     # still loading the scene, or no frame to compare with
        return max(sum(t) / len(t) - (time.time() - os.path.getmtime(self.p("local.cur"))), 0)

    def _rewind_cursor(self):
        """After dropping a frame mid-render, claim only what is really finished, so that the remote takes
        the dropped frame instead of stopping in front of it."""
        last = ""
        for f in self.todo:
            if not os.path.exists(frame_path(self.job, f)):
                break
            last = str(f)
        write(self.p("local.cur"), last)

    def _remote_off(self):
        """An empty remote cursor tells the local worker that everything left is its own."""
        self.remote_live = False
        write(self.p("remote.cur"), "")

    # ---- between rounds ----------------------------------------------------------------------------
    def exchange(self):
        if not self.remote_live:
            return
        self.remote.exchange()
        if self.remote.running() and (why := self.remote.dead()):
            self.note = f"remote dropped: {why}"
            self.log(f"dropping the remote: {why}")
            self.remote.stop(kill=True)
            self._remote_off()

    def report(self):
        left = len(missing(self.job, self.frames))
        state = (len(self.frames) - left, "waiting" if self.holding and not self.local_on else read(self.p("local.cur")),
                 read(self.p("remote.cur")) if self.remote_live else "")
        if state == self.shown:
            return
        self.shown = state
        speed, rate = {}, 0.0
        for role, on in (("local", self.local_on), ("remote", self.remote_live)):
            t = timings(self.job, role, self.offsets[role])
            speed[role] = f" ({sum(t) / len(t):.1f}s/frame)" if t else ""
            rate += len(t) / sum(t) if t and on and sum(t) else 0
        where = lambda v: f"at {v}" if v.isdigit() else v or "-"   # noqa: E731
        self.log(f"{state[0]}/{len(self.frames)} frames   local {where(state[1])}{speed['local']}"
                 f"   remote {where(state[2])}{speed['remote']}" + (f"   about {fmt(left / rate)} left" if rate and left else ""))

    # ---- the whole job -----------------------------------------------------------------------------
    def run(self):
        """Render until done or stuck. Returns the frames still missing."""
        while not self.step():
            self.sleep(self.cfg.sync if self.remote_live else 2)
            self.exchange()
            self.report()
        self.finish()
        return missing(self.job, self.todo)

    def finish(self):
        self.local.kill()       # if still running, it is on a frame the remote also rendered
        if self.remote and self.remote_started:
            if self.remote.running():
                self.remote.stop(kill=True)
            self.remote.pull(cursor=False)

    def interrupt(self):
        """Ctrl-C: stop both machines now. Finished frames stay; the same command resumes."""
        write(self.p("stop"), "1")
        self.local.kill()
        if self.remote and self.remote_started:
            self.remote.stop(kill=True)
