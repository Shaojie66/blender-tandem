"""The scheduling rules, with fake workers that follow the same file protocol as worker.py."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from blender_tandem.config import Config                               # noqa: E402
from blender_tandem.job import frame_path, missing, read, write       # noqa: E402
from blender_tandem.scheduler import Scheduler                         # noqa: E402


class FakeWorker:
    """Renders `per_tick` frames each tick(), honouring cursors, stop and hold like the real worker."""

    def __init__(self, job, role, per_tick=1):
        self.job, self.role, self.per_tick = job, role, per_tick
        self.on, self.rc, self.queue, self.starts, self.kills, self.rendered = False, None, [], 0, 0, []

    def begin(self, frames):
        self.queue, self.on, self.rc = sorted(frames, reverse=self.role == "remote"), True, None
        self.starts += 1

    def tick(self):
        for _ in range(self.per_tick if self.on else 0):
            if not self.one():
                self.on, self.rc = False, 0
                return

    def one(self):
        p = lambda n: os.path.join(self.job, n)   # noqa: E731
        other = "remote" if self.role == "local" else "local"
        while self.queue:
            f = self.queue[0]
            if read(p("stop")) or read(p(f"{self.role}.hold")):
                return False
            if os.path.exists(frame_path(self.job, f)):
                self.queue.pop(0)
                continue
            oc = read(p(f"{other}.cur"))
            if oc and (f >= int(oc) if self.role == "local" else f <= int(oc)):
                return False
            write(p(f"{self.role}.cur"), str(f))
            write(frame_path(self.job, f), "png")
            with open(p(f"{self.role}.log"), "a") as fh:
                fh.write(f"[tandem] {self.role} frame {f} 1.0s\n")
            self.rendered.append(f)
            self.queue.pop(0)
            return True
        return False


class FakeLocal(FakeWorker):
    def __init__(self, job, per_tick=1, broken=False):
        super().__init__(job, "local", per_tick)
        self.broken = broken

    def start(self, frames):
        self.begin(frames)
        if self.broken:
            self.on, self.rc = False, 1
        return 4242

    def running(self):
        return self.on

    def exit_code(self):
        return None if self.on else self.rc

    def kill(self):
        if self.on:
            self.on, self.kills = False, self.kills + 1


class FakeRemote(FakeWorker):
    def __init__(self, job, per_tick=1):
        super().__init__(job, "remote", per_tick)
        self.dead_reason, self.stopped = "", 0

    def start(self, percent, samples, frames):
        self.begin(frames)
        return True

    def running(self):
        return self.on

    def pull(self, cursor=True):
        return 0

    def push_state(self):
        return 0

    def exchange(self):
        pass

    def dead(self):
        return self.dead_reason

    def stop(self, kill=True):
        self.on, self.stopped = False, self.stopped + 1


class Script:
    """Stands in for Others: busy() answers from a list, then ''."""

    def __init__(self, answers):
        self.answers, self.mine = list(answers), set()

    def busy(self):
        return self.answers.pop(0) if self.answers else ""


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.job = self.tmp.name
        os.makedirs(os.path.join(self.job, "frames"))
        self.now = 0.0
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def sched(self, n, local, remote=None, others=None, mode="both", **cfg):
        cfg = Config(**{"remote_after": 0, "remote_min": 0, **cfg})
        return Scheduler(self.job, list(range(1, n + 1)), cfg, local, remote, others, mode,
                         clock=lambda: self.now, sleep=lambda s: None, log=self.logs.append)

    def drive(self, s, workers, limit=200, dt=10, each=None):
        """step -> exchange -> every worker renders -> time passes, until the scheduler stops."""
        for i in range(limit):
            result = s.step()
            if result:
                s.finish()
                return result
            s.exchange()
            if each:
                each(i)
            for w in workers:
                w.tick()
            self.now += dt
        self.fail("scheduler did not stop")

    def test_both_machines_meet_in_the_middle(self):
        local, remote = FakeLocal(self.job, 2), FakeRemote(self.job, 1)
        s = self.sched(30, local, remote)
        self.assertEqual(self.drive(s, [local, remote]), "done")
        self.assertEqual(missing(self.job, s.frames), [])
        self.assertTrue(local.rendered and remote.rendered)
        self.assertLess(max(local.rendered), min(remote.rendered) + 1)          # two halves, no interleaving
        self.assertLessEqual(len(local.rendered) + len(remote.rendered), 31)    # at most the meeting frame twice
        self.assertGreater(len(local.rendered), len(remote.rendered))           # the faster one did more

    def test_small_job_never_starts_the_remote(self):
        local, remote = FakeLocal(self.job, 1), FakeRemote(self.job)
        s = self.sched(5, local, remote, remote_after=30, remote_min=120)       # 10 s a frame: 50 s in all
        self.assertEqual(self.drive(s, [local, remote]), "done")
        self.assertEqual(remote.starts, 0)

    def test_remote_joins_once_the_job_proves_long(self):
        local, remote = FakeLocal(self.job, 1), FakeRemote(self.job)
        s = self.sched(60, local, remote, remote_after=30, remote_min=120)      # 600 s alone
        self.assertEqual(self.drive(s, [local, remote]), "done")
        self.assertEqual(remote.starts, 1)
        self.assertGreaterEqual(len(local.rendered), 3)                         # the remote was not there at once

    def test_remote_joins_when_local_has_no_frame_yet(self):
        local, remote = FakeLocal(self.job, 0), FakeRemote(self.job)            # local never finishes a frame
        s = self.sched(6, local, remote, remote_after=30, remote_min=120)
        self.assertEqual(self.drive(s, [local, remote]), "done")
        self.assertEqual(len(remote.rendered), 6)

    def test_local_gives_way_after_its_frame_and_resumes(self):
        local, remote = FakeLocal(self.job), FakeRemote(self.job)
        s = self.sched(40, local, remote, Script(["", "", "preview", "preview", "preview", "preview"]))
        s.frame_time_left = lambda: 5                                           # nearly done: let it finish
        seen = []
        self.assertEqual(self.drive(s, [local, remote], each=lambda i: seen.append((s.holding, local.on))), "done")
        self.assertFalse(any("at once" in l for l in self.logs))                # its frame was not dropped
        self.assertEqual(local.starts, 2)                                       # before and after the hold
        self.assertIn((True, False), seen)                                      # it really stood aside
        self.assertEqual(read(os.path.join(self.job, "local.hold")), "")
        self.assertTrue(any("gives way" in l for l in self.logs) and any("resumes" in l for l in self.logs))

    def test_local_drops_a_long_frame_and_the_remote_takes_it(self):
        local, remote = FakeLocal(self.job), FakeRemote(self.job)
        s = self.sched(12, local, remote, Script(["", "", "preview"] + ["preview"] * 50))
        s.frame_time_left = lambda: 100                                         # a long frame: drop it
        self.assertEqual(self.drive(s, [local, remote]), "done")
        self.assertEqual(local.kills, 1)
        self.assertEqual(local.starts, 1)                                       # held for the rest of the job
        self.assertEqual(sorted(local.rendered + remote.rendered), list(range(1, 13)))

    def test_cursor_is_rewound_to_the_last_finished_frame(self):
        local = FakeLocal(self.job)
        s = self.sched(10, local)
        for f in (1, 2, 3):
            write(frame_path(self.job, f), "png")
        write(os.path.join(self.job, "local.cur"), "4")                         # 4 was claimed, then dropped
        s._rewind_cursor()
        self.assertEqual(read(os.path.join(self.job, "local.cur")), "3")

    def test_remote_disappears_and_local_finishes(self):
        local, remote = FakeLocal(self.job), FakeRemote(self.job)
        s = self.sched(20, local, remote)

        def unplug(i):
            if i == 3:
                remote.dead_reason, remote.per_tick = "unreachable for a minute", 0
        self.assertEqual(self.drive(s, [local, remote], each=unplug), "done")
        self.assertEqual(remote.stopped, 1)
        self.assertEqual(missing(self.job, s.frames), [])
        self.assertEqual(read(os.path.join(self.job, "stop")), "")              # the local worker was not stopped

    def test_remote_worker_dies_and_local_finishes(self):
        local, remote = FakeLocal(self.job), FakeRemote(self.job)
        s = self.sched(20, local, remote)

        def crash(i):
            if i == 3:
                remote.on, remote.rc = False, 1
        self.assertEqual(self.drive(s, [local, remote], each=crash), "done")
        self.assertEqual(missing(self.job, s.frames), [])
        self.assertEqual(remote.starts, 1)                                      # it is not started again
        self.assertTrue(any("ended early" in l for l in self.logs))

    def test_meeting_in_the_middle_is_not_reported_as_an_early_end(self):
        local, remote = FakeLocal(self.job), FakeRemote(self.job)
        self.assertEqual(self.drive(self.sched(40, local, remote), [local, remote]), "done")
        self.assertFalse(any("ended early" in l for l in self.logs))

    def test_remote_only_reports_what_is_left(self):
        local, remote = FakeLocal(self.job), FakeRemote(self.job)
        s = self.sched(10, local, remote, mode="remote")

        def crash(i):
            if i == 2:
                remote.on, remote.rc = False, 1
        self.assertEqual(self.drive(s, [remote], each=crash), "stuck")
        self.assertEqual(local.starts, 0)
        self.assertTrue(missing(self.job, s.frames))

    def test_broken_local_worker_leaves_the_job_to_the_remote(self):
        local, remote = FakeLocal(self.job, broken=True), FakeRemote(self.job)
        s = self.sched(8, local, remote)
        self.assertEqual(self.drive(s, [local, remote]), "done")
        self.assertEqual(local.starts, 2)                                       # tried twice, then no more
        self.assertEqual(len(remote.rendered), 8)

    def test_broken_local_worker_alone_stops(self):
        local = FakeLocal(self.job, broken=True)
        s = self.sched(8, local)
        self.assertEqual(self.drive(s, [local]), "stuck")
        self.assertIn("failed twice", s.note)

    def test_resume_renders_only_what_is_missing(self):
        for f in (1, 2, 3, 9, 10):
            write(frame_path(self.job, f), "png")
        local = FakeLocal(self.job)
        s = self.sched(10, local)
        self.assertEqual(self.drive(s, [local]), "done")
        self.assertEqual(local.rendered, [4, 5, 6, 7, 8])

    def test_no_remote_and_held_waits(self):
        local = FakeLocal(self.job)
        s = self.sched(4, local, None, Script(["preview"] * 5))
        self.assertEqual(self.drive(s, [local]), "done")
        self.assertEqual(local.rendered, [1, 2, 3, 4])
        self.assertGreaterEqual(self.now, 50)                                   # nothing happened while held

    def test_a_long_wait_says_who_it_is_waiting_for(self):
        local = FakeLocal(self.job)
        s = self.sched(4, local, None, Script(["Blender pid 1 at 900% CPU", "Blender pid 1 at 700% CPU",
                                               "Blender pid 2 at 600% CPU", "Blender pid 1 at 650% CPU"]))
        self.assertEqual(self.drive(s, [local]), "done")
        told = [l for l in self.logs if "gives way" in l or "still waiting" in l]
        self.assertEqual(len(told), 2)                                          # once per program, not per reading
        self.assertIn("Blender pid 1", told[0])
        self.assertIn("still waiting", told[1])
        self.assertIn("Blender pid 2", told[1])


if __name__ == "__main__":
    unittest.main()
