# Pulse AR — preparation manual (Meta Quest 3S & other AR glasses)

A step-by-step field guide to standing up the Pulse AR case: a headset (or any
camera device) sees the room, a nearby node runs real YOLO, and the objects come
back tagged with **WHAT / WHO / WHERE / HOW / DEVICE**, signed and shared on the
bitchat mesh.

If you only want the architecture and the "why", read
[`QUEST3S_AR.md`](QUEST3S_AR.md) first. This document is the checklist.

---

## 0. The shape of the setup

```
  ┌── AR device (thin glass) ──┐         ┌── Compute node (a "spider") ──┐
  │  camera → JPEG frames      │ ──Wi-Fi──▶  real ultralytics YOLO       │
  │  renders labels in view    │ ◀──JSON──   sign → observe → mesh        │
  └────────────────────────────┘         └───────────────────────────────┘
```

**The universal contract:** any device that can `POST` a JPEG to `/observe` and
render the JSON reply works with Pulse AR. The Quest 3S is the richest case; a phone
browser is the fastest; other glasses sit in between. You prepare **two** things: the
**node** (§1) and the **device** (§2 for Quest, §3 for others).

**Bill of materials**
- A **compute node**: a laptop / mini-PC / phone with Python 3.12+. A CUDA GPU makes
  YOLO real-time; CPU works for a slower demo.
- An **AR device**: Meta Quest 3 / 3S (best), or a phone, or other AR glasses (§3).
- Both on the **same Wi‑Fi/LAN**, able to reach each other by IP.
- For the native Quest client: a **dev machine with Unity** (§2C).

---

## 1. Prepare the compute node (the spider)

The node does the heavy lifting. Do this first — everything else points at it.

