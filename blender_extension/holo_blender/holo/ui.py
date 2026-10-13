"""The panel in the 3D viewport sidebar, under a Holo tab.

Everything the extension does is reachable from here, and the panel is also
where the two things you cannot guess are shown: the address the lens needs
on the local network, and the four words it needs through a tunnel. Printing
those only to the console would mean reading the console to use the product.
"""

from __future__ import annotations

import bpy
from bpy.types import Panel

from . import service


class HOLO_PT_main(Panel):
    bl_label = "Holo-Blender"
    bl_idname = "HOLO_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Holo"

    def draw(self, context):
        layout = self.layout
        running = service.server_running()

        send = layout.column()
        send.scale_y = 1.4
        send.operator("holo.send", icon="EXPORT")

        row = layout.row(align=True)
        row.operator(
            "holo.toggle_live",
            icon="REC" if service.live_enabled() else "PLAY",
            text="Live Mode: On" if service.live_enabled() else "Live Mode: Off",
            depress=service.live_enabled(),
        )
        layout.operator("holo.resend_all", icon="FILE_REFRESH")

        box = layout.box()
        box.label(text="Server", icon="WORLD")
        if running:
            serv = service.bridge()
            box.label(text=serv.socket_url())
            lenses = serv.lens_count
            box.label(
                text="{0} lens{1} connected".format(lenses, "" if lenses == 1 else "es"),
                icon="CHECKMARK" if lenses else "ERROR",
            )
            box.operator("holo.stop_server", icon="PAUSE")
        else:
            box.label(text="not serving yet", icon="ERROR")
            box.operator("holo.start_server", icon="PLAY")


class HOLO_PT_share(Panel):
    bl_label = "Share Over the Internet"
    bl_idname = "HOLO_PT_share"
    bl_parent_id = "HOLO_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Holo"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        handle = service.tunnel_handle()

        if not service.online_allowed():
            col = layout.column()
            col.label(text="Allow Online Access is off", icon="ERROR")
            col.label(text="Preferences, System, Network")
            return

        if handle.starting:
            layout.label(text="opening, up to twenty seconds", icon="SORTTIME")
            layout.operator("holo.share", text="Opening...")
            return

        if handle.words:
            col = layout.column(align=True)
            col.label(text="Type these words into the lens:", icon="CHECKMARK")
            # Selectable, so the words can be copied rather than squinted at.
            col.prop(context.scene, "holo_words_readonly", text="")
            layout.operator("holo.share", text="Stop Sharing", icon="CANCEL")
            return

        layout.label(text="Needs cloudflared installed")
        layout.operator("holo.share", icon="URL")
        if handle.error:
            layout.label(text=handle.error[:60], icon="ERROR")


class HOLO_PT_scale(Panel):
    bl_label = "Scale"
    bl_idname = "HOLO_PT_scale"
    bl_parent_id = "HOLO_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Holo"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        prefs = service.prefs()
        if prefs is None:
            layout.label(text="preferences unavailable", icon="ERROR")
            return
        layout.prop(prefs, "scale_mode", text="")
        if prefs.scale_mode == "ratio":
            layout.prop(prefs, "scale_factor")
        elif prefs.scale_mode == "fit":
            layout.prop(prefs, "fit_target_mm")
        layout.label(text=service.describe_scale())

        col = layout.column(align=True)
        col.prop(prefs, "per_object")
        col.prop(prefs, "apply_modifiers")

        # The one number that decides whether true size means anything.
        unit = context.scene.unit_settings
        layout.label(text="Scene unit scale: {0:g} m per unit".format(
            unit.scale_length))


CLASSES = (HOLO_PT_main, HOLO_PT_share, HOLO_PT_scale)


def _words_get(self):
    return service.tunnel_words()


def _words_set(self, value):
    # Read only in practice. A settable property is the only way to get a
    # text field the words can be selected and copied out of.
    return None


def register():
    bpy.types.Scene.holo_words_readonly = bpy.props.StringProperty(
        name="Tunnel words",
        description="The words to type into the lens",
        get=_words_get,
        set=_words_set,
    )
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.holo_words_readonly
