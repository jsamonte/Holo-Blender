"""The acceptance test, run inside a headless Blender.

    "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" ^
        --background --factory-startup --python tools\\test_exporter.py

Builds objects in Blender, exports them with the extension's exporter, then
reads the GLB back and measures it exactly the way the lens would. The
numbers that matter are the ones the lens is told: bbox_mm, cm_per_unit and
offset_mm. If those are wrong the model is the wrong size or in the wrong
place, and nothing downstream can recover.

--factory-startup matters: without it this picks up the user's own addons
and preferences, and a test that depends on those is not a test.
"""

import json
import math
import os
import struct
import sys

import bpy

# Blender buffers stdout when it is piped, and sys.exit then throws the lot
# away: the run looked silent and successful whatever happened.
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "blender_extension", "holo_blender"))

from holo import exporter  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    {0}".format(name))
    else:
        print("  FAIL  {0}  {1}".format(name, detail))
        FAILURES.append(name)


def close(a, b, tolerance=1e-6):
    return abs(a - b) <= tolerance


def read_glb(blob):
    """Parse a GLB far enough to measure it, the way the lens would."""
    magic, version, total = struct.unpack_from("<4sII", blob, 0)
    assert magic == b"glTF" and version == 2, "not a glTF 2.0 binary"
    assert total == len(blob), "declared {0} but got {1}".format(total, len(blob))
    offset = 12
    gltf = None
    binary = b""
    while offset < len(blob):
        length, kind = struct.unpack_from("<I4s", blob, offset)
        chunk = blob[offset + 8:offset + 8 + length]
        if kind == b"JSON":
            gltf = json.loads(chunk.decode("utf-8"))
        elif kind.startswith(b"BIN"):
            binary = chunk
        offset += 8 + length
    return gltf, binary


