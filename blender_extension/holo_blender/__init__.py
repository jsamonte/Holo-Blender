"""Holo-Blender: see your Blender scene on Snapchat Spectacles at true size.

A Blender extension, so there is no bl_info here: the metadata lives in
blender_manifest.toml, which is what Blender 4.2 and later read.

The extension is also the server. It serves the models from inside Blender
using only the bundled Python, so there is nothing to host, no separate
program and nothing to pip install.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import AddonPreferences

from .holo import ops, service, ui


class HoloBlenderPreferences(AddonPreferences):
    bl_idname = __package__

    port: IntProperty(
        name="Port",
        description=(
            "Port the glasses connect to. 8766 by default rather than 8765, "
            "so Holo-Blender and Holo-CAD can run side by side"
        ),
        default=8766,
        min=1024,
        max=65535,
    )
    auto_start: BoolProperty(
        name="Start serving when Blender opens",
        description="Start the server as soon as the extension is enabled",
        default=True,
    )
    scale_mode: EnumProperty(
        name="Scale",
        description="How big the model appears on the glasses",
        items=[
            ("true_size", "1:1 True Size", "Exactly the size it is in Blender"),
            ("ratio", "Ratio", "A fixed fraction of true size, for example 1:10"),
            ("fit", "Fit", "Scaled so the largest dimension is a size you choose"),
        ],
        default="true_size",
    )
    scale_factor: FloatProperty(
        name="Ratio",
        description="0.1 means 1:10",
        default=1.0,
        min=0.0001,
        max=1000.0,
    )
    fit_target_mm: FloatProperty(
        name="Fit to (mm)",
        description="Largest dimension, in millimetres, when the mode is Fit",
        default=300.0,
        min=1.0,
        max=100000.0,
    )
    per_object: BoolProperty(
        name="One model per object",
        description=(
            "Send each object as its own model, so live mode re-sends only "
            "what changed. Off sends the whole scene as a single model"
        ),
        default=True,
    )
    apply_modifiers: BoolProperty(
        name="Apply modifiers",
        description=(
            "Send what the viewport shows, subdivision and mirrors included. "
            "Off sends the base mesh, which is faster on a heavy scene"
        ),
        default=True,
    )
    live_debounce_ms: IntProperty(
        name="Live delay (ms)",
        description="How long to wait after you stop changing things before re-sending",
        default=500,
        min=50,
        max=5000,
    )

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.prop(self, "port")
        col.prop(self, "auto_start")
        col.prop(self, "live_debounce_ms")
        col.separator()
        col.prop(self, "scale_mode")
        if self.scale_mode == "ratio":
            col.prop(self, "scale_factor")
        elif self.scale_mode == "fit":
            col.prop(self, "fit_target_mm")
        col.separator()
        col.prop(self, "per_object")
        col.prop(self, "apply_modifiers")


def _start_when_ready():
    """Start serving once Blender has finished opening.

    Registration happens while Blender is still starting up, and binding a
    socket from inside register() has nowhere to report a failure to. A
    one-shot timer runs after the UI exists, so a port clash becomes a line
    in the console instead of a broken startup.
    """
    try:
        if service.setting("auto_start", True):
            service.start_server()
    except OSError:
        pass  # start_server has already explained it
    except Exception as e:
        service.error("could not start on load: {0}: {1}".format(
            type(e).__name__, e))
    return None


def register():
    bpy.utils.register_class(HoloBlenderPreferences)
    ops.register()
    ui.register()
    bpy.app.timers.register(_start_when_ready, first_interval=0.5)


def unregister():
    service.shutdown()
    ui.unregister()
    ops.unregister()
    bpy.utils.unregister_class(HoloBlenderPreferences)
