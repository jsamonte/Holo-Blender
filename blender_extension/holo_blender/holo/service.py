"""Server lifecycle, sending, and live mode. The part that knows about both.

Everything here runs on Blender's main thread. The server itself runs on a
daemon thread, but it only ever hands out bytes that were prepared here, so
nothing on that thread touches bpy. That rule is not negotiable: Blender's
data is not thread safe, and a server thread reading a mesh mid-edit would
take Blender down rather than raise.
"""

from __future__ import annotations

import hashlib
import os

import bpy

from . import exporter, server as server_mod, tunnel

_bridge = None
_tunnel = None
_live = False
_pending = False

# Per object, the digest of what was last sent. Live mode re-sends only what
# changed, which is what makes a scene of many objects usable.
_digests = {}

# The point every object's position is measured from. Worked out once and
# then held, deliberately.
#
# Recomputing it from the scene bounds on every send looks tidier and is
# wrong: moving one object changes the scene bounds, which changes the
# origin, which changes every other object's offset, so every object is
# re-sent. Dragging one bracket in a 21 object scene re-published all 21.
# Holding it also gives the better meaning: the origin is an anchor the
# scene sits at, and moving an object moves that object.
_origin_mm = None


# ------------------------------------------------------------------ talking


def log(message: str) -> None:
    print("Holo-Blender: {0}".format(message))


def warn(message: str) -> None:
    print("Holo-Blender: {0}".format(message))


def error(message: str) -> None:
    print("Holo-Blender: {0}".format(message))


def report(operator, level, message):
    """Say it in Blender's status bar as well as the console.

    The console is where the detail goes, but an operator that only prints
    looks to the user like a button that did nothing.
    """
    if operator is not None:
        operator.report({level}, "Holo-Blender: {0}".format(message))
    log(message)


# ------------------------------------------------------------- preferences


# The key Blender files this extension's preferences under. As an
# extension the package is something like
# "bl_ext.user_default.holo_blender.holo", and the preferences live under
# the parent, so the trailing ".holo" comes off. Taking the first component
# instead would ask for "bl_ext", which is not an add-on and never matches.
ADDON_ID = __package__.rpartition(".")[0] or __package__


def prefs():
    """This extension's preferences, or None when it is not registered.

    None rather than raising: the tests exercise the exporter and the server
    without registering the extension at all, and every caller has a
    sensible default to fall back on.
    """
    try:
        return bpy.context.preferences.addons[ADDON_ID].preferences
    except (AttributeError, KeyError):
        return None


def setting(name, fallback):
    got = prefs()
    if got is None:
        return fallback
    return getattr(got, name, fallback)


def scale_spec() -> dict:
    """The requested scale, in the shape the lens expects."""
    mode = setting("scale_mode", "true_size")
    return {
        "mode": mode,
        "factor": float(setting("scale_factor", 1.0)),
        "target_mm": float(setting("fit_target_mm", 300.0)) if mode == "fit" else None,
    }


def describe_scale() -> str:
    spec = scale_spec()
    if spec["mode"] == "fit":
        return "fit to {0:.0f} mm".format(spec["target_mm"])
    if spec["mode"] == "ratio":
        factor = spec["factor"] or 1.0
        if factor < 1:
            return "1:{0:.0f}".format(1.0 / factor)
        return "{0:.0f}:1".format(factor)
    return "1:1 true size"


# ----------------------------------------------------------------- the tunnel


def _state_path() -> str:
    """Beside Blender's other user data, so one Blender can adopt another's."""
    try:
        base = bpy.utils.user_resource("CONFIG", create=True)
    except (AttributeError, TypeError):
        base = os.path.expanduser("~")
    return os.path.join(base, "holo_blender_tunnel.json")


def tunnel_handle() -> tunnel.Tunnel:
    global _tunnel
    if _tunnel is None:
        _tunnel = tunnel.Tunnel(log=log, warn=warn, on_ready=_tunnel_ready,
                                state_path=_state_path())
    return _tunnel


def _tunnel_ready(public_base: str) -> None:
    """Point the server's model urls at the tunnel once it has a hostname."""
    if _bridge is not None:
        _bridge.public_base = public_base


