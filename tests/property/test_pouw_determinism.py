"""Proofs for the PoUW determinism foundations (CRYPTO_CORPUS_STUDY §1).

Two existential gaps closed here:
  * tolerance digests so honest float noise doesn't slash (digest.py);
  * commit-before-sample with a fresh salt so neither precompute nor retroactive
    work-swap can fake a proof (challenge.py).

These primitives are the *foundation* for future GPU compute job classes; they
are intentionally NOT wired into pouw/job.py yet — the synaptic-compile job is
float-free and deterministic, so it correctly stays exact-match.
"""

import pytest

from knitweb.pouw import challenge, digest


# -- tolerance / quantized digest -------------------------------------------

@pytest.mark.property
def test_within_eps_agrees_beyond_eps_differs():
    a = [1.000000, 2.000000, 3.000000]
    near = [1.0000004, 1.9999996, 3.0000003]   # sub-eps noise
    far = [1.05, 2.0, 3.0]                      # > eps on the first element
    eps = 1e-3
    assert digest.digests_agree(a, near, eps)
    assert not digest.digests_agree(a, far, eps)


@pytest.mark.property
def test_tolerance_digest_is_deterministic_and_hex():
    vals = [0.1, 0.2, 0.3]
    d = digest.tolerance_digest(vals, 1e-6)
    assert d == digest.tolerance_digest(vals, 1e-6)
    assert len(d) == 64 and int(d, 16) >= 0     # 32-byte hex digest


@pytest.mark.property
def test_quantize_returns_int_and_rejects_bad_eps():
    assert isinstance(digest.quantize(4.5, 1.0), int)
    assert digest.quantize(4.4, 1.0) == 4 and digest.quantize(4.5, 1.0) == 5
    for bad in (0, -1.0):
        with pytest.raises(ValueError):
            digest.quantize(1.0, bad)
    for bad in (True, "1.0"):
        with pytest.raises(TypeError):
            digest.quantize(bad, 1.0)


@pytest.mark.property
def test_quantize_negative_sign_behaviour_is_round_half_up():
    # round-half-up via floor(x/eps + 0.5) is deterministic (not banker's):
    # the .5 tie breaks toward +inf, consistently on both signs.
    assert digest.quantize(-4.5, 1.0) == -4    # -4.5 + 0.5 = -4.0 -> floor -4
    assert digest.quantize(-4.6, 1.0) == -5    # -4.6 + 0.5 = -4.1 -> floor -5
    assert digest.quantize(-4.4, 1.0) == -4    # -4.4 + 0.5 = -3.9 -> floor -4


# -- commit-before-sample ----------------------------------------------------

def _blocks(n=16):
    return [f"block-{i}".encode() for i in range(n)]


@pytest.mark.property
def test_honest_commit_challenge_response_verifies():
    blocks = _blocks()
    c = challenge.commit(blocks)
    salt = challenge.new_salt()
    reveals = challenge.respond(blocks, salt, k=4)
    assert challenge.verify_response(c, salt, 4, reveals)


@pytest.mark.property
def test_retroactive_work_swap_fails_membership():
    blocks = _blocks()
    c = challenge.commit(blocks)
    salt = challenge.new_salt()
    reveals = challenge.respond(blocks, salt, k=4)
    # Worker swaps a sampled block's content after committing.
    bad = reveals[0]
    swapped = challenge.Reveal(
        index=bad.index,
        block=b"forged-content",
        proof=bad.proof,
        salted=challenge._salted_digest(salt, bad.index, b"forged-content"),
    )
    assert not challenge.verify_response(c, salt, 4, [swapped] + reveals[1:])


@pytest.mark.property
def test_stale_salt_reveals_are_rejected():
    blocks = _blocks()
    c = challenge.commit(blocks)
    salt1 = challenge.new_salt()
    reveals1 = challenge.respond(blocks, salt1, k=4)
    salt2 = challenge.new_salt()
    # Replaying salt1's reveals against a fresh salt2 fails (indices + salted mismatch).
    assert not challenge.verify_response(c, salt2, 4, reveals1)


@pytest.mark.property
def test_tampered_salted_digest_fails():
    blocks = _blocks()
    c = challenge.commit(blocks)
    salt = challenge.new_salt()
    reveals = challenge.respond(blocks, salt, k=3)
    r0 = reveals[0]
    tampered = challenge.Reveal(r0.index, r0.block, r0.proof, "00" * 32)
    assert not challenge.verify_response(c, salt, 3, [tampered] + reveals[1:])


@pytest.mark.property
def test_reordered_reveals_are_rejected():
    # verify_response compares the index list positionally; a reorder must fail.
    blocks = _blocks()
    c = challenge.commit(blocks)
    salt = challenge.new_salt()
    reveals = challenge.respond(blocks, salt, k=4)
    assert challenge.verify_response(c, salt, 4, reveals)
    assert not challenge.verify_response(c, salt, 4, list(reversed(reveals)))


@pytest.mark.property
def test_sample_indices_deterministic_distinct_and_bounded():
    salt = b"fixed-salt-for-determinism"
    idx = challenge.sample_indices(salt, n=10, k=5)
    assert idx == challenge.sample_indices(salt, 10, 5)   # deterministic
    assert len(idx) == len(set(idx)) == 5                 # distinct
    assert all(0 <= i < 10 for i in idx)
    # asking for more than n yields exactly n distinct indices
    assert len(challenge.sample_indices(salt, 4, 99)) == 4


@pytest.mark.property
def test_commit_rejects_empty_output():
    with pytest.raises(ValueError):
        challenge.commit([])


@pytest.mark.property
def test_respond_rejects_empty_output():
    # Public API guard: without this, the internal level builder would never
    # terminate for an empty leaf list.
    with pytest.raises(ValueError):
        challenge.respond([], challenge.new_salt(), k=1)


@pytest.mark.property
def test_single_block_commit_round_trips():
    blocks = [b"only-one"]
    c = challenge.commit(blocks)
    salt = challenge.new_salt()
    reveals = challenge.respond(blocks, salt, k=1)
    assert challenge.verify_response(c, salt, 1, reveals)
