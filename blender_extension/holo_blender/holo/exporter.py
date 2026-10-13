"""Blender objects to GLB, and the true size that goes with them.

The lens is told three things about every model, and deliberately asked to
work out none of them for itself:

  bbox_mm      the true size, in millimetres
  cm_per_unit  how many Lens Studio units one unit in the file is worth
  offset_mm    where this part sits inside the send, in the lens's own axes

That division of labour was learned the hard way on the FreeCAD side of this
project. The lens used to measure the mesh it had loaded and derive the scale
from that, and the measurement is not reliable: Lens Studio's reported mesh
bounds do not agree with the glTF accessors, and the error is not a constant
that can be corrected for. The side that wrote the file knows the answer, so
the side that wrote the file says it.

Three coordinate systems are in play:

  Blender    Z up. One Blender unit is one metre times the scene's
             unit scale (Scene Properties, Units, Unit Scale).
  glTF       Y up, metres, which is what this writes.
  Lens Studio  centimetres, so one glTF unit is 100 of them.

Standard library and bpy only. Nothing to pip install into Blender's Python.
"""

from __future__ import annotations

import json
import struct

import bmesh
import bpy

COMPONENT_FLOAT = 5126
COMPONENT_UINT = 5125
TARGET_ARRAY_BUFFER = 34962
TARGET_ELEMENT_ARRAY_BUFFER = 34963

# The exporter writes glTF in metres, which the spec asks for, so one file
# unit is 1000 mm, and Lens Studio counting centimetres makes it worth 100 of
# its units. The lens is told this rather than measuring the mesh it loaded.
MM_PER_UNIT = 1000.0
CM_PER_UNIT = MM_PER_UNIT / 10.0

# Object types that can become a mesh. Empties, cameras, lights, armatures
# and speakers cannot, and quietly contribute nothing if asked.
MESHABLE = frozenset(("MESH", "CURVE", "SURFACE", "FONT", "META"))

DEFAULT_COLOUR = (0.78, 0.80, 0.84, 1.0)


class ExportError(Exception):
    """Something the user can act on, not a bug. Shown as a plain sentence."""


# ------------------------------------------------------------------ picking


def is_renderable(obj) -> bool:
    """Whether this object can contribute geometry.

    Visibility counts: an object hidden in the viewport is one the user has
    deliberately put away, and sending it anyway would mean the glasses show
    something the screen does not.
    """
    if obj is None or obj.type not in MESHABLE:
        return False
    try:
        if not obj.visible_get():
            return False
    except RuntimeError:
        # visible_get needs a view layer. Without one, headless, fall back to
        # the object's own flags rather than refusing everything.
        if obj.hide_viewport or obj.hide_render:
            return False
    return True


def collect_objects(context=None, selection=None) -> list:
    """What to send: the selection, or everything visible in the scene.

    Mirrors how the FreeCAD side behaves, because the habit is the same one:
    select the parts you care about, or select nothing and send the lot.
    """
    if selection:
        chosen = [o for o in selection if is_renderable(o)]
        if not chosen:
            raise ExportError(
                "nothing in the selection can be turned into a mesh. Select "
                "mesh, curve, surface or text objects."
            )
        return chosen

    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        raise ExportError("no scene is open")
    chosen = [o for o in scene.objects if is_renderable(o)]
    if not chosen:
        raise ExportError(
            "the scene has nothing visible to send. Unhide something, or "
            "select the objects you want."
        )
    return chosen


# ------------------------------------------------------------------ colour