def online_allowed() -> bool:
    """Blender's own Allow Online Access switch.

    Extensions that reach the internet have to respect it, and the tunnel
    plainly does. Serving on the local network does not, so only the tunnel
    is gated on this.
    """
    return bool(getattr(bpy.app, "online_access", True))


def start_tunnel() -> bool:
    if not online_allowed():
        error(
            "Blender's Allow Online Access is off, so the tunnel cannot be "
            "opened. Turn it on in Preferences, System, Network, or use the "
            "same Wi-Fi route instead."
        )
        return False
    serv = bridge()
    if not serv.running:
        start_server()
    return tunnel_handle().start(serv.port)


def stop_tunnel() -> None:
    if _tunnel is not None:
        _tunnel.stop()
    if _bridge is not None:
        _bridge.public_base = ""


def tunnel_words() -> str:
    return tunnel_handle().words if _tunnel is not None else ""


# ----------------------------------------------------------------- the server


def bridge():
    global _bridge
    if _bridge is None:
        _bridge = server_mod.Bridge(port=int(setting("port", 8766)), log=log)
    return _bridge


def start_server() -> str:
    serv = bridge()
    if serv.running:
        return serv.socket_url()
    try:
        url = serv.start()
    except OSError as e:
        error("could not listen on port {0}: {1}".format(serv.port, e))
        error(
            "Something else already has that port, very likely another "
            "Blender, or Holo-CAD, or a server left running from earlier. "
            "Close it, or change the port in the extension's preferences."
        )
        raise
    log("ready. The lens needs this address:")
    log("    {0}".format(url))
    log("    health check in a browser on the same Wi-Fi: {0}/status".format(
        serv.base_url()))
    return url


def stop_server() -> None:
    global _bridge
    if _bridge is not None:
        _bridge.stop()
        _bridge = None


def server_running() -> bool:
    return _bridge is not None and _bridge.running


# ------------------------------------------------------------------ sending


def _digest(blob: bytes, scale: dict, offset=None) -> str:
    """What "unchanged" means: everything the lens is told, not just the mesh.

    Hashing the GLB alone is not enough, twice over.

    Switching 1:1 to 1:10 leaves the bytes identical, so a scale change was
    silently dropped and pressing Send appeared to do nothing.

    And each object's mesh is written about its own bottom centre, with its
    position sent alongside, so **moving** an object leaves the bytes
    identical too. Live mode then watched you drag something across the
    scene and told the lens nothing at all.
    """
    marker = "{0}|{1}|{2}|{3}".format(
        scale.get("mode"), scale.get("factor"), scale.get("target_mm"),
        offset)
    digest = hashlib.sha1(blob)
    digest.update(marker.encode("utf-8"))
    return digest.hexdigest()


def send(objs=None, context=None, force=False) -> list:
    """Export and hand to every connected lens. Returns what was sent.

    With per object on, which is the default, each object becomes its own
    model with its own id, and one whose GLB is byte for byte what went last
    time is skipped. That is what makes live mode usable on a scene of many
    objects: moving one bracket re-sends one bracket.
    """
    context = context or bpy.context
    serv = bridge()
    if not serv.running:
        start_server()

    chosen = objs or exporter.collect_objects(context)
    scale = scale_spec()
    per_object = bool(setting("per_object", True))
    apply_modifiers = bool(setting("apply_modifiers", True))
    groups = [[obj] for obj in chosen] if per_object else [chosen]

    origin_mm = _shared_origin(context, chosen, apply_modifiers)

    sent = []
    unchanged = 0
    for group in groups:
        result = exporter.export(group, context=context, origin_mm=origin_mm,
                                 apply_modifiers=apply_modifiers)
        digest = _digest(result["glb"], scale, result.get("offset_mm"))
        slug = server_mod.slugify(result["id"])
        if not force and _digests.get(slug) == digest:
            unchanged += 1
            continue
        metadata = serv.publish(
            result["id"],
            result["glb"],
            result["bbox_mm"],
            scale=scale,
            triangles=result["triangles"],
            colours=result.get("colours"),
            offset_mm=result.get("offset_mm"),
            cm_per_unit=result.get("cm_per_unit"),
        )
        _digests[metadata["id"]] = digest
        sent.append(metadata)
        bbox = result["bbox_mm"]
        log("sent {0} at {1}, {2:.1f} x {3:.1f} x {4:.1f} mm, {5} triangles".format(
            metadata["id"], describe_scale(), bbox[0], bbox[1], bbox[2],
            result["triangles"]))

    if per_object:
        _remove_vanished(serv, chosen)

    if unchanged:
        log("{0} unchanged, not re-sent".format(unchanged))
    if sent and serv.lens_count == 0:
        warn(
            "no lens is connected yet, so nothing saw that. The lens will "
            "catch up as soon as it connects."
        )
    return sent


