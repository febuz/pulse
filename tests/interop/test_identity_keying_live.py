"""Live-path proofs that reputation is keyed on the PROVEN node identity (#58).

Step 2 of #58 keys the ban gate + every reputation penalty on a peer's proven
cryptographic node key (``node:<pubkey>``) instead of its ``tcp:<ip>`` whenever
the dialing peer piggybacks a valid identity proof on its request. That removes
the NAT collateral-ban: a forger is banned individually by its key, and an honest
peer sharing the forger's public IP — but presenting its OWN proof — is keyed on
its own key and untouched.

These tests drive the EXISTING in-process transport pattern the convergence suite
uses: genuine :class:`~knitweb.fabric.node.FabricNode`s whose ``__aenter__`` runs
the real ``start()`` accept loop over the real
:class:`~knitweb.p2p.transport.TcpTransport`, all on ``127.0.0.1`` (so every peer
shares one IP — exactly the NAT scenario). There is NO real-socket bidirectional
handshake: the proof is self-minted and rides on the one request the dialer was
already sending, so nothing here adds a round-trip that could stall. Every
``await`` is wrapped in :func:`asyncio.wait_for` with a 5s timeout so a stall
fails loudly instead of hanging.

Proven on the LIVE path:
  1. a forger that presents a proof is banned by its ``node:<pubkey>``;
  2. an honest peer sharing the forger's IP but presenting its OWN proof is NOT
     banned (NAT collateral gone);
  3. a peer with no proof still works, keyed on its IP (backward-compat);
  4. a tampered/expired proof falls back to the IP, not accepted.
"""

import asyncio

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.node import FabricNode, _RECORD_TAG
from knitweb.p2p import identity
from knitweb.p2p.relay import ENVELOPE_ID_PROOF_KEY
from knitweb.p2p.reputation import Offense
from knitweb.p2p.transport import tcp_peer_id

TIMEOUT = 5


def run(coro):
    return asyncio.run(coro)


async def _aw(coro):
    """Every live await is bounded so a stall fails loudly instead of hanging."""
    return await asyncio.wait_for(coro, timeout=TIMEOUT)


def _forged_envelope(author_pub: str, record: dict, sig: str) -> dict:
    """A `fabric-record` gossip envelope with a chosen (author, record, sig)."""
    return {"kind": "fabric-record", "author": author_pub, "record": record, "sig": sig}


def _forged_record(author_pub: str, tag: str) -> dict:
    return {"kind": "knowledge", "title": tag, "body": "evil", "author": author_pub}


async def _dial_with_proof(attacker: FabricNode, target_addr, env: dict, signing_key: str):
    """Dial ``target`` over the REAL TcpTransport, piggybacking ``signing_key``'s
    proof onto the request — the seam a peer uses to claim its node identity.

    Mirrors what ``FabricNode._send`` does on the broadcast path, but lets the test
    choose WHICH key signs the proof (so a forger proves its own key, and an honest
    peer proves a different one) while sharing the single 127.0.0.1 IP.
    """
    # Use the node's real coarse clock so the proof is fresh against the verifier.
    proof = identity.make_id_proof(signing_key, timestamp=attacker._id_proof_now())
    stamped = dict(env)
    stamped[ENVELOPE_ID_PROOF_KEY] = identity.id_proof_to_record(proof)
    return await _aw(attacker.dialer.dial(target_addr, stamped))


@pytest.mark.interop
def test_forger_presenting_a_proof_is_banned_by_its_node_key():
    """(1) A forger that presents a proof accrues penalties on its node:<pubkey>
    (NOT on the shared 127.0.0.1 IP) and is banned there on the live path."""
    async def scenario():
        victim = FabricNode()
        attacker = FabricNode()
        async with victim, attacker:
            forger_pub = attacker.pub
            node_key = identity.node_peer_id(forger_pub)
            ip_key = tcp_peer_id("127.0.0.1")
            assert victim.reputation.score(node_key) == 0

            forgeries = 0
            banned_seen = False
            # INVALID_SIGNATURE is 50; two forgeries reach the 100 ban threshold.
            for i in range(3):
                rec = _forged_record(forger_pub, f"lie-{i}")
                env = _forged_envelope(forger_pub, rec, sig="00" * 64)
                expected = victim.reputation.score(node_key)
                resp = await _dial_with_proof(
                    attacker, victim.address, env, attacker._priv
                )
                assert resp.get("kind") == "error"
                if resp.get("code") == "banned":
                    banned_seen = True
                    assert victim.reputation.score(node_key) == expected
                else:
                    forgeries += 1
                    assert resp.get("code") == "bad-request"
                    # The penalty landed on the NODE key, not the IP.
                    assert (
                        victim.reputation.score(node_key)
                        == expected + Offense.INVALID_SIGNATURE.value
                    )
                assert victim.web.size == (0, 0)

            assert forgeries >= 1
            assert banned_seen
            assert victim.reputation.is_banned(node_key)
            # Crucially: the shared IP itself was never penalized → no collateral.
            assert victim.reputation.score(ip_key) == 0
            assert not victim.reputation.is_banned(ip_key)

    run(scenario())


