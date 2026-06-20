"""vBank — end-to-end voting demo on the personhood foundation.

Runs the whole vBank loop in one script and asserts every invariant, so a green run is the
feature's definition-of-done:

    define a poll → enrol real voters via the personhood gate (eIDAS trusted-RP, zero PII on
    the fabric) → each voter casts a gated, signed ballot → certify a deterministic,
    one-person-one-vote, auditable result → show that a revoked voter cannot cast.

Run:  PYTHONPATH=src python examples/vbank_demo.py   (exit 0 ⇒ vBank works)

Everything is stdlib + `cryptography` only — no heavy deps, integers throughout, no identity
data ever written to the fabric.
"""

from __future__ import annotations

from knitweb.core import crypto
from knitweb.knitwebs.vbank import Ballot, Poll, VbankKnitweb, VbankPoll
from knitweb.personhood.errors import RevokedError
from knitweb.personhood.gate import AnchorIndex, enroll, require_personhood
from knitweb.personhood.pairwise import derive_pairwise_keypair, pairwise_address
from knitweb.personhood.records import ISSUER_CLASS_EUDI_PID
from knitweb.personhood.revocation import RevocationLog
from knitweb.personhood.verifier import Presentation, TrustedRPVerifier

EUDI_ENTRY = b"eu-trusted-list:NL:pid-issuer"
SCOPE = "vbank"
POLL_ID = "referendum-2026"
NOW = 1500


def _presentation(secret: bytes) -> Presentation:
    return Presentation(holder_secret=secret, issuer_entry=EUDI_ENTRY, age_over_18=True,
                        is_unique_person=True, not_before=1000, not_after=2000,
                        transcript=b"openid4vp-redacted")


def main() -> None:
    # --- infrastructure: an eIDAS relying-party verifier, anchor index, revocation log,
    #     and a (separate) election authority that defines polls and certifies results.
    verifier = TrustedRPVerifier.from_issuer_entries({EUDI_ENTRY: ISSUER_CLASS_EUDI_PID})
    rp_priv, _ = crypto.generate_keypair()
    index = AnchorIndex()
    revlog = RevocationLog(rp_priv, scope=SCOPE)
    authority = VbankPoll(crypto.generate_keypair()[0], SCOPE)

    poll = authority.define(Poll(scope=SCOPE, poll_id=POLL_ID, options=3,
                                 opens_at=1000, closes_at=2000, quorum=2))
    print(f"poll defined: {POLL_ID} (3 options, quorum 2), signed by {poll.record['authority']}")

    # --- five voters enrol once each and cast a gated, signed ballot.
    choices = [0, 0, 1, 2, 0]
    ballots = []
    for i, choice in enumerate(choices):
        secret = crypto.sha256(f"voter-{i}".encode())
        priv, pub = derive_pairwise_keypair(secret, SCOPE)
        addr = pairwise_address(pub)
        enroll(SCOPE, _presentation(secret), verifier=verifier, anchor_index=index,
               rp_priv=rp_priv, holder_pairwise_priv=priv,
               revocation_pointer=crypto.sha256(b"rev" + secret).hex())
        ticket = require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                                    anchor_index=index, now=NOW, revocation=revlog, epoch=1)
        att = VbankKnitweb(SCOPE).emit(
            Ballot(scope=SCOPE, poll_id=POLL_ID, choice=choice, voter=addr,
                   scope_nullifier=ticket.scope_nullifier, cast_at=NOW),
            ticket, priv)
        assert att.verify(author_field="actor")
        # the on-fabric ballot carries NO identity — only a scoped nullifier + pairwise address
        assert set(att.record) == {"kind", "scope", "poll_id", "choice", "actor",
                                   "scope_nullifier", "seq", "cast_at"}
        ballots.append(att.record)
    print(f"{len(ballots)} gated, signed ballots cast (no identity on the fabric)")

    # --- certify the result (deterministic, one-person-one-vote, auditable).
    result = authority.certify_result(poll.record, ballots)
    assert result.verify(author_field="authority")
    r = result.record
    assert r["total_voters"] == 5
    assert r["results"] == [[0, 3], [1, 1], [2, 1]]
    assert r["winner"] == 0 and r["winner_votes"] == 3 and r["quorum_met"] is True
    # order-independence: shuffling the ballot set gives the identical certified result
    assert authority.certify_result(poll.record, list(reversed(ballots))).cid == result.cid
    print(f"result certified: winner=option {r['winner']} with {r['winner_votes']} votes, "
          f"turnout {r['total_voters']} (quorum_met={r['quorum_met']}); ballot_root={r['ballot_root'][:16]}…")

    # --- a revoked voter cannot obtain a ticket, so cannot cast.
    secret = crypto.sha256(b"revoked-voter")
    priv, pub = derive_pairwise_keypair(secret, SCOPE)
    enroll(SCOPE, _presentation(secret), verifier=verifier, anchor_index=index,
           rp_priv=rp_priv, holder_pairwise_priv=priv,
           revocation_pointer=crypto.sha256(b"rev" + secret).hex())
    revlog.revoke(crypto.sha256(b"rev" + secret).hex(), revoked_at=NOW)
    try:
        require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                           anchor_index=index, now=NOW, revocation=revlog, epoch=2)
        raise AssertionError("revoked voter should not get a ticket")
    except RevokedError:
        print("revoked voter correctly blocked from casting")

    print("\nvBank demo OK ✓")


if __name__ == "__main__":
    main()
