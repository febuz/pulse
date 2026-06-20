"""Randomized fuzz tests for the canonical CBOR layer — the hash-critical invariant.

Every hash and signature in Knitweb assumes a single canonical byte-string per value.
These tests hammer that with thousands of randomly-generated nested structures and
assert the four properties the whole system depends on:

  1. round-trip:      decode(encode(x)) == x
  2. idempotence:     encode(decode(encode(x))) == encode(x)   (one canonical form)
  3. determinism:     dict key order never affects the bytes / CID
  4. collision-free:  flipping any encoded byte either fails to decode or yields a
                      *different* value — two distinct byte strings never decode to
                      the same object (which would break signature soundness).

Deterministic (seeded stdlib `random`) so any failure reproduces; no external deps.
"""

import random

import pytest

from knitweb.core import canonical
from knitweb.core.canonical import CanonicalError


def _rand_str(rng: random.Random) -> str:
    n = rng.randint(0, 8)
    # mix ASCII + a few multi-byte code points to exercise UTF-8 length handling
    alphabet = "abcXYZ0_/ é€中🜍"
    return "".join(rng.choice(alphabet) for _ in range(n))


def _rand_key(rng: random.Random):
    kind = rng.choice(("str", "int", "bytes"))
    if kind == "str":
        return _rand_str(rng)
    if kind == "int":
        return rng.randint(-1000, 1000)
    return bytes(rng.randrange(256) for _ in range(rng.randint(0, 4)))


def _rand_value(rng: random.Random, depth: int = 0):
    choices = ["int", "str", "bytes", "bool", "none"]
    if depth < 3:
        choices += ["list", "dict"]
    kind = rng.choice(choices)
    if kind == "int":
        # span shortest-form boundaries (24, 256, 65536, 2**32) and negatives
        return rng.choice([
            rng.randint(0, 23), rng.randint(24, 255), rng.randint(256, 65535),
            rng.randint(65536, 2**32 - 1), rng.randint(2**32, 2**40),
            -rng.randint(1, 2**33),
        ])
    if kind == "str":
        return _rand_str(rng)
    if kind == "bytes":
        return bytes(rng.randrange(256) for _ in range(rng.randint(0, 6)))
    if kind == "bool":
        return rng.choice([True, False])
    if kind == "none":
        return None
    if kind == "list":
        return [_rand_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    # dict — keys must be unique hashable scalars
    out = {}
    for _ in range(rng.randint(0, 4)):
        out[_rand_key(rng)] = _rand_value(rng, depth + 1)
    return out


@pytest.mark.property
def test_round_trip_and_idempotence_fuzz():
    rng = random.Random(0xC0FFEE)
    for _ in range(3000):
        v = _rand_value(rng)
        b = canonical.encode(v)
        assert canonical.decode(b) == v               # round-trip
        assert canonical.encode(canonical.decode(b)) == b  # idempotent canonical form


@pytest.mark.property
def test_dict_key_order_never_affects_bytes_or_cid():
    rng = random.Random(0xBEEF)
    for _ in range(1000):
        # build a dict, then a shuffled-insertion-order copy; bytes + CID must match
        base = {}
        for _ in range(rng.randint(1, 6)):
            base[_rand_key(rng)] = _rand_value(rng, depth=2)
        items = list(base.items())
        rng.shuffle(items)
        reordered = dict(items)
        assert canonical.encode(base) == canonical.encode(reordered)
        assert canonical.cid(base) == canonical.cid(reordered)


@pytest.mark.property
def test_single_byte_flip_never_silently_aliases():
    rng = random.Random(0xD00D)
    for _ in range(2000):
        v = _rand_value(rng)
        b = canonical.encode(v)
        i = rng.randrange(len(b))
        mutated = b[:i] + bytes([b[i] ^ (1 << rng.randrange(8))]) + b[i + 1:]
        if mutated == b:
            continue
        try:
            decoded = canonical.decode(mutated)
        except (CanonicalError, UnicodeDecodeError, IndexError):
            continue                                   # rejected — fine
        # if it decoded, it must NOT be the same value with different bytes
        assert decoded != v or canonical.encode(decoded) == mutated


def test_encode_decode_reject_excessive_nesting():
    """#145: container nesting beyond MAX_DEPTH raises CanonicalError (a typed, handled
    error) instead of exhausting the Python stack with RecursionError — the decode path
    is attacker-controlled (every gossiped record / CID / verify decodes untrusted bytes),
    so deep nesting must not be a DoS."""
    from knitweb.core.canonical import MAX_DEPTH, CanonicalError, decode, encode

    # At the limit: still encodes + round-trips cleanly.
    ok = cur = []
    for _ in range(MAX_DEPTH - 1):
        nxt = []
        cur.append(nxt)
        cur = nxt
    assert decode(encode(ok)) == ok

    # Past the limit: a typed CanonicalError, never a RecursionError.
    deep = cur = []
    for _ in range(MAX_DEPTH + 50):
        nxt = []
        cur.append(nxt)
        cur = nxt
    with pytest.raises(CanonicalError):
        encode(deep)

    # And the decode side rejects an over-deep buffer too (CBOR array heads, byte 0x81 = [x]).
    over_deep_bytes = b"\x81" * (MAX_DEPTH + 50) + b"\x00"
    with pytest.raises(CanonicalError):
        decode(over_deep_bytes)
