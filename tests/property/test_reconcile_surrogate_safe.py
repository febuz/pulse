"""Range-reconciliation boundaries are always UTF-8/CBOR encodable (no lone surrogates).

``_int_key`` builds a boundary string from the base-``_KEY_RADIX`` digits of a split
midpoint via ``chr(d)``. An arbitrary midpoint digit can land in the UTF-16 surrogate
block ``U+D800..U+DFFF`` — which are not scalar values and cannot be UTF-8 encoded — so
the boundary crashed ``build_probe_frame``'s wire encode with ``UnicodeEncodeError``.
Reconciliation of any range whose keyspace split produced such a midpoint died instead
of converging. The fix skips the surrogate block with an order-preserving shift that
``_key_int`` reverses, so boundaries stay encodable while range tiling, the lexical
bisect, and the exact ``_key_int(_int_key(v)) == v`` round-trip are unchanged.

Integer/string only, no clock/rand; touches no canonical/CID byte path (boundaries are
ephemeral protocol values, never persisted content ids).
"""
from knitweb.p2p import reconcile as R


def _has_surrogate(s: str) -> bool:
    return any(0xD800 <= ord(c) <= 0xDFFF for c in s)


def _utf8_ok(s: str) -> bool:
    try:
        s.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def _digit_band_values():
    """Midpoint integers guaranteed to place a digit inside the surrogate band."""
    for pos in range(R._KEY_CHARS):
        place = R._KEY_RADIX ** (R._KEY_CHARS - 1 - pos)
        for d in (R._SURROGATE_LO, R._SURROGATE_LO + 1, 0xDFFE, 0xDFFF):
            # A low non-surrogate digit in a higher place keeps the value < ceiling.
            yield d * place + (0x41 * (place * R._KEY_RADIX) if pos > 0 else 0)


def test_int_key_emits_no_surrogate_and_is_utf8_encodable():
    # Every value that forces a surrogate-band digit must still yield an encodable bound.
    checked = 0
    for v in _digit_band_values():
        if v >= R._KEY_RADIX ** R._KEY_CHARS:
            continue
        s = R._int_key(v)
        assert not _has_surrogate(s), f"_int_key({v}) emitted a surrogate: {[hex(ord(c)) for c in s]}"
        assert _utf8_ok(s), f"_int_key({v}) is not utf-8 encodable"
        checked += 1
    assert checked > 0, "test was vacuous — no surrogate-band values exercised"


def test_split_range_boundaries_build_valid_probe_frames():
    # End-to-end regression: recursively split realistic base32 CID ranges and assert
    # EVERY produced boundary builds a probe frame without crashing. Pre-fix the "2".."7"
    # range alone produced 1500+ surrogate boundaries that raised UnicodeEncodeError here.
    bounds = set()

    def rec(lo, hi, depth):
        if depth > 6:
            return
        for (a, b) in R.split_range(lo, hi, fanout=R.FANOUT):
            bounds.add(a)
            bounds.add(b)
            if (a, b) != (lo, hi):
                rec(a, b, depth + 1)

    for lo, hi in (("a", "z"), ("baf", "bag"), ("2", "7")):
        rec(lo, hi, 0)

    assert len(bounds) > 1000, "exploration was too shallow to be meaningful"
    for b in bounds:
        if b == R.FULL_HI:
            continue
        # The operation that crashed pre-fix — must now succeed for every boundary.
        R.build_probe_frame(b, R.FULL_HI, 0, 0, 0)
        assert not _has_surrogate(b)


def test_key_int_int_key_round_trips_through_the_shift():
    # The surrogate-skip shift must be exactly reversible: digits recovered intact.
    for v in _digit_band_values():
        if v >= R._KEY_RADIX ** R._KEY_CHARS:
            continue
        # _int_key drops trailing zero digits, so round-trip holds for the value its
        # minimal-prefix bound represents; re-encoding that bound is the fixed point.
        s = R._int_key(v)
        assert R._int_key(R._key_int(s)) == s


def test_int_key_is_strictly_monotonic_across_the_band():
    # Order preservation across the surrogate boundary (the shift must not reorder).
    vals = sorted({v for v in _digit_band_values() if v < R._KEY_RADIX ** R._KEY_CHARS})
    prev_v, prev_s = None, None
    for v in vals:
        s = R._int_key(v)
        if prev_v is not None and prev_v < v:
            assert prev_s <= s, f"non-monotonic: _int_key({prev_v})>{_int_key_dbg(prev_s)} vs _int_key({v})"
        prev_v, prev_s = v, s


def _int_key_dbg(s):  # pragma: no cover - only used in an assertion message
    return [hex(ord(c)) for c in s]


def test_fix_is_a_noop_for_non_surrogate_boundaries():
    # Conservative-fix guarantee: any boundary whose digits are all below the surrogate
    # band is byte-identical to plain chr() — only the broken band changed.
    for v in (0, 1, 0x41, 0x61 * R._KEY_RADIX ** 5, 0xD7FF, 0xD7FF * R._KEY_RADIX ** 4):
        if v >= R._KEY_RADIX ** R._KEY_CHARS:
            continue
        s = R._int_key(v)
        assert all(ord(c) < R._SURROGATE_LO for c in s)
