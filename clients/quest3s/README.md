# Pulse AR — Meta Quest 3S client

A native Unity client that turns a Quest 3S into a Pulse AR **glass**: it reads the
passthrough cameras, sends frames to the Pulse AR edge node (which runs real
ultralytics YOLO), and draws the returned observations — **WHAT / WHO / WHERE /
HOW / DEVICE** — as world-anchored labels in passthrough.

## Why this is a native app talking to a server (not WebXR, not on-device)

- **ultralytics YOLO is Python + PyTorch** and does not run on the Quest's
  standalone Android runtime. Inference runs on a nearby node — a laptop, phone, or
  edge box, i.e. a Knitweb **spider** selling verifiable compute for PLS. The
  headset is the thin glass; it captures + renders.
- **Raw Quest cameras are only reachable through the Passthrough Camera API (PCA)**,
  a Unity/Android API (Quest 3 / 3S, Horizon OS v74+). WebXR in the Quest Browser
  cannot read passthrough pixels, so a browser page cannot detect what the headset
  sees — hence a native Unity client.

```
Quest 3S (this client)            Edge node (examples/pulse_ar_server.py)
  PCA passthrough camera  ──JPEG──▶  ultralytics YOLO → VisionPipeline
  world-anchored labels  ◀──JSON──   sign → ObjectObservation → bitchat mesh
```

If you only want to *see it working* first, open the node URL in a phone/laptop
browser — `examples/pulse_ar_web/index.html` does the same loop with the device
webcam, no Unity needed.

## Prerequisites

- **Unity 6** (or 2022.3 LTS) with **Android Build Support** (SDK/NDK/OpenJDK).
- **Meta XR Core SDK** v74+ (Package Manager or the Meta XR All-in-One SDK).
- **Passthrough Camera API samples** — provides `WebCamTextureManager`,
  `PassthroughCameraUtils`, `PassthroughCameraPermissions` (namespace
  `PassthroughCameraSamples`), imported by `PulseARClient.cs`.
- A Quest 3 or 3S in **Developer Mode**.

## Setup

1. Create/open an MR project; enable passthrough (OVRManager → Insight Passthrough,
   or an `OVRPassthroughLayer`).
2. Import the Meta XR Core SDK **and** the Passthrough Camera API samples.
3. Add the camera permissions to your `AndroidManifest.xml`:
   ```xml
   <uses-permission android:name="android.permission.CAMERA" />
   <uses-permission android:name="horizonos.permission.HEADSET_CAMERA" />
   ```
4. Copy `PulseARClient.cs` and `PulseARModels.cs` into `Assets/PulseAR/`.
5. In the scene:
   - add the PCA `WebCamTextureManager` (from the samples) to a GameObject;
   - create an empty GameObject, add **`PulseARClient`**, and assign:
     - **Web Cam Manager** → the `WebCamTextureManager`;
     - **Label Prefab** → a small world-space prefab with a `TextMesh` in its
       children (a 3D Text or a TMP label facing +Z);
     - **Server Url** → `http://<node-ip>:8008` (same Wi-Fi as the headset).
6. Start the node on that machine:
   ```bash
   pip install 'knitweb[vision]'
   PYTHONPATH=src python3 examples/pulse_ar_server.py --host 0.0.0.0 --port 8008
   ```
7. Build & Run to the Quest (Android). Approve the camera permission prompt on
   first launch. Look at objects — labels appear anchored to them.

## Tuning & notes

- **Send rate / quality**: `sendRate` (fps) and `jpegQuality` on the component
  trade latency for load. 5 fps at 640 px is a good start.
- **Label placement** unprojects each box centre through the PCA camera and
  raycasts the scene; with no physics/depth hit it falls back to `defaultDepth`.
  For solid depth, feed the Quest **Depth API** into the raycast.
- **SDK drift**: three call sites are marked `(adjust for SDK)` —
  `PassthroughCameraPermissions.EnsurePermissionAsync`,
  `PassthroughCameraUtils.GetCameraPoseInWorld`, and
  `PassthroughCameraUtils.ScreenPointToRayInWorld`. Names/signatures track Meta's
  samples; match them to the SDK version you imported.
- **HTTP on device**: plain-HTTP to a LAN IP is fine for local dev; Android may
  require `usesCleartextTraffic="true"` in the manifest `<application>` tag.
- This client is provided as source to build on your own machine; it has **not**
  been compiled or run on-device from this repository.
