"""Knitweb MVP — end-to-end acceptance demo (M5).

Runs the whole crypto loop in one script and asserts every invariant, so a green
run is the MVP's definition-of-done:

    genesis → P2P payment over the wire → earn PLS via verifiable useful work
    (PoUW, bounded mint) → persistence across restart → checkpoint on a Pulse beat

Run:  PYTHONPATH=src python examples/mvp_demo.py   (exit 0 ⇒ the MVP works)

Everything is stdlib + `cryptography` only — no heavy deps, integers throughout.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from knitweb import store
from knitweb.core import crypto
from knitweb.core.pulse import Pulse
from knitweb.fabric.items import checkpoint, web_state_root
from knitweb.fabric.web import Web
from knitweb.ledger.node import AccountNode
from knitweb.p2p.node import AsyncioP2PNode, PeerAddress
from knitweb.pouw.job import SynapticCompileJob, execute
from knitweb.token.mint import EmissionPolicy, Treasury


async def main() -> None:
    print("== Knitweb MVP end-to-end demo ==\n")

    # 1. Genesis. Alice is a consumer (dev-seeded — the native PLS layer has no
    #    premine; this seed is local/demo only). Bob is a spider/worker.
    alice = AccountNode(genesis_balances={"PLS": 100})
    bob = AccountNode()
    print(f"1. genesis  Alice={alice.address} ({alice.balance('PLS')} PLS)  "
          f"Bob={bob.address} ({bob.balance('PLS')} PLS)")

    # 2. P2P payment over the wire: Bob runs a node; Alice pays him 20 PLS.
    server = AsyncioP2PNode(account=bob, host="127.0.0.1", port=0)
    await server.start()
    try:
        alice_p2p = AsyncioP2PNode(account=alice)
        knit = await alice_p2p.send_knit(
            PeerAddress(server.host, server.port), bob.pub, "PLS", 20, timestamp=1
        )
    finally:
        await server.stop()
    print(f"2. p2p pay  Alice -> Bob 20 PLS over the wire (knit {knit.id[:18]}…)  "
          f"=> Alice={alice.balance('PLS')}  Bob={bob.balance('PLS')}")
    assert alice.balance("PLS") == 80 and bob.balance("PLS") == 20

    # 3. PoUW: Bob earns PLS by compiling a provenance-verified OriginTrail asset to
    #    signed synaptic bytecode; a verifier re-executes; escrow settles + bounded mint.
    orig_priv, orig_pub = crypto.generate_keypair()
    asset = {
        "origintrail_id": 42,
        "originator": "Acme",
        "linked_sources": [{"type": "IFRS_File", "url": "https://ifrs.org"}],
    }
    job = SynapticCompileJob(asset=asset, originator_pub=orig_pub)
    proof = execute(job, orig_priv)
    treasury = Treasury(EmissionPolicy(rate_num=1, rate_den=2))  # 50% work subsidy
    supply_before = alice.balance("PLS") + bob.balance("PLS")
    issuance = treasury.reward_verified_work(alice, bob, 10, job, proof, timestamp=2)
    assert issuance is not None
    print(f"3. PoUW     Bob compiled a verified asset: escrow 10 settled + "
          f"{issuance.amount} PLS minted  => Alice={alice.balance('PLS')}  "
          f"Bob={bob.balance('PLS')}  total_minted={treasury.total_minted}")
    assert issuance.amount == 5                         # 10 * 1/2, bounded by escrow
    assert alice.balance("PLS") == 70 and bob.balance("PLS") == 35
    assert alice.balance("PLS") + bob.balance("PLS") == supply_before + 5  # grew by mint only

    # 4. Persistence: Bob's node survives a restart byte-identically.
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "bob.cbor")
    store.save_node(bob, path)
    bob2 = store.load_node(path)
    print(f"4. persist  Bob saved + reloaded: balance={bob2.balance('PLS')}  "
          f"head_cid={bob2.braid.head.cid[:18]}…")
    assert bob2.balance("PLS") == 35
    assert bob2.braid.head.cid == bob.braid.head.cid    # canonical-CBOR ⇒ identical CID

    # 5. Checkpoint: anchor the fabric state to a Pulse beat.
    web = Web()
    web.weave(asset)
    web.weave(issuance.to_record())
    pulse = Pulse(interval_s=60, genesis_ts=0)
    beat = pulse.beat(timestamp=120, state_root=web_state_root(web))
    cp = checkpoint(web, beat)
    print(f"5. anchor   checkpoint epoch={cp.epoch} state_root={cp.state_root[:18]}… "
          f"on beat {beat.cid[:18]}…")
    assert pulse.verify_chain() and cp.beat_cid == beat.cid

    print("\n✅ MVP verified: genesis → p2p payment → PoUW earn (bounded mint) "
          "→ persistence → checkpoint")


if __name__ == "__main__":
    asyncio.run(main())
