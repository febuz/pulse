#!/usr/bin/env python3
"""End-to-end Knitweb demo: pay pulses, then compile + serve verified bytecode.

Run:  PYTHONPATH=src python3 examples/synaptic_demo.py

Shows the full PLS economic loop in miniature:
  1. A device wallet pays PLS to a spider for a verified relation bundle.
  2. The spider resolves an OriginTrail Knowledge Asset, compiles it to signed
     synaptic bytecode, and serves it.
  3. The device verifies the originator signature and decodes the relations —
     the edge-side path an AR glass / IoT device would take.
"""

from knitweb import sdk
from knitweb.core import crypto


def main() -> None:
    # --- 1. Pay pulses for access ----------------------------------------
    device = sdk.Wallet.create(genesis_pulses=10)   # dev seed; real wallets earn PLS
    spider = sdk.Wallet.create()
    print(f"device {device.address}  ->  {device.balance()} PLS")
    print(f"spider {spider.address}  ->  {spider.balance()} PLS")

    device.pay(spider, pulses=1, timestamp=1)        # one pulse per served bundle
    print(f"\nafter paying 1 pulse: device={device.balance()} PLS, spider={spider.balance()} PLS")

    # --- 2. Spider compiles + signs verified relations -------------------
    originator_priv, originator_pub = crypto.generate_keypair()
    asset = {
        "origintrail_id": 99482,
        "originator": "Global Finance & Media Corp",
        "linked_sources": [
            {"type": "IFRS_File", "url": "https://ifrs.org"},
            {"type": "YouTube_Video", "url": "https://youtube.com/watch?v=x"},
            {"type": "Youku_Video", "url": "https://youku.com/v_show/y"},
            {"type": "RuTube_Video", "url": "https://rutube.ru/video/z"},
        ],
    }
    bytecode, signature = sdk.compile_asset(asset, originator_priv)
    print(f"\ncompiled bundle: {len(bytecode)} bytes, signed by originator")

    # --- 3. Device verifies + decodes at the edge ------------------------
    ok = sdk.verify_bundle(originator_pub, bytecode, signature)
    print(f"originator signature valid: {ok}")
    decoded = sdk.decode_bundle(bytecode)
    print(f"originator: {decoded['originator']}")
    print(f"relations:  {len(decoded['relations'])}")
    for r in decoded["relations"]:
        print(f"  - {r.predicate} -> {r.obj}  [{r.source_type}]")


if __name__ == "__main__":
    main()
