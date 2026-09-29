"""DanDon Media bumper — Blender render script for one piece of connective tissue.

Run headless (render_bumpers.bat does this for every piece):
    blender -b -P blender/bumper.py -- blender/anim_01.json

The JSON gives the on-screen text, duration, output path and optional
keyframe background. If `blender/host.glb` exists (e.g. your Meshy export of
the DanDon host), it is dropped in and turned slowly beside the text.
Everything here is plain bpy, so open the .blend it saves and art-direct it.
"""
import json
import math
import os
import sys

import bpy

args = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
here = os.path.dirname(os.path.abspath(sys.argv[sys.argv.index("--") + 1]))
pkg = os.path.dirname(here)


def path(rel):
    return os.path.join(pkg, rel) if rel and not os.path.isabs(rel) else rel


def hex_rgba(h, a=1.0):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (a,)


FPS = 30
frames = max(int(round(float(args.get("duration_sec", 3)) * FPS)), 12)
accent = hex_rgba(args.get("accent", "#e63946"))

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = 1440, 810
scene.render.fps = FPS
scene.frame_start, scene.frame_end = 1, frames
scene.view_settings.view_transform = "Standard"  # keep brand colours exact
# EEVEE on the GPU (fast on a 4060 Ti). Set DANDON_BLENDER_ENGINE=CYCLES on machines without one.
for engine in ([os.environ["DANDON_BLENDER_ENGINE"]] if os.environ.get("DANDON_BLENDER_ENGINE")
               else ["BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"]):
    try:
        scene.render.engine = engine
        break
    except TypeError:
        continue
if scene.render.engine == "CYCLES":
    scene.cycles.samples = 16

world = bpy.data.worlds.new("DanDonWorld")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.05, 0.07, 1)
scene.world = world

cam_data = bpy.data.cameras.new("Cam")
cam_data.type = "ORTHO"
cam_data.ortho_scale = 16
cam = bpy.data.objects.new("Cam", cam_data)
cam.location = (0, 0, 20)
scene.collection.objects.link(cam)
scene.camera = cam

light = bpy.data.objects.new("Key", bpy.data.lights.new("Key", "SUN"))
light.data.energy = 4
light.rotation_euler = (math.radians(35), 0, math.radians(25))
scene.collection.objects.link(light)


def emission_material(name, rgba, strength=1.0, image=None):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = strength
    if image:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(image)
        nt.links.new(tex.outputs["Color"], em.inputs["Color"])
    else:
        em.inputs["Color"].default_value = rgba
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


# Background: the keyframe from the image model if there is one, else charcoal.
bg_img = path(args.get("background_image"))
bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, -1))
bg = bpy.context.active_object
bg.scale = (16, 9, 1)
bg.data.materials.append(emission_material(
    "BG", (0.08, 0.08, 0.1, 1), 0.55 if bg_img and os.path.exists(bg_img) else 1.0,
    bg_img if bg_img and os.path.exists(bg_img) else None))

# Accent bar that wipes across.
bpy.ops.mesh.primitive_plane_add(size=1, location=(0, -2.6, 0))
bar = bpy.context.active_object
bar.scale = (0.01, 0.35, 1)
bar.data.materials.append(emission_material("Bar", accent, 2.0))
bar.keyframe_insert("scale", frame=1)
bar.scale = (13, 0.35, 1)
bar.keyframe_insert("scale", frame=min(14, frames))

# Headline text: pops in, holds, drifts.
text = args.get("on_screen_text") or args.get("concept") or args.get("brand", "DanDon Media")
curve = bpy.data.curves.new("Headline", "FONT")
curve.body = text[:60]
curve.align_x, curve.align_y = "CENTER", "CENTER"
curve.size = 1.35 if len(text) < 24 else 0.95
curve.extrude = 0.06
headline = bpy.data.objects.new("Headline", curve)
scene.collection.objects.link(headline)
headline.data.materials.append(emission_material("Ink", (0.93, 0.93, 0.95, 1), 1.4))
headline.scale = (0.01, 0.01, 0.01)
headline.keyframe_insert("scale", frame=1)
headline.scale = (1.08, 1.08, 1.08)
headline.keyframe_insert("scale", frame=min(9, frames))
headline.scale = (1, 1, 1)
headline.keyframe_insert("scale", frame=min(13, frames))
headline.location = (0, 0.3, 0)
headline.keyframe_insert("location", frame=min(13, frames))
headline.location = (0.4, 0.3, 0)
headline.keyframe_insert("location", frame=frames)

# Optional 3D host from Meshy (or any GLB), turning beside the headline.
host = os.path.join(here, "host.glb")
if os.path.exists(host):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=host)
    imported = [o for o in bpy.data.objects if o not in before]
    roots = [o for o in imported if o.parent is None]
    pivot = bpy.data.objects.new("HostPivot", None)
    scene.collection.objects.link(pivot)
    for o in roots:
        o.parent = pivot
    pivot.location = (5.2, -0.5, 2)
    pivot.scale = (2.2, 2.2, 2.2)
    pivot.rotation_euler = (math.radians(80), 0, 0)
    pivot.keyframe_insert("rotation_euler", frame=1)
    pivot.rotation_euler = (math.radians(80), 0, math.radians(35))
    pivot.keyframe_insert("rotation_euler", frame=frames)
    headline.location.x -= 2.0

out = path(args["output"])
os.makedirs(os.path.dirname(out), exist_ok=True)
scene.render.image_settings.file_format = "FFMPEG"
scene.render.ffmpeg.format = "MPEG4"
scene.render.ffmpeg.codec = "H264"
scene.render.ffmpeg.constant_rate_factor = "HIGH"
scene.render.filepath = out
bpy.ops.wm.save_as_mainfile(filepath=os.path.splitext(out)[0] + ".blend")
if not args.get("save_only"):
    bpy.ops.render.render(animation=True)
print("DANDON_BUMPER_DONE", out)
