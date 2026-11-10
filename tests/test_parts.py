"""The small pure pieces: process sampling, frame ranges, config loading."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from blender_tandem import config                                    # noqa: E402
from blender_tandem.job import parse_frames                          # noqa: E402
from blender_tandem.others import Others, cpu_seconds                # noqa: E402
from blender_tandem.remote import shell_path                         # noqa: E402

APP = "/Applications/Blender.app/Contents/MacOS/Blender"


class OthersCase(unittest.TestCase):
    def watch(self, **kw):
        self.now, self.table = 0.0, ""
        return Others(ps=lambda: self.table, clock=lambda: self.now, **kw)

    def at(self, t, *rows):
        self.now, self.table = t, "\n".join(f"{pid} {pgid} {cpu} {comm}" for pid, pgid, cpu, comm in rows)

    def test_cpu_seconds(self):
        self.assertAlmostEqual(cpu_seconds("132:26.11"), 132 * 60 + 26.11)
        self.assertAlmostEqual(cpu_seconds("1:02:03.5"), 3723.5)
        self.assertAlmostEqual(cpu_seconds("2-01:02:03.5"), 2 * 86400 + 3723.5)

    def test_a_rendering_blender_is_busy_and_an_idle_one_is_not(self):
        o = self.watch()
        self.at(0, (10, 10, "1:00.00", APP), (20, 20, "5:00.00", APP))
        self.assertEqual(o.busy(), "")                       # first reading: nothing to compare with
        self.at(10, (10, 10, "1:00.20", APP), (20, 20, "6:40.00", APP))   # 2% and 1000%
        self.assertEqual(o.busy(), "Blender pid 20 at 1000% CPU")

    def test_own_workers_and_other_programs_do_not_count(self):
        o = self.watch()
        o.mine.add(20)
        self.at(0, (20, 20, "5:00.00", APP), (30, 30, "0:10.00", "/usr/local/bin/ffmpeg"))
        o.busy()
        self.at(10, (20, 20, "6:40.00", APP), (30, 30, "1:10.00", "/usr/local/bin/ffmpeg"))
        self.assertEqual(o.busy(), "")

    def test_configured_names_count(self):
        o = self.watch(names=["Blender", "ffmpeg"])
        self.at(0, (30, 30, "0:10.00", "/usr/local/bin/ffmpeg"))
        o.busy()
        self.at(10, (30, 30, "0:20.00", "/usr/local/bin/ffmpeg"))
        self.assertIn("ffmpeg pid 30 at 100%", o.busy())

    def test_stays_busy_until_quiet_long_enough(self):
        o = self.watch(quiet=60)
        self.at(0, (20, 20, "5:00.00", APP))
        o.busy()
        self.at(10, (20, 20, "5:30.00", APP))
        self.assertTrue(o.busy())
        self.at(40, (20, 20, "5:30.00", APP))                # the render ended at t=10
        self.assertTrue(o.busy())
        self.at(69, (20, 20, "5:30.00", APP))
        self.assertTrue(o.busy())
        self.at(71, (20, 20, "5:30.00", APP))
        self.assertEqual(o.busy(), "")

    def test_names_with_spaces(self):
        o = self.watch(names=["DaVinci Resolve"])
        self.at(0, (40, 40, "0:00.00", "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/MacOS/DaVinci Resolve"))
        o.busy()
        self.at(10, (40, 40, "0:30.00", "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/MacOS/DaVinci Resolve"))
        self.assertIn("DaVinci Resolve pid 40", o.busy())


class PartsCase(unittest.TestCase):
    def test_parse_frames(self):
        scene = {"frame_start": 3, "frame_end": 6, "frame_step": 1}
        self.assertEqual(parse_frames(None, scene), [3, 4, 5, 6])
        self.assertEqual(parse_frames("10-12", scene), [10, 11, 12])
        self.assertEqual(parse_frames("7", scene), [7])
        for bad in ("5-2", "a-b", "1-", ""):
            with self.assertRaises(SystemExit):
                parse_frames(bad, scene)

    def test_shell_path(self):
        self.assertEqual(shell_path("tandem-jobs/x"), '"$HOME"/tandem-jobs/x')
        self.assertEqual(shell_path("~/jobs/.busy"), '"$HOME"/jobs/.busy')
        self.assertEqual(shell_path("/Volumes/My Disk/jobs"), "'/Volumes/My Disk/jobs'")

    def test_config_file_and_environment(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml") as fh:
            fh.write('[remote]\nhost = "second-mac"\nmin_job = 300\n[yield]\nprocesses = ["Blender", "ffmpeg"]\n')
            fh.flush()
            cfg = config.load(fh.name, env={})
            self.assertEqual((cfg.remote_host, cfg.remote_min, cfg.yield_processes),
                             ("second-mac", 300, ["Blender", "ffmpeg"]))
            self.assertEqual(config.load(fh.name, env={"TANDEM_REMOTE": "other"}).remote_host, "other")

    def test_config_rejects_unknown_settings(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml") as fh:
            fh.write('[remote]\nhots = "typo"\n')
            fh.flush()
            with self.assertRaises(SystemExit):
                config.load(fh.name, env={})

    def test_every_documented_setting_exists(self):
        fields = set(config.Config.__dataclass_fields__)
        self.assertTrue(set(config.KEYS.values()) <= fields)
        example = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples", "config.toml")
        config.load(example, env={})                         # the shipped example must load


if __name__ == "__main__":
    unittest.main()