def measured_extent(gltf):
    """Largest dimension across every POSITION accessor, in file units."""
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for mesh in gltf["meshes"]:
        for primitive in mesh["primitives"]:
            accessor = gltf["accessors"][primitive["attributes"]["POSITION"]]
            for axis in range(3):
                lo[axis] = min(lo[axis], accessor["min"][axis])
                hi[axis] = max(hi[axis], accessor["max"][axis])
    return [hi[i] - lo[i] for i in range(3)]


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def add_cube(name, size_m, location=(0, 0, 0)):
    """A cube of a given size in metres, sitting at a location."""
    bpy.ops.mesh.primitive_cube_add(size=size_m, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def main():
    print("")
    print("a 100 mm cube is 100 mm on the glasses")
    clear_scene()
    # Blender's default unit is the metre, so 0.1 m is 100 mm.
    cube = add_cube("Cube", 0.1)
    result = exporter.export([cube])

    check("exported something", result["glb"][:4] == b"glTF")
    check("bbox is 100 mm cubed",
          all(close(v, 100.0, 1e-4) for v in result["bbox_mm"]),
          str(result["bbox_mm"]))
    check("12 triangles for a box", result["triangles"] == 12,
          str(result["triangles"]))

    gltf, _ = read_glb(result["glb"])
    extent = measured_extent(gltf)
    check("file holds 0.1 units per side, since glTF says metres",
          all(close(v, 0.1, 1e-6) for v in extent), str(extent))
    check("one file unit is 100 lens units", close(result["cm_per_unit"], 100.0),
          str(result["cm_per_unit"]))
    # This is the whole acceptance test in one line: what the lens will draw.
    drawn_mm = max(extent) * result["cm_per_unit"] * 10
    check("so the lens would draw it 100 mm across", close(drawn_mm, 100.0, 1e-4),
          "{0:.4f}".format(drawn_mm))

    print("")
    print("the scene's unit scale is respected")
    clear_scene()
    # A scene authored at 1 unit = 1 cm. The same 0.1 unit cube is now 1 mm.
    bpy.context.scene.unit_settings.scale_length = 0.01
    small = add_cube("Small", 0.1)
    scaled = exporter.export([small])
    check("a 0.1 unit cube in a centimetre scene is 1 mm",
          all(close(v, 1.0, 1e-5) for v in scaled["bbox_mm"]),
          str(scaled["bbox_mm"]))
    gltf_s, _ = read_glb(scaled["glb"])
    drawn_mm = max(measured_extent(gltf_s)) * scaled["cm_per_unit"] * 10
    check("and the lens would draw it 1 mm across", close(drawn_mm, 1.0, 1e-5),
          "{0:.5f}".format(drawn_mm))

    print("")
    print("5 inches is 127 mm, the ruler test")
    clear_scene()
    inch = 0.0254
    ruler = add_cube("FiveInch", 5 * inch)
    measured = exporter.export([ruler])
    check("a 5 inch cube reports 127 mm",
          all(close(v, 127.0, 1e-4) for v in measured["bbox_mm"]),
          str(measured["bbox_mm"]))
    gltf_r, _ = read_glb(measured["glb"])
    drawn_mm = max(measured_extent(gltf_r)) * measured["cm_per_unit"] * 10
    check("and the lens would draw it 127 mm across", close(drawn_mm, 127.0, 1e-4),
          "{0:.4f}".format(drawn_mm))
    check("which is five inches", close(drawn_mm / 25.4, 5.0, 1e-5),
          "{0:.6f}".format(drawn_mm / 25.4))

    print("")
    print("Z up becomes Y up without changing the shape")
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    tall = bpy.context.active_object
    tall.name = "Tall"
    # 100 x 200 x 400 mm: three different axes, so a swap cannot hide.
    tall.scale = (0.1, 0.2, 0.4)
    bpy.context.view_layer.update()
    turned = exporter.export([tall])
    check("bbox matches the object",
          all(close(a, b, 1e-4) for a, b in
              zip(sorted(turned["bbox_mm"]), [100.0, 200.0, 400.0])),
          str(turned["bbox_mm"]))
    gltf_t, _ = read_glb(turned["glb"])
    ext_t = measured_extent(gltf_t)
    check("sorted proportions survive the turn",
          all(close(a, b, 1e-6) for a, b in
              zip(sorted(ext_t), sorted([0.1, 0.2, 0.4]))),
          str(ext_t))
    # Blender Z is up, and glTF Y is up, so the tallest axis must land on Y.
    check("height lands on the glTF Y axis", close(ext_t[1], 0.4, 1e-6),
          str(ext_t))

    print("")
    print("each object is written about its own origin, and says where it belongs")
    clear_scene()
    near = add_cube("Near", 0.1, location=(0, 0, 0.05))
    far = add_cube("Far", 0.02, location=(0.5, 0.3, 0.2))

    lone = exporter.export([far])
    gltf_far, _ = read_glb(lone["glb"])
    # The lens measures a box that also contains the origin. With the mesh
    # written about its own bottom centre that must change nothing, which is
    # what stops a part modelled far from the origin arriving tiny.
    accessors = [gltf_far["accessors"][p["attributes"]["POSITION"]]
                 for m in gltf_far["meshes"] for p in m["primitives"]]
    lo = [min([0.0] + [a["min"][i] for a in accessors]) for i in range(3)]
    hi = [max([0.0] + [a["max"][i] for a in accessors]) for i in range(3)]
    span = max(hi[i] - lo[i] for i in range(3))
    check("a part far from the origin still measures its own size",
          close(span, 0.02, 1e-6), str(span))
    check("a lone object reports no offset",
          all(close(v, 0.0, 1e-9) for v in lone["offset_mm"]),
          str(lone["offset_mm"]))

    depsgraph = bpy.context.evaluated_depsgraph_get()
    box_lo, box_hi = exporter.bounding_box_mm(
        [near, far], depsgraph, exporter.unit_scale(bpy.context))
    origin = exporter.bottom_centre_mm(box_lo, box_hi)
    a = exporter.export([near], origin_mm=origin)
    b = exporter.export([far], origin_mm=origin)
    check("two objects report different offsets",
          a["offset_mm"] != b["offset_mm"],
          "{0} vs {1}".format(a["offset_mm"], b["offset_mm"]))
    # Near sits at world (0, 0, 0.05) and is 100 mm, so its bottom centre is
    # (0, 0, 0). Far sits at (500, 300, 200) mm and is 20 mm, so its bottom
    # centre is (500, 300, 190). The gap is (500, 300, 190) in Blender axes.
    # Lens axes are x, z, -y, so it reads as (500, 190, -300).
    gap = [b["offset_mm"][i] - a["offset_mm"][i] for i in range(3)]
    check("the offset is the gap between their bottom centres, in lens axes",
          close(gap[0], 500.0, 1e-3) and close(gap[1], 190.0, 1e-3)
          and close(gap[2], -300.0, 1e-3),
          str(gap))

    print("")
    print("colour comes from the material")
    clear_scene()
    painted = add_cube("Painted", 0.1)
    material = bpy.data.materials.new("Red")
    # Materials already use nodes in Blender 5, and the property is on its
    # way out there, but 4.2 still needs it. Testing node_tree rather than
    # use_nodes keeps the deprecation warning out of the output.
    if material.node_tree is None:
        material.use_nodes = True
    bsdf = material.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.8, 0.1, 0.05, 1.0)
    painted.data.materials.append(material)

    coloured = exporter.export([painted])
    check("one colour per object", len(coloured["colours"]) == 1,
          str(coloured["colours"]))
    check("the Principled base colour is used",
          all(close(a, b, 1e-5) for a, b in
              zip(coloured["colours"][0], [0.8, 0.1, 0.05, 1.0])),
          str(coloured["colours"][0]))
    gltf_c, _ = read_glb(coloured["glb"])
    factor = gltf_c["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    check("and it reaches the GLB as baseColorFactor",
          all(close(a, b, 1e-5) for a, b in zip(factor, coloured["colours"][0])),
          str(factor))

    bare = add_cube("Bare", 0.1, location=(1, 0, 0))
    plain = exporter.export([bare])
    check("an object with no material still gets a usable colour",
          len(plain["colours"][0]) == 4
          and all(0.0 <= v <= 1.0 for v in plain["colours"][0]),
          str(plain["colours"]))

    print("")
    print("modifiers are applied, so the glasses show what the viewport shows")
    clear_scene()
    base = add_cube("Repeated", 0.1)
    # An array of three lays the cube end to end, so the object is 300 mm
    # wide while its base mesh is still 100. Blender's own bound_box reports
    # the base mesh, which is why the size is measured from the evaluated
    # triangles instead. A mirror would have been the obvious choice and is
    # the wrong one: mirroring a cube about its own origin reproduces the
    # same cube and proves nothing.
    mod = base.modifiers.new("Array", "ARRAY")
    mod.count = 3
    bpy.context.view_layer.update()

    applied = exporter.export([base], apply_modifiers=True)
    check("the arrayed object measures three times as wide",
          close(max(applied["bbox_mm"]), 300.0, 1e-3),
          str(applied["bbox_mm"]))
    raw = exporter.export([base], apply_modifiers=False)
    check("and without modifiers it measures its base mesh",
          close(max(raw["bbox_mm"]), 100.0, 1e-3), str(raw["bbox_mm"]))

    print("")
    print("what cannot be sent is refused, with something to act on")
    clear_scene()
    bpy.ops.object.camera_add()
    bpy.ops.object.light_add()
    try:
        exporter.collect_objects(bpy.context)
        check("a scene of only cameras and lights is refused", False,
              "it did not raise")
    except exporter.ExportError:
        check("a scene of only cameras and lights is refused", True)

    hidden = add_cube("Hidden", 0.1)
    hidden.hide_set(True)
    try:
        exporter.collect_objects(bpy.context)
        check("a hidden object is not sent", False, "it did not raise")
    except exporter.ExportError:
        check("a hidden object is not sent", True)

    try:
        exporter.export([])
        check("an empty selection is refused", False, "it did not raise")
    except exporter.ExportError:
        check("an empty selection is refused", True)

    print("")
    if FAILURES:
        print("{0} failure(s): {1}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


_status = main()
sys.stdout.flush()
# Blender swallows a plain return, so the status has to be forced out.
sys.exit(_status)
