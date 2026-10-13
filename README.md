# Holo-Blender

See your Blender scene floating in front of you at its real size, on Snapchat
Spectacles, and watch it update while you model.

One inch in Blender is one inch on the glasses. The extension reports each
object's true size and the exact units it wrote, so a 127 mm cube draws
127 mm across and you can hold a ruler up to it. A scene of many objects
keeps its shape: each one arrives in its own colour, in the right place
relative to the others.

**The extension is the server.** It serves the models from inside Blender
using only the bundled Python, so there is nothing to host, no separate
program, no virtual environment and nothing to pip install. Your scene never
leaves your machine unless you open a tunnel yourself.

```
Blender + the Holo-Blender extension              your Spectacles
  evaluate, triangulate, write GLB                connect, listen
  serve it from Blender itself   <------------>   download, place, show at 1:1
```

Two downloads: this extension and the lens in this repository. Plus
cloudflared if you take the tunnel route below, which is one `winget install`
and is worth it.

This is the Blender counterpart of
[Holo-CAD](https://github.com/jsamonte/Holo-CAD), which does the same thing
from FreeCAD. They speak the same protocol to the same lens, so if you have
both you only need one lens on the glasses.

## Before you start

| You need | Notes |
| --- | --- |
| **Blender 4.2** or newer | Where your scene lives. The extension runs inside it. |
| **Lens Studio 5.15.4** | Free, from Snap. How the lens gets onto your glasses. |
| **Spectacles** (2024) | Paired with Lens Studio already. |
| **cloudflared** | Only for the tunnel route below. Free, no account. |

Developed against Blender 5.1 on Windows. The extension itself is plain
Python with no Windows-specific code, but `install.ps1` is PowerShell.

## Install

### 1. The extension

Build a package and install it the normal way:

```powershell
.\install.ps1 -Build
```

That writes `dist\holo_blender-0.1.0.zip`. In Blender, **Edit, Preferences,
Get Extensions**, the dropdown at the top right, **Install from Disk**, and
pick that file.

If you are going to edit the code, link it instead so your edits take effect
on the next Blender restart with nothing to copy:

```powershell
.\install.ps1
```

That makes a directory junction from Blender's user extensions folder to
this repository, which needs no administrator rights. It asks Blender where
that folder is rather than guessing the version.
`.\install.ps1 -Uninstall` removes the link.

Either way, enable it in **Edit, Preferences, Add-ons**, search for
**Holo-Blender**, and tick it. A **Holo** tab then appears in the 3D
viewport sidebar, which is the **N** key.

### 2. The lens

Open `Spectacles/Holo-Blender` in Lens Studio, then send it to your glasses
with Lens Studio's own **Send to Spectacles** button. The scene is already
wired, so there is nothing to assemble.

## Pick a route: same Wi-Fi, or a tunnel

The glasses have to reach Blender. There are two ways, and the difference is
one address and one checkbox.

| | Same Wi-Fi | Tunnel |
| --- | --- | --- |
| Address | `ws://<your machine>:8766/ws` | `wss://<four words>.trycloudflare.com/ws` |
| Experimental APIs | **on** | **off** |
| Extra install | none | cloudflared |
| Firewall rule | yes | no |
| Works on a guest or hotel network | often not | yes |
| Can be published to Lens Explorer | no | yes |

**Start with the tunnel.** It is less setup in practice, because it skips
both the firewall rule and the networks that stop devices reaching each
other. Experimental APIs lives in Lens Studio under Project Settings.

### The tunnel route

Install cloudflared once:

```powershell
winget install Cloudflare.cloudflared
```

Blender gates internet access behind its own switch, so turn on **Allow
Online Access** in **Edit, Preferences, System, Network**. The extension
refuses to open a tunnel without it, and says so rather than failing
quietly. Serving on your own network does not need it.

Then open the **Holo** tab, **Share Over the Internet**, and press the
button. It takes up to twenty seconds, after which the panel shows four
words and so does the console:

```
Holo-Blender: TUNNEL READY. Type these words into the lens:
    mount-playlist-yarn-orientation
```

Type those four words into the lens. The `.trycloudflare.com` part is
already built in, so the words are all you type. On the glasses the lens
prompts for them and remembers them afterwards.

Pressing the button again closes the tunnel. Closing Blender leaves it open
on purpose, so the words you typed keep working into the next session.

One wrinkle worth knowing: **the words change every time the tunnel
restarts.** A quick tunnel gets a random name. While you leave it running
the words stay good, but after a reboot you will be typing new ones.

### The same Wi-Fi route

The **Holo** panel shows the address when the server is running:

```
ws://192.168.1.56:8766/ws
```

Put that into the lens's `BridgeClient` `bridgeUrl` input in Lens Studio,
and turn **Experimental APIs** on in Project Settings. That address changes
when your machine moves between networks, so read it off the panel rather
than writing it down.

`127.0.0.1` can never work here. The glasses are a separate device and need
your machine's address on the network.

You also need to let the glasses through Windows Firewall, once, in an
**elevated** PowerShell. Check which profile your Wi-Fi is on first, because
a rule on the wrong profile does nothing at all:

```powershell
Get-NetConnectionProfile | Select-Object InterfaceAlias, NetworkCategory
```

```powershell
New-NetFirewallRule -DisplayName "Holo-Blender 8766" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 8766 -Profile Public
```

Swap `Public` for whatever that first command reported.

To check the network before involving the glasses at all, open
`http://<your machine>:8766/status` in a phone browser on the same Wi-Fi.
JSON back means the path is clear.

### Why 8766 and not 8765

Holo-CAD serves on 8765. Holo-Blender defaults to 8766 so the two can run at
once without fighting over the port. Either can be changed in the
extension's preferences.

## Use it

1. Open your scene in Blender.
2. Select the objects you want. **With nothing selected it sends every
   visible object in the scene.**
3. In the **Holo** tab, press **Send to Spectacles**.
4. Look through the glasses.

The console names what went, with its real size:

```
Holo-Blender: sent Bracket at 1:1 true size, 120.0 x 40.0 x 15.0 mm, 1534 triangles
```

**Live Mode** re-sends whenever the scene changes, half a second after you
stop, so the model on the glasses follows your work. Each object is tracked
on its own: moving one bracket re-sends that bracket, not the whole scene,
and deleting or hiding one removes it from the glasses.

**Send Everything Again** forgets what was sent and re-anchors the scene,
which is what to press if things have drifted.

What gets sent is what the viewport shows: modifiers are applied, so
subdivision, mirrors and arrays all arrive. Turn **Apply modifiers** off in
preferences to send base meshes instead, which is faster on a heavy scene.

### On the glasses

- **Grab it** with one hand to move and turn it.
- **Pinch with both hands** and pull apart or together to resize, from 0.05x
  to 20x.
- The size label under the model updates as you scale. **Tap the label** to
  snap back to true 1:1, for that one object.
- The wireframe box and its measurements **appear only when your hand is
  near enough to pinch the object**, so a scene is not permanently inside a
  cage of lines.
- Both panels can be **dragged** from anywhere on them, buttons included. A
  pinch that moves the panel is a drag; a pinch that stays put is a press.

### The controls panel

A second draggable panel carries four buttons:

| Button | What it does |
| --- | --- |
| **Reset size** | Every object back to true size, the scene included. |
| **Bring to me** | Everything back in front of you, sizes left alone. |
| **Reset all** | Both of the above, plus rotation. |
| **Grab: parts / whole** | Whether a grab moves one object or the whole scene. |

### Scale modes

The **Scale** section of the panel has three, for when true size is not what
you want:

- **1:1 True Size**, the default, and the point of the whole thing.
- **Ratio**, for example 1:10, for something that would not fit in the room.
- **Fit**, which scales whatever you send to a size you name.

## When something does not work

**The lens says `No FreeCAD yet`.** It has no address. That message is the
lens's, and it is shared with Holo-CAD, so it names the wrong program here.
Press **Share Over the Internet** and type the words it prints.

**The lens says `bridge: retrying`.** It has an address and cannot reach it.
On the same Wi-Fi route this is almost always the firewall rule, or a
network that stops devices reaching each other. Many guest, campus and hotel
networks do the latter and nothing on your machine can change it. The phone
browser check above tells you which of the two you have.

**The lens says `model: waiting for a push`.** Good news, it is connected.
Press **Send to Spectacles**.

**`[WinError 10048] Only one usage of each socket address`.** Something else
already has port 8766, most likely another Blender. Close it, or change the
port in the extension's preferences. The server refuses to share the port
rather than quietly starting a second server that gets none of the traffic.

**The tunnel button says Allow Online Access is off.** That is Blender's own
switch, in **Preferences, System, Network**. Extensions that reach the
internet have to respect it.

**The model is a hundred times too big or too small.** Check **Scene
Properties, Units, Unit Scale**. A scene authored at 1 unit = 1 cm reports
0.01, and that is the number everything else is built on. The extension
reads it, so this is usually a scene that says metres and means something
else.

**Nothing visible to send.** Hidden objects are deliberately skipped: an
object you have put away is one the glasses should not show. Cameras,
lights, empties and armatures cannot become meshes and are skipped too.

## How the sizing works

This is the part worth understanding, because it is what makes the model
trustworthy.

Three different units are in play. Blender is Z up, and one Blender unit is
one metre times the scene's **Unit Scale**. glTF declares metres and is Y up.
Lens Studio works in centimetres.

**The extension states the conversion rather than leaving the lens to infer
it.** It wrote the file, so it knows: it writes metres, which makes one file
unit 100 Lens Studio units, and it sends that number with every model. It is
identical for every object, which matters more than it sounds: an object
scaled by a factor of its own looks both the wrong size and in the wrong
place, and a scene of them falls apart.

**It also reports the true size, measured from the evaluated triangles**,
not from Blender's `bound_box`. Those differ whenever a modifier changes the
shape, because `bound_box` is the object's bounds before evaluation, so an
arrayed or subdivided object would report the size it used to be.

**And each object's mesh is written about its own bottom centre**, with its
position sent separately, relative to the bottom centre of the whole send.
That is what lets the lens rebuild a scene in the right shape while still
updating one object at a time.

That origin is worked out once and then held. Recomputing it per send looks
tidier and is wrong: moving one object would change the bounds, which would
change the origin, which would change every other object's offset, so every
object would be re-sent.

The lens still measures the mesh it loaded, but only to check the
arithmetic. The measurement is not trustworthy enough to scale by, which the
FreeCAD side of this project learned the hard way.

## What is proven, and what is not

**Verified by automated tests**, on Blender's own Python: the 100 mm cube
acceptance arithmetic, the 5 inch ruler case, a scene authored in
centimetres, the Z up to Y up turn, per object offsets, modifiers applied
and not, colour from a Principled BSDF, the WebSocket handshake against the
RFC's own test vector, and the whole chain from a Blender object to a
downloaded GLB.

**Not yet on the glasses.** The extension is new. The lens it talks to is
the one proven on hardware with Holo-CAD, and the wire protocol is identical
and covered by the end to end test, but nobody has yet put these glasses on
and looked at a Blender scene. The ruler check is the first thing to do.

## Layout

```
blender_extension/holo_blender/     the extension
  blender_manifest.toml             what Blender 4.2 and later read
  __init__.py                       preferences, register and unregister
  holo/exporter.py                  Blender objects to GLB, and the true size
  holo/server.py                    HTTP + WebSocket, running inside Blender
  holo/service.py                   server, send, live mode
  holo/tunnel.py                    runs cloudflared, and adopts a running one
  holo/ops.py                       the operators behind the buttons
  holo/ui.py                        the Holo tab in the viewport sidebar
Spectacles/Holo-Blender/            the Lens Studio project
install.ps1                         link it in, or build a .zip
tools/                              tests
```

## Tests

```powershell
.\tools\run_tests.ps1
```

104 checks in three suites, all on Blender's own Python with nothing
installed, which is the interpreter the extension actually runs on.

- `test_server.py` the embedded server: the RFC 6455 handshake against the
  spec's own test vector, host rewriting, version pruning, broadcast, a
  frame past the 64 bit length boundary, and the refusals. Runs without
  Blender at all, because the server never touches Blender's data. That rule
  is what makes it safe to run on a thread.
- `test_exporter.py` Blender objects to GLB: the acceptance arithmetic, the
  scene unit scale, the ruler case, the axis turn, per object offsets,
  modifiers, colour, and which objects get picked.
- `test_end_to_end.py` the whole chain: a Blender object through
  `service.send()` to a WebSocket client and a downloaded GLB, including per
  object sends, an object that moves, one that is deleted, and a lens that
  connects late.

Both Blender suites run with `--factory-startup`, so they are unaffected by
whatever addons and preferences happen to be set up on the machine.

## Why there is no server to host

A published lens may only use `wss://` and `https://`, which needs a real
certificate on a public hostname. Blender running on your machine has
neither.

cloudflared solves it without anyone hosting anything: it opens an outbound
connection from your machine and Cloudflare hands back a real certificate on
a public hostname, free and without an account. The model still comes from
your own machine. The cost is the four random words, which change when the
tunnel restarts.