def _shared_origin(context, chosen, apply_modifiers):
    """The point the whole scene is measured from, worked out once.

    Cleared by forget_sent(), so Send Everything Again re-anchors on what is
    in the scene now. Ordinary sends keep the anchor they had.
    """
    global _origin_mm
    if _origin_mm is None:
        depsgraph = context.evaluated_depsgraph_get()
        lo, hi = exporter.bounding_box_mm(
            chosen, depsgraph, exporter.unit_scale(context), apply_modifiers)
        _origin_mm = exporter.bottom_centre_mm(lo, hi)
    return _origin_mm


def _remove_vanished(serv, chosen) -> None:
    """Tell the lens about anything no longer being sent.

    An object deleted or hidden in Blender would otherwise hang in the air,
    because nothing else tells the lens it went away.
    """
    live = set(server_mod.slugify(obj.name) for obj in chosen)
    for model_id in list(_digests):
        if model_id in live:
            continue
        if serv.remove(model_id):
            log("removed {0}, no longer in the scene".format(model_id))
        _digests.pop(model_id, None)


def forget_sent() -> None:
    """Drop the change tracking, so the next send pushes everything again.

    The anchor goes too, so the scene is re-measured and re-centred rather
    than keeping an origin from whatever was in it before.
    """
    global _origin_mm
    _digests.clear()
    _origin_mm = None


def send_selected(context=None) -> list:
    """What the button calls: the selection, or everything visible."""
    context = context or bpy.context
    selection = [o for o in getattr(context, "selected_objects", [])
                 if exporter.is_renderable(o)]
    objs = exporter.collect_objects(context, selection=selection)
    # Pressing the button always sends. Skipping unchanged objects is for
    # live mode, where it is the whole point.
    return send(objs, context=context, force=True)


# ---------------------------------------------------------------- live mode


def live_enabled() -> bool:
    return _live


def _on_depsgraph_update(scene, depsgraph=None) -> None:
    """Queue a send. Runs on every change, so it must stay cheap.

    The work itself is deliberately not done here. This handler fires during
    a depsgraph update, and exporting from inside one risks re-entering the
    evaluation that is still running. A timer picks it up afterwards.
    """
    global _pending
    if not _live or _pending:
        return
    _pending = True
    delay = max(0.05, float(setting("live_debounce_ms", 500)) / 1000.0)
    bpy.app.timers.register(_flush, first_interval=delay)


def _flush():
    """Send what changed, once the scene has settled."""
    global _pending
    _pending = False
    if not _live:
        return None
    try:
        send()
    except exporter.ExportError as e:
        warn(str(e))
    except Exception as e:
        error("live send failed: {0}: {1}".format(type(e).__name__, e))
    return None  # do not repeat


def set_live(enabled: bool) -> bool:
    """Turn live mode on or off. Returns what it ended up as."""
    global _live
    handlers = bpy.app.handlers.depsgraph_update_post
    if enabled:
        if not server_running():
            try:
                start_server()
            except OSError:
                return False
        if _on_depsgraph_update not in handlers:
            handlers.append(_on_depsgraph_update)
        _live = True
        log("live mode on, re-sending {0} ms after you stop changing things".format(
            int(setting("live_debounce_ms", 500))))
    else:
        if _on_depsgraph_update in handlers:
            handlers.remove(_on_depsgraph_update)
        _live = False
        log("live mode off")
    return _live


# ----------------------------------------------------------------- shutdown


def shutdown() -> None:
    """Called when the extension is disabled or Blender quits."""
    set_live(False)
    stop_server()
    # Deliberately not stop_tunnel(): the hostname is what keeps the words
    # already typed into the lens valid, so closing Blender leaves the
    # tunnel up and the next session adopts it. The button ends it.
    if _tunnel is not None:
        _tunnel.detach()