1. **Get the code and dependencies**
   ```bash
   git clone https://github.com/febuz/pulse.git && cd pulse
   python3 -m venv .venv && . .venv/bin/activate
   pip install -e .            # core
   pip install 'knitweb[vision]'   # ultralytics + pillow (real YOLO)
   ```
   - **GPU (recommended):** install a CUDA build of torch for your platform per the
     [PyTorch instructions](https://pytorch.org/get-started/locally/) *before* the
     first run, so YOLO uses the GPU. CPU-only works but is slower.
   - **First run downloads the model weights** (a few MB for `yolo11n.pt`). If the
     node will be offline, run once online first, or copy the `.pt` file over and
     pass it with `--model /path/to/weights.pt`.

2. **Pick a model** (accuracy ↔ speed): `yolo11n.pt` (nano, fastest, default) →
   `yolo11s.pt` → `yolo11m.pt`. Start with nano.

3. **Find the node's LAN IP** (the headset needs it):
   ```bash
   # Linux/macOS
   ip addr | grep 'inet ' | grep -v 127.0.0.1     # or:  ipconfig getifaddr en0
   # Windows
   ipconfig | findstr IPv4
   ```
   Say it is `192.168.1.50`.

4. **Start the node**
   ```bash
   PYTHONPATH=src python3 examples/pulse_ar_server.py --host 0.0.0.0 --port 8008
   # options: --model yolo11s.pt  --conf 0.30  --lat <deg> --lon <deg>  --owner pls1...
   ```
   You should see `detector: ultralytics YOLO (yolo11n.pt)`. If it says
   `stub text-frame detector`, the `vision` extra is not installed — fix step 1.

5. **Smoke-test the node** from any browser on the LAN:
   - `http://192.168.1.50:8008/health` → JSON with the device address + detector.
   - `http://192.168.1.50:8008/` → the built-in webcam client; click **Start
     camera**, point it at a chair/laptop/bottle, and boxes should appear. This
     alone proves the whole pipeline before any headset is involved.

6. **Open the firewall** for the port if needed (`ufw allow 8008/tcp`, or the
   Windows Defender prompt on first run). Keep the node on a **trusted LAN** — camera
   frames leave the device (see §6).

**Node endpoints** (the contract every client uses):

| Method + path | Purpose |
|---|---|
| `GET /` | the built-in webcam browser client |
| `GET /health` | node status (device address, detector, cell) |
| `POST /observe?lat=&lon=&alt=` | body = JPEG/PNG frame → detections JSON (with bbox) |
| `GET /overlays` | current fused field-of-view overlays |
| `GET /features` | inner-world-model feature set |

---

## 2. Prepare the Meta Quest 3S

You have a **quick path** (browser, no build — for a first look) and the **full path**
(native Unity app that reads the passthrough cameras). The quick path cannot see
through the headset cameras (WebXR/Quest Browser has no passthrough-camera access) —
use it to confirm the node from the headset, then do the full path for real headset
vision.

### 2A. One-time headset preparation
1. Update the headset to **Horizon OS v74 or newer** (Settings → System → Software
   Update). PCA (Passthrough Camera API) needs a recent build.
2. Create a **Meta developer account / organization** at
   [developer.meta.com](https://developer.meta.com) (required for dev mode).
3. Enable **Developer Mode**: Meta Horizon phone app → **Devices** → your headset →
   **Headset settings → Developer Mode → On**. Reboot the headset.
4. Do **Space Setup** (draw your room) and enable passthrough so MR apps can anchor.
5. On the same Wi‑Fi as the node; confirm the headset can reach it — open
   `http://192.168.1.50:8008/health` in the **Quest Browser**.

### 2B. Quick path (browser, no build)
- In the Quest Browser open `http://192.168.1.50:8008/` and **Start camera**. Note:
  the Quest Browser exposes a limited camera, **not** the forward passthrough view —
  so this is a functional check, not true "see what I see." For that, do 2C.

### 2C. Full path (native Unity client — real headset cameras)
Build `clients/quest3s/` and sideload it. Summary here; the authoritative,
field-by-field steps are in [`clients/quest3s/README.md`](../clients/quest3s/README.md).

1. **Dev machine:** install **Unity 6** (or 2022.3 LTS) with **Android Build
   Support** (SDK + NDK + OpenJDK).
2. Create/open a **Meta MR** project; import the **Meta XR Core SDK** (v74+) and the
   **Passthrough Camera API samples** (they provide `WebCamTextureManager`,
   `PassthroughCameraUtils`, `PassthroughCameraPermissions`).
3. Copy `PulseARClient.cs` and `PulseARModels.cs` into `Assets/PulseAR/`.
4. Add camera permissions to `AndroidManifest.xml`:
   ```xml
   <uses-permission android:name="android.permission.CAMERA" />
   <uses-permission android:name="horizonos.permission.HEADSET_CAMERA" />
   ```
   and, for plain-HTTP to a LAN IP, set `android:usesCleartextTraffic="true"` on the
   `<application>` tag.
5. In the scene: add the PCA `WebCamTextureManager`; create a GameObject with
   **`PulseARClient`** and assign **Web Cam Manager**, a **Label Prefab** (a small
   world-space object with a `TextMesh` child), and **Server Url** =
   `http://192.168.1.50:8008`.
6. **Build & Run** to the headset over USB (or sideload the APK with
   [Meta Quest Developer Hub](https://developer.oculus.com/meta-quest-developer-hub/)
   / `adb install`). Approve the **camera permission** prompt on first launch.
7. Look at objects — labels appear anchored to them. Tune `sendRate` (fps) and
   `jpegQuality` on the component for latency vs. load.

---

## 3. Other AR glasses & camera devices

Pulse AR is device-agnostic — anything that can send a JPEG and render JSON works.

| Device class | Examples | How to run |
|---|---|---|
| **Phone / tablet** | any modern phone | Open `http://<node-ip>:8008/` in the browser. Works today, rear camera, full overlays. The fastest demo. |
| **Tethered display glasses** | Xreal Air/One, Rokid Max, VITURE | These are *displays* driven by a phone/PC — they have no usable world camera. Run the **web client on the host phone/PC**; its camera feeds the node and the glasses show the overlay. |
| **Standalone Android glasses** | Rokid, Vuzix, INMO | Build a small Android app: capture with **CameraX**, `POST` JPEG to `/observe`, draw the returned boxes/labels. Reuse the JSON contract from `clients/quest3s/PulseARModels.cs`. |
| **Other MR headsets** | Quest 3, Quest Pro | Same as §2C (PCA on Quest 3/Pro; on other OpenXR headsets use that platform's camera API). |
| **Fixed camera / robot** | webcam, IP camera, drone | Any script that grabs frames and `POST`s them works — this is the "spider watches a space" mode. |

To port to a new device you implement exactly two things:
1. **grab a frame → POST it** to `/observe` (optionally with `?lat=&lon=&alt=`), and
2. **render** each returned detection's `bbox` + `what/owner/maker/dimensions_mm`.

---

## 4. Calibration & tuning

- **Location (WHERE):** set the node's anchor with `--lat`/`--lon`, or send GPS from
  the client as query params (`/observe?lat=..&lon=..`). Observations carry a geohash;
  the proximity filter (`--precision`, default 6 ≈ ~1 km cell) decides what counts as
  "near me" when fusing peers. Lower precision = larger area.
- **Detection quality:** raise `--conf` (e.g. `0.35`) to cut false positives; use a
  bigger `--model` for accuracy. On the Quest client, drop `sendRate` if the node
  can't keep up.
- **HOW / WHO priors:** dimensions and maker/owner come from a priors table
  (`_DEMO_PRIORS` in `examples/pulse_ar_server.py`). Edit it, or wire a real
  LLM/knowledge-fabric lookup behind the `Enricher` protocol for live provenance.
- **Depth / anchoring (Quest):** the client raycasts the bbox centre into the scene;
  feed the Quest **Depth API** into that raycast for solid placement on real surfaces.

---

## 5. Verify the full loop (checklist)

- [ ] Node prints `detector: ultralytics YOLO (...)` (not the stub).
- [ ] `GET /health` reachable from the AR device's browser.
- [ ] `GET /` webcam client shows boxes on a laptop/phone.
- [ ] Quest: app installed, camera permission granted, `Server Url` set to the node IP.
- [ ] Labels appear anchored to real objects and update as you move.
- [ ] `GET /overlays` shows the same objects with signed `cid`s (the mesh record).

---

## 6. Safety, privacy & trust

- **Frames leave the device.** The headset streams camera images to the node. Keep the
  node on a **trusted LAN**; do not expose the port to the public internet. Use plain
  HTTP only on a LAN you control (add TLS/reverse-proxy for anything beyond a lab).
- **Observations are signed and verify-before-trust.** Each observation is secp256k1-
  signed and bound to the observing device's PLS address; a peer refuses any it can't
  verify before it ever reaches the field of view. The mesh moves bytes; trust is
  decided where data is consumed.
- **Integers near the hash.** YOLO's float outputs are quantised at the boundary, so
  the signed record is float-free and deterministic across peers.

---

## 7. Quick reference

```bash
# Node (compute / spider)
pip install 'knitweb[vision]'
PYTHONPATH=src python3 examples/pulse_ar_server.py --host 0.0.0.0 --port 8008 \
    --model yolo11n.pt --conf 0.30 --lat 52.3702 --lon 4.8952

# Verify
curl http://<node-ip>:8008/health
#   browser: http://<node-ip>:8008/         (webcam demo)

# Quest 3S native client
#   build clients/quest3s/ in Unity → set Server Url http://<node-ip>:8008 → Build & Run
```

**Troubleshooting**

| Symptom | Likely cause → fix |
|---|---|
| Node says `stub text-frame detector` | `vision` extra missing → `pip install 'knitweb[vision]'` |
| No detections on real camera | using the stub, or `--conf` too high, or bad lighting |
| Browser client can't reach node | wrong IP, firewall, or different Wi‑Fi/subnet |
| Quest app: request fails | cleartext HTTP blocked → add `usesCleartextTraffic="true"`; check IP |
| Quest: black/no camera | camera permission denied, or PCA sample not imported / Horizon OS < v74 |
| Model download fails | node offline → pre-download `.pt` and pass `--model /path.pt` |
| Peer observations dropped | too far apart for the proximity cell → lower `--precision` |
| Labels misplaced in depth | no scene/depth hit → wire the Quest Depth API into the raycast |
