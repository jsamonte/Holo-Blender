"""The buttons, through the real install path, inside a headless Blender.

    "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" ^
        --background --factory-startup --python tools\\test_operators.py

The other suites import the modules directly, which skips the two things
most likely to be quietly broken in a Blender extension: whether it
registers at all, and whether its preferences can be found once it has. As
an extension the package is bl_ext.user_default.holo_blender, and a
submodule looking its preferences up under the wrong key finds nothing and
silently falls back to defaults, so every setting is ignored and the only
symptom is the port being wrong.

Needs the extension linked in, which install.ps1 does. It is skipped,
passing, when it is not, because that is a setup difference rather than a
failure.
"""

import os
import socket
import sys

import bpy

try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

MODULE = "bl_ext.user_default.holo_blender"
TEST_PORT = 8792

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    {0}".format(name))
    else:
        print("  FAIL  {0}  {1}".format(name, detail))
        FAILURES.append(name)


def main():
    print("")
    print("the extension registers through the real install path")
    try:
        bpy.ops.preferences.addon_enable(module=MODULE)
    except Exception as e:
        print("  the extension is not installed, so there is nothing to drive")
        print("  run install.ps1 first. ({0}: {1})".format(type(e).__name__, e))
        print("")
        print("all checks passed")
        return 0

    check("it enabled", MODULE in bpy.context.preferences.addons)
    check("every operator registered",
          all(hasattr(bpy.ops.holo, name) for name in
              ("send", "toggle_live", "share", "start_server",
               "stop_server", "resend_all")),
          str(dir(bpy.ops.holo)))
    panels = [c.__name__ for c in bpy.types.Panel.__subclasses__()
              if c.__name__.startswith("HOLO")]
    check("every panel registered", len(panels) == 3, str(panels))

    from bl_ext.user_default.holo_blender.holo import service  # noqa: E402

    print("")
    print("preferences are found under the key an extension actually uses")
    check("the addon id is the package, not its first component",
          service.ADDON_ID == MODULE, service.ADDON_ID)
    prefs = service.prefs()
    check("and the preferences come back", prefs is not None)
    prefs.port = TEST_PORT
    check("a setting written is a setting read",
          service.setting("port", 0) == TEST_PORT,
          str(service.setting("port", 0)))

    print("")
    print("the buttons do what they say")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # read_factory_settings resets preferences too, so set the port again.
    bpy.ops.preferences.addon_enable(module=MODULE)
    service.prefs().port = TEST_PORT
    service.stop_server()
    service.forget_sent()

    check("Start Server reports success",
          bpy.ops.holo.start_server() == {"FINISHED"})
    check("and it is listening on the port from preferences",
          service.bridge().port == TEST_PORT, str(service.bridge().port))

    bpy.ops.mesh.primitive_cube_add(size=0.1)
    bpy.context.active_object.name = "Widget"
    check("Send reports success", bpy.ops.holo.send() == {"FINISHED"})
    # latest() hands back a list of metadata, not a mapping by id.
    served = [m["id"] for m in service.bridge().store.latest()]
    check("and the model is being served", "Widget" in served, str(served))

    check("Live Mode turns on", bpy.ops.holo.toggle_live() == {"FINISHED"})
    check("and the extension agrees it is on", service.live_enabled() is True)
    check("Live Mode turns off again", bpy.ops.holo.toggle_live() == {"FINISHED"})
    check("and the extension agrees it is off", service.live_enabled() is False)

    check("Send Everything Again reports success",
          bpy.ops.holo.resend_all() == {"FINISHED"})

    print("")
    print("an empty scene is refused, not crashed on")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=MODULE)
    service.prefs().port = TEST_PORT
    check("Send on an empty scene is cancelled, not an exception",
          bpy.ops.holo.send() == {"CANCELLED"})

    print("")
    print("disabling the extension puts the port back")
    bpy.ops.preferences.addon_disable(module=MODULE)
    check("the extension is gone", MODULE not in bpy.context.preferences.addons)
    try:
        probe = socket.create_connection(("127.0.0.1", TEST_PORT), timeout=1)
        probe.close()
        check("port released", False, "something is still listening")
    except OSError:
        check("port released", True)

    print("")
    if FAILURES:
        print("{0} failure(s): {1}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


_status = main()
sys.stdout.flush()
sys.exit(_status)
