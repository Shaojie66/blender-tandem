"""Make a tiny animated scene for testing:  blender -b --factory-startup -P tests/make_scene.py -- out.blend [frames]

A cube turning in front of the camera, Cycles, 320x180, 8 samples, plus one image texture next to the
.blend, so that packing the snapshot has something to pack."""
import os, sys
import bpy

a = sys.argv[sys.argv.index("--") + 1:]
out, frames = os.path.abspath(a[0]), int(a[1]) if len(a) > 1 else 12
os.makedirs(os.path.dirname(out), exist_ok=True)
sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.samples = 8
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = 320, 180, 100
sc.frame_start, sc.frame_end = 1, frames

cube = bpy.data.objects["Cube"]
cube.rotation_euler = (0, 0, 0)
cube.keyframe_insert("rotation_euler", frame=1)
cube.rotation_euler = (0, 0, 3.14159)
cube.keyframe_insert("rotation_euler", frame=frames)

tex = os.path.join(os.path.dirname(out), "tandem_test_texture.png")
img = bpy.data.images.new("tandem_test_texture", 16, 16)
img.pixels = [v for i in range(256) for v in ((i % 16) / 15, (i // 16) / 15, 0.5, 1.0)]
img.filepath_raw, img.file_format = tex, "PNG"
img.save()
mat = bpy.data.materials.new("textured")
if mat.node_tree is None:      # before Blender 5 a new material has no node tree until asked
    mat.use_nodes = True
nodes = mat.node_tree.nodes
node = nodes.new("ShaderNodeTexImage")
node.image = bpy.data.images.load(tex)
bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
cube.data.materials.append(mat)

bpy.ops.wm.save_as_mainfile(filepath=out)
print(f"[make_scene] {out}: frames 1-{frames}")
