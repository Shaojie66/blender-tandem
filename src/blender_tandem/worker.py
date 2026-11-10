"""The half of blender-tandem that runs inside Blender. Bundled libraries only: this file is copied into
the job directory and run as is on both machines.

  blender -b <source.blend> -P worker.py -- snapshot <out.blend>
      Save a frozen, self-contained copy: images, fonts, sounds and linked libraries are packed into it,
      so the copy renders on a machine that has none of the source files. Prints
        [tandem] SCENE {json}        frame range, fps, engine, resolution
        [tandem] EXTERNAL <path>     a file that could not be packed (the remote machine cannot render)
        [tandem] MISSING <path>      a file that does not exist at all

  blender -b <job>/scene.blend -P worker.py -- render <job_dir> <local|remote> <percent|-> <samples|-> <f,f,f>
      Render the listed frames into <job_dir>/frames/f_NNNN.png (16-bit PNG). local walks the list
      upwards, remote downwards. Before each frame the worker writes the frame number to
      <job_dir>/<role>.cur (its claim) and reads the other machine's .cur; it stops where the two meet.
      An empty or absent .cur of the other machine means "the other machine is not rendering".
      A frame is written under a temporary name and renamed when complete, so an existing f_NNNN.png is whole.
      A non-empty <job_dir>/stop ends the run after the current frame; <job_dir>/<role>.hold does the same
      for that machine only.
"""
import glob, json, os, sys, time
import bpy

a = sys.argv[sys.argv.index("--") + 1:]
mode = a[0] if a else ""


def externals():
    """Absolute paths of the files this .blend still reads from disk."""
    out = []
    for im in bpy.data.images:
        if im.filepath and im.packed_file is None and im.source in {"FILE", "SEQUENCE", "MOVIE", "TILED"}:
            out.append(im.filepath)
    for group in (bpy.data.fonts, bpy.data.sounds, bpy.data.volumes):
        out += [d.filepath for d in group
                if d.filepath and d.filepath != "<builtin>" and getattr(d, "packed_file", None) is None]
    out += [l.filepath for l in bpy.data.libraries if getattr(l, "packed_file", None) is None]
    out += [c.filepath for c in bpy.data.cache_files if c.filepath]
    return sorted({os.path.realpath(bpy.path.abspath(p)) for p in out})


if mode == "snapshot":
    out = os.path.abspath(a[1])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    for pack in ("pack_all", "pack_libraries"):   # each is best effort; what is left over is reported below
        try:
            getattr(bpy.ops.file, pack)()
        except Exception as e:
            print(f"[tandem] {pack}: {e}")
    for p in externals():
        print(f"[tandem] {'EXTERNAL' if os.path.exists(p) else 'MISSING'} {p}")
    sc = bpy.context.scene
    print("[tandem] SCENE " + json.dumps({
        "frame_start": sc.frame_start, "frame_end": sc.frame_end, "frame_step": sc.frame_step,
        "fps": sc.render.fps / sc.render.fps_base, "engine": sc.render.engine,
        "resolution": [sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage]}))
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, relative_remap=True)
    print(f"[tandem] snapshot {out}")
    sys.exit(0)

if mode != "render":
    sys.exit(__doc__)

job, role, pct, samples = os.path.abspath(a[1]), a[2], a[3], a[4]
frames = [int(f) for f in a[5].split(",")]
other = {"local": "remote", "remote": "local"}[role]
up = role == "local"
frames.sort(reverse=not up)
fdir = os.path.join(job, "frames")
os.makedirs(fdir, exist_ok=True)
for stale in glob.glob(os.path.join(fdir, f".tmp_{role}_*")):   # left by a run that was killed mid-frame
    os.remove(stale)
sc = bpy.context.scene

missing = [p for p in externals() if not os.path.exists(p)]
if missing:
    sys.exit(f"[tandem] {role} is missing files, not rendering: {missing}")

devices = "scene settings"
if sc.render.engine == "CYCLES":
    # the render device is a per-machine preference, not stored in the .blend: use everything this Mac has
    prefs = bpy.context.preferences.addons["cycles"].preferences
    try:
        prefs.compute_device_type = "METAL"
        prefs.get_devices()
        for d in prefs.devices:
            d.use = True
        sc.cycles.device = "GPU"
        devices = ", ".join(d.name for d in prefs.devices if d.use)
    except TypeError as e:
        print(f"[tandem] {role} no Metal, CPU only: {e}")
        sc.cycles.device = "CPU"
        devices = "CPU"
    if samples != "-":
        sc.cycles.samples = int(samples)
if pct != "-":
    sc.render.resolution_percentage = int(pct)
im = sc.render.image_settings
try:
    im.media_type = "IMAGE"
except Exception:
    pass
im.file_format = "PNG"
im.color_mode, im.color_depth = ("RGBA" if sc.render.film_transparent else "RGB"), "16"
print(f"[tandem] {role} start: {len(frames)} frames {'up' if up else 'down'}, {sc.render.resolution_percentage}%, "
      f"{sc.render.engine}, {devices}", flush=True)


def read(name):
    try:
        return open(os.path.join(job, name)).read().strip()
    except OSError:
        return ""


def claim(f):
    tmp = os.path.join(job, f".{role}.cur.tmp")
    with open(tmp, "w") as fh:
        fh.write(str(f))
    os.replace(tmp, os.path.join(job, f"{role}.cur"))


done = 0
for f in frames:
    final = os.path.join(fdir, f"f_{f:04d}.png")
    if read("stop"):
        print(f"[tandem] {role} stop requested", flush=True)
        break
    if read(f"{role}.hold"):
        print(f"[tandem] {role} gives way: {read(f'{role}.hold')}", flush=True)
        break
    if os.path.exists(final):
        continue
    oc = read(f"{other}.cur")
    if oc and (f >= int(oc) if up else f <= int(oc)):
        print(f"[tandem] {role} met {other} at frame {oc}", flush=True)
        break
    claim(f)
    sc.frame_set(f)
    tmp = os.path.join(fdir, f".tmp_{role}_{f:04d}.png")
    sc.render.filepath = tmp
    t = time.time()
    bpy.ops.render.render(write_still=True)
    os.replace(tmp, final)
    done += 1
    print(f"[tandem] {role} frame {f} {time.time() - t:.1f}s", flush=True)
print(f"[tandem] {role} finished: {done} frames rendered", flush=True)
