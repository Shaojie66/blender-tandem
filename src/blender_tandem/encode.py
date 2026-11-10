"""Optional: turn the finished frames into an H.264 mp4 with ffmpeg."""
import os
import shutil
import subprocess


def encode(job, frames, out, fps, crf=18):
    """frames must be consecutive. Raises SystemExit when ffmpeg is missing or the result is short."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("--encode needs ffmpeg and ffprobe on PATH")
    if frames != list(range(frames[0], frames[-1] + 1)):
        raise SystemExit("--encode needs consecutive frames")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"{fps:g}", "-start_number", str(frames[0]),
                    "-i", os.path.join(job, "frames", "f_%04d.png"), "-frames:v", str(len(frames)),
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "slow",
                    "-pix_fmt", "yuv420p", "-crf", str(crf), "-movflags", "+faststart", out], check=True)
    n = subprocess.check_output(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                                 "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", out], text=True).strip()
    if int(n) != len(frames):
        raise SystemExit(f"{out}: {n} frames encoded, expected {len(frames)}")
    return out
