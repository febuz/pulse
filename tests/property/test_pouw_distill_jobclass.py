"""IL-105 (#115) — distill as a PoUW job class with a SPLIT verification policy.

These property tests pin the self-contained IL-105 contract that ships in
``pouw/job.py``:

  * a job-class registry that distinguishes the existing UNIFORM (byte-reproducible
    re-execution) policy from the new SPLIT (non-deterministic mining) policy;
  * a ``distill`` manifest recording query / subscription / web_state_cid /
    bundle_cid / originator, canonical-CBOR clean and content-addressable;
  * the split settlement decision predicate — reward settles iff the deterministic
    re-check passed AND the challenge window closed AND no dispute was upheld
    (the IL-106 / IL-107 producers are injected booleans).

The pre-existing uniform path (``SynapticCompileJob`` / ``execute`` / ``verify``)
is untouched; these only exercise the additive surface.
"""

import itertools

import pytest

from knitweb.core import canonical
from knitweb.pouw import job as J


# --- registry + policy taxonomy --------------------------------------------- #


def test_builtin_job_classes_have_expected_policies():
    assert J.verification_policy("synaptic-compile") == J.VERIFICATION_UNIFORM
    assert J.verification_policy("distill") == J.VERIFICATION_SPLIT
    assert J.job_class("distill").verification == J.VERIFICATION_SPLIT


def test_register_job_class_is_idempotent_but_rejects_conflicts():
    # Re-registering the same policy is a no-op (returns an equal JobClass).
    again = J.register_job_class("distill", J.VERIFICATION_SPLIT)
    assert again == J.job_class("distill")
    # Flipping a live class to a different policy is refused.
    with pytest.raises(ValueError):
        J.register_job_class("distill", J.VERIFICATION_UNIFORM)


def test_unknown_verification_policy_rejected():
    with pytest.raises(ValueError):
        J.JobClass(name="x", verification="best-effort")
    with pytest.raises(ValueError):
        J.JobClass(name="", verification=J.VERIFICATION_SPLIT)


def test_job_class_lookup_missing_raises():
    with pytest.raises(KeyError):
        J.job_class("no-such-class")


# --- bundle_cid commitment -------------------------------------------------- #


def test_bundle_cid_is_deterministic_and_content_bound():
    a = J.bundle_cid(b"some-bytecode")
    b = J.bundle_cid(b"some-bytecode")
    c = J.bundle_cid(b"other-bytecode")
    assert isinstance(a, str) and a
    assert a == b           # deterministic
    assert a != c           # content-bound
    with pytest.raises(TypeError):
        J.bundle_cid("not-bytes")  # type: ignore[arg-type]


# --- manifest --------------------------------------------------------------- #


def _manifest(**over):
    base = dict(
        query="bafy-query",
        subscription=("scope-a", "scope-b"),
        web_state_cid="bafy-web-state",
        bundle_cid="bafy-bundle",
        originator="02deadbeef",
    )
    base.update(over)
    return J.DistillManifest(**base)


def test_manifest_records_all_required_fields_and_tags():
    m = _manifest()
    rec = m.to_record()
    # AC2 — manifest records query/subscription/web_state_cid/bundle_cid/originator.
    for key in ("query", "subscription", "web_state_cid", "bundle_cid", "originator"):
        assert key in rec
    # AC1/AC4 — declares the split policy + mining stage inline.
    assert rec["job_class"] == "distill"
    assert rec["verification"] == J.VERIFICATION_SPLIT
    assert rec["stage"] == J.STAGE_MINING


def test_manifest_record_is_canonical_and_addressable():
    m = _manifest()
    # Canonical encoding must accept it (no float/None) and round-trip its CID.
    encoded = canonical.encode(m.to_record())
    assert isinstance(encoded, (bytes, bytearray))
    assert m.cid() == canonical.cid(m.to_record())
    # Equal content -> equal address; changed output -> changed address.
    assert _manifest().cid() == m.cid()
    assert _manifest(bundle_cid="bafy-other").cid() != m.cid()


def test_manifest_rejects_malformed_fields():
    with pytest.raises(ValueError):
        _manifest(web_state_cid="")
    with pytest.raises(TypeError):
        _manifest(subscription=["not", "a", "tuple"])  # list, not tuple
    with pytest.raises(TypeError):
        _manifest(subscription=(1, 2))  # non-str entries
    with pytest.raises(ValueError):
        _manifest(stage="banana")


class _FakeCandidateSet:
    """Duck-typed stand-in for ``interpret.retrieve.CandidateSet``."""

    def __init__(self, query, subscription, web_state_cid):
        self.query = query
        self.subscription = subscription
        self.web_state_cid = web_state_cid


def test_from_distill_derives_manifest_without_importing_interpret():
    cs = _FakeCandidateSet(
        query={"subject": "H2O", "predicate": "is-a"},
        subscription=("chem",),
        web_state_cid="bafy-state-1",
    )
    m = J.DistillManifest.from_distill(cs, bundle_cid="bafy-out", originator="02pub")
    assert m.web_state_cid == "bafy-state-1"
    assert m.subscription == ("chem",)
    assert m.bundle_cid == "bafy-out"
    assert m.originator == "02pub"
    # query is committed as a canonical CID fingerprint, not stored verbatim.
    assert m.query == canonical.cid(
        {"query": {"predicate": "is-a", "subject": "H2O"}}
    )


def test_from_distill_query_commitment_is_order_independent_for_dicts():
    a = _FakeCandidateSet({"a": "1", "b": "2"}, None, "s")
    b = _FakeCandidateSet({"b": "2", "a": "1"}, None, "s")
    assert J.DistillManifest.from_distill(a, bundle_cid="x", originator="p").query == \
        J.DistillManifest.from_distill(b, bundle_cid="x", originator="p").query


def test_from_distill_unscoped_subscription_becomes_empty_tuple():
    cs = _FakeCandidateSet("q", None, "bafy-state")
    m = J.DistillManifest.from_distill(cs, bundle_cid="b", originator="p")
    assert m.subscription == ()


def test_from_distill_requires_web_state_cid():
    cs = _FakeCandidateSet("q", ("s",), None)
    with pytest.raises(ValueError):
        J.DistillManifest.from_distill(cs, bundle_cid="b", originator="p")


# --- split settlement policy (AC3) ------------------------------------------ #


def test_split_settles_truth_table():
    # Settles iff deterministic_ok AND window_closed AND NOT dispute_upheld.
    for det, win, disp in itertools.product([False, True], repeat=3):
        expected = det and win and not disp
        assert J.split_settles(
            deterministic_ok=det, window_closed=win, dispute_upheld=disp
        ) is expected


def test_split_verdict_settles_only_on_full_clear():
    assert J.SplitVerdict(True, True, False).settles is True
    assert J.SplitVerdict(True, True, False).stage == J.STAGE_SETTLEMENT
    # Any single failing signal withholds reward.
    assert J.SplitVerdict(False, True, False).settles is False  # det check failed
    assert J.SplitVerdict(True, False, False).settles is False  # window still open
    assert J.SplitVerdict(True, True, True).settles is False    # dispute upheld


def test_split_policy_is_monotone_no_signal_alone_settles():
    # Withholding is conservative: with the deterministic check failing, neither a
    # closed window nor an absent dispute can rescue settlement.
    assert not J.split_settles(deterministic_ok=False, window_closed=True, dispute_upheld=False)
    assert not J.split_settles(deterministic_ok=False, window_closed=False, dispute_upheld=False)
