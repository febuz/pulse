// Pulse AR — Meta Quest 3S client.
//
// Grabs passthrough camera frames via Meta's Passthrough Camera API (PCA), sends
// each to the Pulse AR edge node (examples/pulse_ar_server.py) which runs real
// ultralytics YOLO, and renders the returned observations as world-anchored labels
// in the field of view — the WHAT / WHO / WHERE / HOW / DEVICE for each object.
//
// WHY A SERVER? ultralytics/YOLO is Python+PyTorch and does not run on the Quest's
// standalone Android runtime; the headset is the thin "glass" and a nearby node
// (laptop / phone / edge box — a Knitweb *spider* selling compute) does inference.
// The raw passthrough cameras are ONLY reachable through PCA (Unity/Android), not
// WebXR — which is why this client is native Unity, not a browser page.
//
// REQUIREMENTS (build on your machine; this cannot be compiled or run on-device
// from the repo alone):
//   * Unity 6 (or 2022.3 LTS) with Android build support.
//   * Meta XR Core SDK (v74+) and the Passthrough Camera API samples
//     (WebCamTextureManager, PassthroughCameraUtils, PassthroughCameraPermissions).
//   * AndroidManifest permissions: android.permission.CAMERA and
//     horizonos.permission.HEADSET_CAMERA (see clients/quest3s/README.md).
// Attach this component to a GameObject in an MR/passthrough scene, assign the
// WebCamTextureManager and a label prefab, and set serverUrl to the node.
//
// The PCA sample class/method names below match Meta's public samples at the time
// of writing; if your SDK version differs, adjust the three marked call sites.

using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;

// From the Meta Passthrough Camera API samples package:
using PassthroughCameraSamples;

namespace Knitweb.PulseAR
{
    public class PulseARClient : MonoBehaviour
    {
        [Header("Edge node")]
        [Tooltip("Base URL of the Pulse AR node, e.g. http://192.168.1.50:8008")]
        public string serverUrl = "http://192.168.1.50:8008";
        [Tooltip("Frames per second to send for detection")]
        [Range(1, 15)] public float sendRate = 5f;
        [Range(0.3f, 0.95f)] public float jpegQuality = 0.6f;

        [Header("Passthrough camera (PCA)")]
        [Tooltip("Meta PCA sample component that surfaces the passthrough WebCamTexture")]
        public WebCamTextureManager webCamManager;

        [Header("Rendering")]
        [Tooltip("A world-space label prefab with a TextMesh / TMP_Text in children")]
        public GameObject labelPrefab;
        [Tooltip("Fallback distance (m) to place a label when no depth hit is found")]
        public float defaultDepth = 2.0f;
        [Tooltip("Seconds a label persists without a fresh detection")]
        public float labelTtl = 1.0f;

        WebCamTexture _cam;
        Texture2D _readback;
        readonly List<Label> _labels = new List<Label>();
        float _lastSend;
        bool _busy;

        class Label { public GameObject go; public TextMesh text; public float seen; }

        IEnumerator Start()
        {
            // 1) Ask for the headset-camera permission (PCA helper).
            yield return PassthroughCameraPermissions.EnsurePermissionAsync();

            // 2) Wait for the PCA manager to expose the passthrough WebCamTexture.
            while (webCamManager == null || webCamManager.WebCamTexture == null ||
                   !webCamManager.WebCamTexture.isPlaying)
                yield return null;
            _cam = webCamManager.WebCamTexture;
            _readback = new Texture2D(_cam.width, _cam.height, TextureFormat.RGB24, false);
            Debug.Log($"[PulseAR] passthrough camera {_cam.width}x{_cam.height} ready");
        }

        void Update()
        {
            ExpireLabels();
            if (_cam == null || _busy) return;
            if (Time.time - _lastSend < 1f / sendRate) return;
            _lastSend = Time.time;
            StartCoroutine(ObserveFrame());
        }

        IEnumerator ObserveFrame()
        {
            _busy = true;

            // Capture the current passthrough frame and the camera pose used for it,
            // so we can unproject detection boxes back into the world afterwards.
            _readback.SetPixels32(_cam.GetPixels32());
            _readback.Apply(false);
            var pose = PassthroughCameraUtils.GetCameraPoseInWorld(PassthroughCameraEye.Left); // (adjust for SDK)
            byte[] jpg = _readback.EncodeToJPG(Mathf.RoundToInt(jpegQuality * 100));
            int fw = _readback.width, fh = _readback.height;

            using (var req = new UnityWebRequest(serverUrl.TrimEnd('/') + "/observe", "POST"))
            {
                req.uploadHandler = new UploadHandlerRaw(jpg) { contentType = "image/jpeg" };
                req.downloadHandler = new DownloadHandlerBuffer();
                yield return req.SendWebRequest();

                if (req.result == UnityWebRequest.Result.Success)
                {
                    var resp = JsonUtility.FromJson<ObserveResponse>(req.downloadHandler.text);
                    if (resp != null && string.IsNullOrEmpty(resp.error) && resp.detections != null)
                        Render(resp.detections, pose, fw, fh);
                }
                else Debug.LogWarning($"[PulseAR] node error: {req.error}");
            }
            _busy = false;
        }

        void Render(Detection[] dets, Pose camPose, int fw, int fh)
        {
            for (int i = 0; i < dets.Length; i++)
            {
                var d = dets[i];
                if (d.bbox == null || d.bbox.Length != 4) continue;
                // bbox centre in captured-frame pixels
                var px = new Vector2Int(d.bbox[0] + d.bbox[2] / 2, d.bbox[1] + d.bbox[3] / 2);

                // Unproject the pixel through the passthrough camera into a world ray,
                // then place the label at a depth hit (fallback: fixed distance).
                Ray ray = PassthroughCameraUtils.ScreenPointToRayInWorld(
                    PassthroughCameraEye.Left, px);           // (adjust for SDK)
                Vector3 world = Physics.Raycast(ray, out var hit, 8f)
                    ? hit.point : ray.origin + ray.direction * defaultDepth;

                var label = GetLabel(i);
                label.go.transform.position = world;
                label.go.transform.rotation =
                    Quaternion.LookRotation(world - camPose.position); // billboard toward the eye
                if (label.text != null) label.text.text = d.Summary();
                label.seen = Time.time;
            }
        }

        Label GetLabel(int i)
        {
            while (_labels.Count <= i)
            {
                var go = Instantiate(labelPrefab);
                _labels.Add(new Label { go = go, text = go.GetComponentInChildren<TextMesh>() });
            }
            _labels[i].go.SetActive(true);
            return _labels[i];
        }

        void ExpireLabels()
        {
            foreach (var l in _labels)
                if (l.go.activeSelf && Time.time - l.seen > labelTtl) l.go.SetActive(false);
        }
    }
}
