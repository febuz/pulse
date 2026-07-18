// Pulse AR — JSON data-transfer objects for the Quest 3S client.
//
// Mirrors the JSON the edge node returns from POST /observe (see
// examples/pulse_ar_server.py and knitweb.edge.pulse_ar.service.observation_view).
// Unity's JsonUtility fills these [Serializable] classes directly.

using System;

namespace Knitweb.PulseAR
{
    [Serializable]
    public class Detection
    {
        public string what;             // object class label (WHAT)
        public string taxonomy;         // class / taxonomy id
        public int confidence_bps;      // 0..10000 basis points
        public int[] bbox;              // [x, y, w, h] in captured-frame pixels
        public string owner;            // PLS address of the owner (WHO)
        public string maker;            // PLS address of the maker (WHO)
        public string where;            // geohash cell (WHERE)
        public int alt_band;
        public int[] dimensions_mm;     // [w, h, d] integer millimetres (HOW)
        public string device;           // observing device PLS address (DEVICE)
        public string cid;              // content id of the observation

        public float Confidence => confidence_bps / 10000f;

        public string Summary()
        {
            string dims = (dimensions_mm != null && dimensions_mm.Length == 3 &&
                           (dimensions_mm[0] + dimensions_mm[1] + dimensions_mm[2]) > 0)
                ? $"\n{dimensions_mm[0]}×{dimensions_mm[1]}×{dimensions_mm[2]} mm" : "";
            string mk = string.IsNullOrEmpty(maker) ? "" : $"\nmaker {Short(maker)}";
            return $"{what}  {(Confidence * 100f):0}%{dims}{mk}";
        }

        static string Short(string a) => (a != null && a.Length > 12) ? a.Substring(0, 12) + "…" : a;
    }

    [Serializable]
    public class ObserveResponse
    {
        public string device;
        public int count;
        public Detection[] detections;
        public string error;
    }
}
