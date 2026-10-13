"""The operators behind the buttons.

Each one is deliberately thin: read the selection, call into service, and
turn any failure into a sentence in the status bar rather than a traceback.
A traceback in Blender's console is how a user concludes the extension is
broken, when usually they just need to unhide something or free a port.
"""

from __future__ import annotations

import bpy
from bpy.types import Operator

from . import exporter, service


class HOLO_OT_send(Operator):
    """Send the selected objects to the glasses, or everything visible"""

    bl_idname = "holo.send"
    bl_label = "Send to Spectacles"
    bl_description = (
        "Send the selected objects to the glasses. With nothing selected, "
        "sends every visible object in the scene"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            sent = service.send_selected(context)
        except exporter.ExportError as e:
            service.report(self, "WARNING", str(e))
            return {"CANCELLED"}
        except OSError as e:
            service.report(self, "ERROR", "could not start the server: {0}".format(e))
            return {"CANCELLED"}
        except Exception as e:
            service.report(self, "ERROR", "send failed: {0}: {1}".format(
                type(e).__name__, e))
            return {"CANCELLED"}

        if not sent:
            service.report(self, "INFO", "nothing had changed, so nothing was sent")
            return {"FINISHED"}
        total = sum(m["triangles"] for m in sent)
        service.report(self, "INFO", "sent {0} object(s), {1} triangles, at {2}".format(
            len(sent), total, service.describe_scale()))
        return {"FINISHED"}


class HOLO_OT_toggle_live(Operator):
    """Re-send automatically whenever the scene changes"""

    bl_idname = "holo.toggle_live"
    bl_label = "Live Mode"
    bl_description = (
        "Re-send automatically whenever the scene changes, so the glasses "
        "follow your edits"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        wanted = not service.live_enabled()
        reached = service.set_live(wanted)
        if wanted and not reached:
            service.report(self, "ERROR", "could not start the server, so live mode is off")
            return {"CANCELLED"}
        if wanted:
            # One send straight away, so the glasses are not left showing
            # whatever was there before live mode was switched on.
            try:
                service.send_selected(context)
            except exporter.ExportError as e:
                service.report(self, "WARNING", str(e))
        service.report(self, "INFO", "live mode {0}".format("on" if reached else "off"))
        return {"FINISHED"}


class HOLO_OT_share(Operator):
    """Open a secure tunnel so a published lens can reach this machine"""

    bl_idname = "holo.share"
    bl_label = "Share Over the Internet"
    bl_description = (
        "Open a secure tunnel so the glasses can reach this machine from "
        "anywhere. Needs cloudflared installed. The words to type into the "
        "lens appear in the panel and the console"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        handle = service.tunnel_handle()

        # Pressing again while it is still coming up used to stop it. A
        # quick tunnel takes ten to twenty seconds, which is long enough
        # that pressing again is the natural thing to do, so that press
        # reports progress instead of undoing the work.
        if handle.starting:
            service.report(self, "INFO",
                           "still opening the tunnel, this takes up to twenty seconds")
            return {"FINISHED"}

        if handle.running or handle.hostname:
            service.stop_tunnel()
            service.report(self, "INFO", "stopped sharing")
            return {"FINISHED"}

        if not service.start_tunnel():
            service.report(self, "ERROR",
                           handle.error or "could not start the tunnel, see the console")
            return {"CANCELLED"}
        service.report(self, "INFO",
                       "opening a tunnel, the words appear here in a moment")
        return {"FINISHED"}


class HOLO_OT_start_server(Operator):
    """Start serving, without sending anything yet"""

    bl_idname = "holo.start_server"
    bl_label = "Start Server"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            url = service.start_server()
        except OSError as e:
            service.report(self, "ERROR", "could not listen: {0}".format(e))
            return {"CANCELLED"}
        service.report(self, "INFO", url)
        return {"FINISHED"}


class HOLO_OT_stop_server(Operator):
    """Stop serving and disconnect the glasses"""

    bl_idname = "holo.stop_server"
    bl_label = "Stop Server"
    bl_options = {"REGISTER"}

    def execute(self, context):
        service.set_live(False)
        service.stop_server()
        service.report(self, "INFO", "server stopped")
        return {"FINISHED"}


class HOLO_OT_resend_all(Operator):
    """Forget what was sent, so the next send pushes everything again"""

    bl_idname = "holo.resend_all"
    bl_label = "Send Everything Again"
    bl_options = {"REGISTER"}

    def execute(self, context):
        service.forget_sent()
        try:
            sent = service.send_selected(context)
        except exporter.ExportError as e:
            service.report(self, "WARNING", str(e))
            return {"CANCELLED"}
        service.report(self, "INFO", "re-sent {0} object(s)".format(len(sent)))
        return {"FINISHED"}


CLASSES = (
    HOLO_OT_send,
    HOLO_OT_toggle_live,
    HOLO_OT_share,
    HOLO_OT_start_server,
    HOLO_OT_stop_server,
    HOLO_OT_resend_all,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