def object_colour(obj):
    """The object's colour as rgba, from its first material.

    Principled BSDF first, since that is what nearly every material is and
    its Base Color is what the viewport shows. Then the material's own
    diffuse_color, which is what the solid viewport falls back to, then the
    object colour, then a neutral grey. Alpha comes along so a glass part
    arrives translucent.
    """
    material = None
    try:
        if obj.material_slots:
            material = obj.material_slots[0].material
    except (AttributeError, IndexError):
        material = None

    if material is not None:
        base = _principled_base_colour(material)
        if base is not None:
            return base
        try:
            c = material.diffuse_color
            return (float(c[0]), float(c[1]), float(c[2]),
                    float(c[3]) if len(c) > 3 else 1.0)
        except (AttributeError, TypeError):
            pass

    try:
        c = obj.color
        return (float(c[0]), float(c[1]), float(c[2]),
                float(c[3]) if len(c) > 3 else 1.0)
    except (AttributeError, TypeError):
        return DEFAULT_COLOUR


def _principled_base_colour(material):
    """Base Color from a Principled BSDF, or None.

    None rather than a guess when the socket is driven by a texture or
    another node: there is no single colour in that case, and inventing one
    would be worse than falling back to the material's own diffuse colour.
    """
    if not getattr(material, "use_nodes", False) or material.node_tree is None:
        return None
    for node in material.node_tree.nodes:
        if node.bl_idname != "ShaderNodeBsdfPrincipled":
            continue
        socket = node.inputs.get("Base Color")
        if socket is None or socket.is_linked:
            return None
        v = socket.default_value
        return (float(v[0]), float(v[1]), float(v[2]),
                float(v[3]) if len(v) > 3 else 1.0)
    return None


# ------------------------------------------------------------------- space


def unit_scale(context=None) -> float:
    """Metres per Blender unit, from the scene's own unit settings.

    A scene built at 1 unit = 1 cm reports 0.01 here, and ignoring it would
    put the model on the glasses a hundred times too big. This is the one
    number that makes true size mean anything.
    """
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return 1.0
    try:
        scale = float(scene.unit_settings.scale_length)
    except (AttributeError, TypeError, ValueError):
        return 1.0
    return scale if scale > 0 else 1.0


def to_gltf_space(x, y, z, metres_per_unit=1.0):
    """Blender Z up to glTF Y up, in metres. A -90 degree turn about X."""
    return (x * metres_per_unit, z * metres_per_unit, -y * metres_per_unit)


def _shifted(point, shift):
    return (point[0] - shift[0], point[1] - shift[1], point[2] - shift[2])


# -------------------------------------------------------------- the meshing


def evaluated_triangles(obj, depsgraph, apply_modifiers=True):
    """(world space vertices, triangles) for one object, modifiers applied.

    Everything goes through bmesh triangulation rather than
    loop_triangles, because curves, text and metaballs become meshes with
    ngons and the GLB has to be triangles. The evaluated object is used so
    what the glasses show is what the viewport shows, subdivision and all.
    """
    source = obj.evaluated_get(depsgraph) if apply_modifiers else obj
    try:
        mesh = source.to_mesh()
    except RuntimeError:
        # Curves with no geometry, empty text, and metaballs that are not
        # the basis of their family all refuse here. None of those are an
        # error worth stopping a send for.
        return [], []
    if mesh is None:
        return [], []

    try:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        matrix = obj.matrix_world
        verts = [matrix @ v.co for v in bm.verts]
        tris = [tuple(v.index for v in f.verts) for f in bm.faces
                if len(f.verts) == 3]
        bm.free()
    finally:
        source.to_mesh_clear()
    return verts, tris


def object_geometry(obj, depsgraph, metres_per_unit, shift=(0.0, 0.0, 0.0),
                    apply_modifiers=True):
    """(positions, normals, indices) in glTF space, flat shaded.

    Each triangle gets its own three vertices so edges stay hard, which is
    what a machined part should look like and what the FreeCAD side does.
    Smooth shading would need split normals per loop, and a CAD part read at
    arm's length does not benefit from it.
    """
    verts, tris = evaluated_triangles(obj, depsgraph, apply_modifiers)
    positions = []
    normals = []
    indices = []
    for tri in tris:
        a, b, c = [
            _shifted(to_gltf_space(*verts[i], metres_per_unit=metres_per_unit),
                     shift)
            for i in tri
        ]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        length = (nx * nx + ny * ny + nz * nz) ** 0.5
        if length == 0:
            continue  # a degenerate triangle contributes nothing
        normal = (nx / length, ny / length, nz / length)
        base = len(positions)
        positions.extend((a, b, c))
        normals.extend((normal, normal, normal))
        indices.extend((base, base + 1, base + 2))
    return positions, normals, indices