@pytest.mark.interop
def test_honest_peer_sharing_the_forgers_ip_is_not_collateral_banned():
    """(2) An honest peer on the SAME 127.0.0.1 IP, presenting its OWN proof, is
    keyed on its own node key and is served — the NAT collateral ban is gone."""
    async def scenario():
        victim = FabricNode()
        forger = FabricNode()
        honest = FabricNode()  # shares 127.0.0.1 with the forger
        async with victim, forger, honest:
            forger_node_key = identity.node_peer_id(forger.pub)
            honest_node_key = identity.node_peer_id(honest.pub)

            # The forger forges twice → banned on ITS node key.
            for i in range(2):
                env = _forged_envelope(
                    forger.pub, _forged_record(forger.pub, f"f{i}"), sig="00" * 64
                )
                resp = await _dial_with_proof(forger, victim.address, env, forger._priv)
                assert resp.get("kind") == "error"
            assert victim.reputation.is_banned(forger_node_key)

            # The honest peer (same IP, own proof) weaves a valid record: served,
            # NOT collateral-banned, and the record converges.
            good = {"kind": "knowledge", "title": "ok", "body": "x", "author": honest.pub}
            sig = crypto.sign(honest._priv, _RECORD_TAG + canonical.encode(good))
            env_ok = _forged_envelope(honest.pub, good, sig=sig)
            resp = await _dial_with_proof(honest, victim.address, env_ok, honest._priv)
            assert resp.get("kind") == "fabric-ack"
            assert not victim.reputation.is_banned(honest_node_key)
            # The honest record wove into the victim's Web (it was NOT refused).
            assert victim.web.get(canonical.cid(good)) is not None
            assert victim.web.size == (1, 0)

    run(scenario())


@pytest.mark.interop
def test_peer_with_no_proof_still_works_keyed_on_ip():
    """(3) Backward-compat: a forger that presents NO proof is keyed on its IP
    exactly as before #58 (the proof is strictly optional)."""
    async def scenario():
        victim = FabricNode()
        attacker = FabricNode()
        async with victim, attacker:
            ip_key = tcp_peer_id("127.0.0.1")
            node_key = identity.node_peer_id(attacker.pub)
            assert victim.reputation.score(ip_key) == 0

            banned_seen = False
            for i in range(3):
                env = _forged_envelope(
                    attacker.pub, _forged_record(attacker.pub, f"n{i}"), sig="00" * 64
                )
                # NO proof attached → dialer.dial direct, pre-#58 IP keying.
                resp = await _aw(attacker.dialer.dial(victim.address, env))
                assert resp.get("kind") == "error"
                if resp.get("code") == "banned":
                    banned_seen = True
                assert victim.web.size == (0, 0)

            assert banned_seen
            # Keyed on the IP (pre-#58 behaviour), node key untouched.
            assert victim.reputation.is_banned(ip_key)
            assert victim.reputation.score(node_key) == 0

    run(scenario())


@pytest.mark.interop
def test_tampered_or_expired_proof_falls_back_to_ip_not_accepted():
    """(4) A tampered/expired proof is NOT accepted as identity: keying falls back
    to the IP on the live path, so a stale proof cannot dodge the IP gate."""
    async def scenario():
        victim = FabricNode()
        attacker = FabricNode()
        async with victim, attacker:
            ip_key = tcp_peer_id("127.0.0.1")
            node_key = identity.node_peer_id(attacker.pub)

            # --- a TAMPERED proof: signature broken → falls back to the IP key ---
            env = _forged_envelope(
                attacker.pub, _forged_record(attacker.pub, "tamper"), sig="00" * 64
            )
            proof_rec = identity.id_proof_to_record(
                identity.make_id_proof(attacker._priv, timestamp=attacker._id_proof_now())
            )
            proof_rec["sig"] = "00" + proof_rec["sig"][2:]  # break the signature
            stamped = dict(env)
            stamped[ENVELOPE_ID_PROOF_KEY] = proof_rec
            resp = await _aw(attacker.dialer.dial(victim.address, stamped))
            assert resp.get("kind") == "error" and resp.get("code") == "bad-request"
            # The penalty landed on the IP (fallback), NOT the proven node key.
            assert victim.reputation.score(ip_key) == Offense.INVALID_SIGNATURE.value
            assert victim.reputation.score(node_key) == 0

            # --- an EXPIRED proof: timestamp far in the past → falls back to IP ---
            stale = identity.id_proof_to_record(
                identity.make_id_proof(
                    attacker._priv,
                    timestamp=attacker._id_proof_now()
                    - identity.DEFAULT_PROOF_WINDOW_S
                    - 1000,
                )
            )
            env2 = _forged_envelope(
                attacker.pub, _forged_record(attacker.pub, "stale"), sig="00" * 64
            )
            env2[ENVELOPE_ID_PROOF_KEY] = stale
            resp2 = await _aw(attacker.dialer.dial(victim.address, env2))
            assert resp2.get("kind") == "error" and resp2.get("code") == "bad-request"
            # Both forgeries charged the IP (now 100 → banned); node key still clean.
            assert victim.reputation.score(ip_key) == 2 * Offense.INVALID_SIGNATURE.value
            assert victim.reputation.is_banned(ip_key)
            assert victim.reputation.score(node_key) == 0
            assert victim.web.size == (0, 0)

    run(scenario())
