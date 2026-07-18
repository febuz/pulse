#!/usr/bin/env python3
"""Pulse AR demo: two smartglasses share verified object observations over the
bitchat BLE mesh.

Run:  PYTHONPATH=src python3 examples/pulse_ar_demo.py

Shows the full edge loop with no infrastructure:
  1. A wearer's YOLO→CNN→LLM pipeline turns a camera frame into signed
     observations — the WHAT (class), WHO (owner + maker), WHERE (geohash),
     HOW (mm dimensions), and DEVICE.
  2. Those observations flood a bitchat Bluetooth Low Energy mesh (fragmented,
     TTL-bounded, deduped) to a nearby wearer.
  3. The peer verifies each observation *before* trusting it, keeps the ones
     anchored near itself, and fuses them into a field-of-view overlay + a
     compact feature set that augments its inner world-model.
"""

from knitweb.core import crypto
from knitweb.edge.pulse_ar import (
    PriorsLLM,
    PulseARGlass,
    StubYOLODetector,
    TaxonomyCNN,
    VisionPipeline,
)


def build_pipeline() -> VisionPipeline:
    """A deterministic stand-in stack — real weights (ultralytics YOLO, a torch
    CNN, a hosted/on-device LLM) plug in behind these same three interfaces."""
    detector = StubYOLODetector({          # YOLO: coarse boxes
        "chair": (10, 20, 100, 200),
        "laptop": (300, 120, 80, 60),
    })
    cnn = TaxonomyCNN({                     # CNN: fine-grained class + taxonomy id
        "chair": ("office_chair", "otkg:furniture/chair"),
        "laptop": ("laptop", "otkg:device/laptop"),
    })
    llm = PriorsLLM({                       # LLM: the WHO + HOW priors from the fabric
        "otkg:furniture/chair": {
            "width_mm": 600, "height_mm": 1100, "depth_mm": 620,
            "maker": "pls1maker_hermanmiller", "fiber_cid": "bcid-chair-fiber",
        },
        "otkg:device/laptop": {"width_mm": 304, "height_mm": 16, "depth_mm": 212},
    })
    return VisionPipeline(detector, cnn, llm)


def main() -> None:
    # Two wearers ~40 m apart in Amsterdam.
    wp, wk = crypto.generate_keypair()
    pp, pk = crypto.generate_keypair()
    wearer = PulseARGlass(priv=wp, pub=wk, lat=52.3702, lon=4.8952,
                          pipeline=build_pipeline(), precision=5)
    peer = PulseARGlass(priv=pp, pub=pk, lat=52.3705, lon=4.8955,
                        pipeline=build_pipeline(), precision=5)
    wearer.mesh.connect(peer.mesh)         # a Bluetooth link between the two glasses

    print(f"wearer device: {wearer.device}")
    print(f"peer   device: {peer.device}\n")

    # 1 + 2. The wearer sees, signs, and floods over the mesh.
    frame = b"a photo of a chair and a laptop"
    shared = wearer.observe_and_share(frame, observed_at=1, owner="pls1owner_alice")
    print(f"wearer shared {len(shared)} signed observations over the bitchat mesh:")
    for s in shared:
        o = s.observation
        print(f"  - {o.label:13s} {o.width_mm}x{o.height_mm}x{o.depth_mm}mm "
              f"maker={o.maker or '?':24s} verified={s.verify()}")

    # 3. The peer verified + kept them; render the overlay + inner-model features.
    print(f"\npeer kept {peer.observation_count} verified, near observations")
    print("peer overlays (what to draw in the field of view):")
    for ov in peer.overlays():
        print(f"  - WHAT={ov['what']}  WHO(owner={ov['owner'] or '?'}, maker={ov['maker'] or '?'})")
        print(f"    WHERE={ov['where']}  HOW={ov['dimensions_mm']}mm  DEVICE={ov['device']}")
        print(f"    confidence={ov['confidence_bps'] / 100:.1f}%  cid={ov['cid'][:16]}…")

    print("\npeer inner-model features (augment the world-model / CNN):")
    for label, feat in sorted(peer.features().items()):
        print(f"  - {label}: count={feat['count']} taxonomies={feat['taxonomies']} "
              f"makers={feat['makers']}")


if __name__ == "__main__":
    main()
