"""The whole chain, inside a headless Blender.

    "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" ^
        --background --factory-startup --python tools\\test_end_to_end.py

A Blender object goes through service.send(), out to a WebSocket client
pretending to be the lens, and the GLB it is told about is downloaded over
HTTP and checked. That is every piece the glasses depend on except the
glasses themselves.

The extension is not registered here, so preferences do not exist and
service falls back to its defaults. That is worth exercising rather than
avoiding: it is the same path a user hits in the moment between enabling
the extension and Blender finishing its startup.
"""

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import urllib.request

import bpy

try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "blender_extension", "holo_blender"))

from holo import exporter, server as server_mod, service  # noqa: E402

# Not the default port: a developer running this very likely has the real
# thing serving on 8766 already, and the clash would read as a broken test.
TEST_PORT = 8791

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    {0}".format(name))
    else:
        print("  FAIL  {0}  {1}".format(name, detail))
        FAILURES.append(name)


class Lens(object):
    """A WebSocket client that behaves the way the lens does."""

    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.rest = b""
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            "GET /ws HTTP/1.1\r\n"
            "Host: {0}:{1}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: {2}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).format(host, port, key).encode())
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError("server closed during the handshake")
            buffer += chunk
        head, self.rest = buffer.split(b"\r\n\r\n", 1)
        self.headers = head.decode("latin-1")
        self.accept = base64.b64encode(
            hashlib.sha1((key + server_mod.WS_GUID).encode()).digest()).decode()

    def frame(self):
        def need(count):
            while len(self.rest) < count:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise AssertionError("server closed mid frame")
                self.rest += chunk

        need(2)
        length = self.rest[1] & 0x7F
        offset = 2
        if length == 126:
            need(4)
            length = struct.unpack(">H", self.rest[2:4])[0]
            offset = 4
        elif length == 127:
            need(10)
            length = struct.unpack(">Q", self.rest[2:10])[0]
            offset = 10
        need(offset + length)
        payload = self.rest[offset:offset + length]
        self.rest = self.rest[offset + length:]
        return payload

    def message(self):
        return json.loads(self.frame().decode("utf-8"))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def add_cube(name, size_m, location=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=size_m, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # Force the port, since there are no preferences to read it from.
    serv = server_mod.Bridge(port=TEST_PORT, log=lambda m: None)
    service._bridge = serv
    service.forget_sent()

    print("")
    print("the server starts and a lens connects")
    url = service.start_server()
    check("start_server returns a ws url", url.startswith("ws://"), url)
    check("reports itself running", service.server_running())

    lens = Lens("127.0.0.1", TEST_PORT)
    check("handshake accepted", "101" in lens.headers.splitlines()[0], lens.headers[:60])
    check("challenge accepted", lens.accept in lens.headers, lens.headers[:200])
    hello = lens.message()
    check("greeted with hello", hello.get("type") == "hello", str(hello)[:120])

    print("")
    print("one object, sent the way the button sends it")
    bracket = add_cube("Bracket", 0.1)
    sent = service.send([bracket], force=True)
    check("one model went", len(sent) == 1, str(len(sent)))
    meta = sent[0]
    check("it is named after the object", meta["id"] == "Bracket", meta["id"])
    check("true size reported in mm",
          all(abs(meta["bbox_mm"][k] - 100.0) < 1e-3 for k in ("x", "y", "z")),
          str(meta["bbox_mm"]))
    check("the units are stated outright", meta["cm_per_unit"] == 100.0,
          str(meta["cm_per_unit"]))
    check("a colour went with it", len(meta["colours"]) == 1, str(meta["colours"]))
    check("a lone send sits at the origin", meta["offset_mm"] == [0.0, 0.0, 0.0],
          str(meta["offset_mm"]))

    update = lens.message()
    check("the lens was told", update.get("type") == "model_update")
    check("same version as the publish", update["version"] == meta["version"])
    check("and told the units", update["cm_per_unit"] == 100.0)
    check("and told the colour", update["colours"] == meta["colours"])

    with urllib.request.urlopen(
            update["url"].replace(serv.host_ip, "127.0.0.1"), timeout=10) as r:
        glb = r.read()
    check("GLB fetched over http", glb[:4] == b"glTF", str(glb[:8]))
    check("byte count matches the announcement", len(glb) == update["bytes"],
          "{0} vs {1}".format(len(glb), update["bytes"]))

    print("")
    print("per object: one model each, and only what changed")
    plate = add_cube("Plate", 0.05, location=(0.3, 0, 0))
    service.forget_sent()
    sent = service.send(force=True)
    ids = sorted(m["id"] for m in sent)
    check("one model per object", ids == ["Bracket", "Plate"], str(ids))
    offsets = {m["id"]: m["offset_mm"] for m in sent}
    check("each one says where it belongs",
          offsets["Bracket"] != offsets["Plate"], str(offsets))
    check("and they are measured from a shared origin",
          any(any(v != 0.0 for v in o) for o in offsets.values()), str(offsets))

    seen = {}
    for _ in range(2):
        message = lens.message()
        seen[message["id"]] = message
    check("both announced", sorted(seen) == ["Bracket", "Plate"], str(sorted(seen)))

    again = service.send()
    check("nothing changed means nothing sent", again == [], str(again))

    plate.location = (0.4, 0, 0)
    bpy.context.view_layer.update()
    moved = service.send()
    check("only the moved object is re-sent",
          [m["id"] for m in moved] == ["Plate"], str([m["id"] for m in moved]))
    check("and that is what the lens hears", lens.message()["id"] == "Plate")

    print("")
    print("an object that goes away is taken off the glasses")
    bpy.data.objects.remove(plate, do_unlink=True)
    service.send()
    removal = lens.message()
    check("removal is announced", removal.get("type") == "model_remove",
          str(removal)[:120])
    check("naming the object that went", removal.get("id") == "Plate",
          str(removal))

    print("")
    print("a lens that connects late is caught up")
    late = Lens("127.0.0.1", TEST_PORT)
    late.message()  # hello
    caught = late.message()
    check("late lens is caught up", caught.get("type") == "model_update",
          str(caught)[:120])
    check("on the model that is still there", caught.get("id") == "Bracket",
          str(caught.get("id")))
    late.close()

    print("")
    print("live mode watches the scene")
    # The timer that does the sending cannot fire here: background Blender
    # runs no event loop, so bpy.app.timers never come due. Everything
    # either side of it is real, and _flush is called directly to stand in
    # for the tick.
    service.forget_sent()
    handlers = bpy.app.handlers.depsgraph_update_post
    before = len(handlers)
    check("live mode turns on", service.set_live(True) is True)
    check("and hooks the depsgraph", len(handlers) == before + 1,
          "{0} -> {1}".format(before, len(handlers)))

    service._pending = False
    watched = add_cube("Watched", 0.08, location=(0, 0.5, 0))
    bpy.context.view_layer.update()
    check("a change to the scene queues a send", service._pending is True)

    service._flush()
    check("and the tick clears the queue", service._pending is False)
    # Drain whatever the flush pushed, since live mode decides for itself
    # how many objects changed.
    names = set()
    try:
        while True:
            lens.sock.settimeout(1.0)
            names.add(lens.message()["id"])
    except (OSError, AssertionError):
        pass
    lens.sock.settimeout(10)
    check("the lens heard about the new object", "Watched" in names, str(names))

    check("live mode turns off", service.set_live(False) is False)
    check("and unhooks the depsgraph", len(handlers) == before,
          "{0} -> {1}".format(before, len(handlers)))
    bpy.data.objects.remove(watched, do_unlink=True)

    print("")
    print("failure is reported, not raised at the user")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        service.send()
        check("an empty scene refuses to send", False, "it did not raise")
    except exporter.ExportError:
        check("an empty scene refuses to send", True)

    print("")
    print("shutdown leaves nothing behind")
    lens.close()
    service.stop_server()
    check("server stopped", not service.server_running())
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