# ---------------------------------------------------------------- measuring


def bounding_box_mm(objs, depsgraph, metres_per_unit, apply_modifiers=True):
    """(min, max) of the evaluated geometry, in world millimetres.

    Measured from the triangles that will actually be written, not from
    Blender's `bound_box`. The two differ whenever a modifier changes the
    shape: bound_box is the object's own bounds before evaluation, so a
    subdivided or mirrored object would report the size it used to be.
    """
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    mm = metres_per_unit * 1000.0
    for obj in objs:
        verts, tris = evaluated_triangles(obj, depsgraph, apply_modifiers)
        if not tris:
            continue
        used = set()
        for tri in tris:
            used.update(tri)
        for i in used:
            v = verts[i]
            for axis in range(3):
                value = v[axis] * mm
                lo[axis] = min(lo[axis], value)
                hi[axis] = max(hi[axis], value)
    if lo[0] == float("inf"):
        raise ExportError("every object came out empty, so there is nothing to measure")
    return lo, hi


def bottom_centre_mm(lo, hi):
    """The point a model hangs from: centred in X and Y, bottom in Z.

    Blender is Z up, so the bottom is the smallest Z. Matching the FreeCAD
    side exactly matters, because the lens treats every model the same way
    whichever program sent it.
    """
    return ((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, lo[2])


# ------------------------------------------------------------------ the GLB


def _pad4(data: bytes, fill: bytes) -> bytes:
    remainder = len(data) % 4
    return data if remainder == 0 else data + fill * (4 - remainder)


def build_glb(parts) -> bytes:
    """Assemble one GLB from [(name, positions, normals, indices, colour)].

    One mesh and one material per object, so colours survive and the lens
    can still treat the whole thing as a single model.
    """
    buffer = bytearray()
    views = []
    accessors = []
    meshes = []
    materials = []
    nodes = []

    for name, positions, normals, indices, colour in parts:
        if not indices:
            continue
        pos_bytes = b"".join(struct.pack("<3f", *p) for p in positions)
        nrm_bytes = b"".join(struct.pack("<3f", *n) for n in normals)
        idx_bytes = b"".join(struct.pack("<I", i) for i in indices)

        pos_view = len(views)
        views.append({"buffer": 0, "byteOffset": len(buffer),
                      "byteLength": len(pos_bytes), "target": TARGET_ARRAY_BUFFER})
        buffer += pos_bytes
        nrm_view = len(views)
        views.append({"buffer": 0, "byteOffset": len(buffer),
                      "byteLength": len(nrm_bytes), "target": TARGET_ARRAY_BUFFER})
        buffer += nrm_bytes
        idx_view = len(views)
        views.append({"buffer": 0, "byteOffset": len(buffer),
                      "byteLength": len(idx_bytes),
                      "target": TARGET_ELEMENT_ARRAY_BUFFER})
        buffer += idx_bytes
        while len(buffer) % 4:
            buffer += b"\x00"

        pos_accessor = len(accessors)
        accessors.append({
            "bufferView": pos_view, "componentType": COMPONENT_FLOAT,
            "count": len(positions), "type": "VEC3",
            "min": [min(p[i] for p in positions) for i in range(3)],
            "max": [max(p[i] for p in positions) for i in range(3)],
        })
        nrm_accessor = len(accessors)
        accessors.append({"bufferView": nrm_view, "componentType": COMPONENT_FLOAT,
                          "count": len(normals), "type": "VEC3"})
        idx_accessor = len(accessors)
        accessors.append({"bufferView": idx_view, "componentType": COMPONENT_UINT,
                          "count": len(indices), "type": "SCALAR"})

        material = len(materials)
        materials.append({
            "name": "{0} material".format(name),
            "pbrMetallicRoughness": {
                "baseColorFactor": list(colour),
                "metallicFactor": 0.15,
                "roughnessFactor": 0.65,
            },
            "doubleSided": True,
        })
        mesh = len(meshes)
        meshes.append({"name": name, "primitives": [{
            "attributes": {"POSITION": pos_accessor, "NORMAL": nrm_accessor},
            "indices": idx_accessor, "material": material, "mode": 4,
        }]})
        nodes.append({"mesh": mesh, "name": name})

    if not nodes:
        raise ExportError("every object came out with no triangles")

    gltf = {
        "asset": {"version": "2.0", "generator": "Holo-Blender"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "buffers": [{"byteLength": len(buffer)}],
        "bufferViews": views,
        "accessors": accessors,
    }

    json_chunk = _pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bin_chunk = _pad4(bytes(buffer), b"\x00")
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    out = bytearray()
    out += struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<I4s", len(json_chunk), b"JSON")
    out += json_chunk
    out += struct.pack("<I4s", len(bin_chunk), b"BIN\x00")
    out += bin_chunk
    return bytes(out)


# ------------------------------------------------------------------- export


def export(objs, context=None, origin_mm=None, apply_modifiers=True) -> dict:
    """Export objs and report everything the lens needs.

    Returns id, glb, bbox_mm, triangles, colours, offset_mm and cm_per_unit.

    origin_mm is the point the whole send is measured from, in world
    millimetres. With it, every part is written about its own bottom centre
    and reports where that sits relative to that shared origin, which is
    what lets the lens rebuild a scene in the right shape while still
    updating one object at a time.
    """
    if not objs:
        raise ExportError("nothing to export")

    context = context or bpy.context
    depsgraph = context.evaluated_depsgraph_get()
    metres_per_unit = unit_scale(context)

    lo, hi = bounding_box_mm(objs, depsgraph, metres_per_unit, apply_modifiers)
    bbox = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    if max(bbox) <= 0:
        raise ExportError("the selection measures zero in every direction")

    centre = bottom_centre_mm(lo, hi)
    # The shift is applied in glTF space, so it is the same conversion the
    # vertices get: millimetres back to metres, Z up to Y up.
    shift = to_gltf_space(centre[0] / 1000.0, centre[1] / 1000.0,
                          centre[2] / 1000.0, metres_per_unit=1.0)

    origin = origin_mm if origin_mm is not None else centre
    # In the lens's own axes, so the lens never has to redo the Z up turn.
    offset_mm = [
        centre[0] - origin[0],
        centre[2] - origin[2],
        -(centre[1] - origin[1]),
    ]

    parts = []
    triangles = 0
    colours = []
    for obj in objs:
        positions, normals, indices = object_geometry(
            obj, depsgraph, metres_per_unit, shift, apply_modifiers)
        if not indices:
            continue
        triangles += len(indices) // 3
        colour = object_colour(obj)
        colours.append([round(c, 6) for c in colour])
        parts.append((obj.name, positions, normals, indices, colour))

    if not parts:
        raise ExportError("every object came out with no triangles")

    glb = build_glb(parts)
    name = objs[0].name if len(objs) == 1 else _scene_name(context)
    return {
        "id": name,
        "glb": glb,
        "bbox_mm": bbox,
        "triangles": triangles,
        "colours": colours,
        "offset_mm": [round(v, 6) for v in offset_mm],
        "cm_per_unit": CM_PER_UNIT,
    }


def _scene_name(context) -> str:
    scene = getattr(context, "scene", None)
    name = getattr(scene, "name", "") if scene is not None else ""
    return name or "scene"
