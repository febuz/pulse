"""Proofs for the chemistry domain knitweb (Phase 5c plugin).

The knitweb's promise: it signs a reaction ONLY if mass (every element) and charge are
conserved, the signed record is integer-only/canonical, authorship is verifiable,
and it weaves into the Web. An unbalanced or charge-violating reaction is refused.
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import verify_record
from knitweb.fabric.web import Web
from knitweb.knitwebs.chemistry import (
    ChemistryKnitweb,
    Reaction,
    Species,
    Term,
    charge_balance,
    element_balance,
    is_balanced,
)


def _water_synthesis() -> Reaction:
    h2 = Species.make("H2", {"H": 2})
    o2 = Species.make("O2", {"O": 2})
    h2o = Species.make("H2O", {"H": 2, "O": 1})
    return Reaction(
        reactants=(Term(h2, 2), Term(o2, 1)),
        products=(Term(h2o, 2),),
    )


def _silver_chloride() -> Reaction:
    ag = Species.make("Ag+", {"Ag": 1}, charge=1)
    cl = Species.make("Cl-", {"Cl": 1}, charge=-1)
    agcl = Species.make("AgCl", {"Ag": 1, "Cl": 1}, charge=0)
    return Reaction(reactants=(Term(ag, 1), Term(cl, 1)), products=(Term(agcl, 1),))


@pytest.mark.knitweb
def test_balanced_reaction_passes_checks():
    rxn = _water_synthesis()
    assert element_balance(rxn) == {}       # nothing left over
    assert charge_balance(rxn) == 0
    assert is_balanced(rxn)


@pytest.mark.knitweb
def test_emit_signs_balanced_reaction_and_is_verifiable():
    priv, pub = crypto.generate_keypair()
    kw = ChemistryKnitweb(priv)
    att = kw.emit(_water_synthesis())
    assert att.record["equation"] == "2 H2 + O2 -> 2 H2O"
    assert att.record["balanced"] is True
    assert att.verify(author_field="author")
    assert verify_record(att.record, att.author_pub, att.sig, "author")
    # signed record round-trips through canonical CBOR (integer-only path)
    assert canonical.decode(canonical.encode(att.record)) == att.record


@pytest.mark.knitweb
def test_charge_balanced_ionic_reaction_is_signed():
    priv, _ = crypto.generate_keypair()
    kw = ChemistryKnitweb(priv)
    rxn = _silver_chloride()
    assert is_balanced(rxn)
    att = kw.emit(rxn)
    assert att.verify(author_field="author")


@pytest.mark.knitweb
def test_mass_imbalance_is_refused():
    # H2 + O2 -> H2O  (O not balanced)
    h2 = Species.make("H2", {"H": 2})
    o2 = Species.make("O2", {"O": 2})
    h2o = Species.make("H2O", {"H": 2, "O": 1})
    bad = Reaction(reactants=(Term(h2, 1), Term(o2, 1)), products=(Term(h2o, 1),))
    assert element_balance(bad)              # non-empty -> imbalanced
    assert not is_balanced(bad)
    kw = ChemistryKnitweb(crypto.generate_keypair()[0])
    with pytest.raises(ValueError, match="element imbalance"):
        kw.emit(bad)


@pytest.mark.knitweb
def test_charge_imbalance_is_refused():
    # Na -> Na+ (loses an electron; charge not conserved without the electron term)
    na = Species.make("Na", {"Na": 1}, charge=0)
    na_plus = Species.make("Na+", {"Na": 1}, charge=1)
    bad = Reaction(reactants=(Term(na, 1),), products=(Term(na_plus, 1),))
    assert element_balance(bad) == {}        # mass is fine...
    assert charge_balance(bad) == 1          # ...but charge is not
    kw = ChemistryKnitweb(crypto.generate_keypair()[0])
    with pytest.raises(ValueError, match="charge imbalance"):
        kw.emit(bad)


@pytest.mark.knitweb
def test_weave_into_web_is_content_addressed():
    priv, _ = crypto.generate_keypair()
    kw = ChemistryKnitweb(priv)
    web = Web()
    cid, att = kw.weave(_water_synthesis(), web)
    assert cid in web.nodes
    assert web.nodes[cid] == att.record
    assert cid == canonical.cid(att.record)   # cid is a pure content hash
    # idempotent: weaving the same balanced reaction yields the same cid
    cid2, _ = kw.weave(_water_synthesis(), web)
    assert cid2 == cid


@pytest.mark.knitweb
def test_tampered_signed_reaction_fails_verification():
    priv, _ = crypto.generate_keypair()
    kw = ChemistryKnitweb(priv)
    att = kw.emit(_water_synthesis())
    forged = dict(att.record, equation="1 H2 + O2 -> 1 H2O")  # lie about the equation
    assert not verify_record(forged, att.author_pub, att.sig, "author")


@pytest.mark.knitweb
def test_term_order_does_not_change_content_id():
    # The same reaction written with reactants in swapped order must produce the
    # SAME signed record/CID, so equivalent reactions dedupe in the Web.
    priv, _ = crypto.generate_keypair()
    kw = ChemistryKnitweb(priv)
    h2 = Species.make("H2", {"H": 2})
    o2 = Species.make("O2", {"O": 2})
    h2o = Species.make("H2O", {"H": 2, "O": 1})
    forward = Reaction(reactants=(Term(h2, 2), Term(o2, 1)), products=(Term(h2o, 2),))
    swapped = Reaction(reactants=(Term(o2, 1), Term(h2, 2)), products=(Term(h2o, 2),))
    assert kw.to_record(forward) == kw.to_record(swapped)
    assert canonical.cid(kw.to_record(forward)) == canonical.cid(kw.to_record(swapped))
    # canonical equation is term-sorted ("H2" < "O2")
    assert kw.to_record(swapped)["equation"] == "2 H2 + O2 -> 2 H2O"


@pytest.mark.knitweb
def test_duplicate_formula_terms_do_not_change_content_id():
    priv, _ = crypto.generate_keypair()
    kw = ChemistryKnitweb(priv)
    a = Species.make("A", {"A": 1})
    a3 = Species.make("A3", {"A": 3})
    forward = Reaction(reactants=(Term(a, 2), Term(a, 1)), products=(Term(a3, 1),))
    swapped = Reaction(reactants=(Term(a, 1), Term(a, 2)), products=(Term(a3, 1),))
    assert kw.to_record(forward) == kw.to_record(swapped)
    assert canonical.cid(kw.to_record(forward)) == canonical.cid(kw.to_record(swapped))


@pytest.mark.knitweb
def test_kinetics_metadata_is_integer_and_optional():
    priv, _ = crypto.generate_keypair()
    kw = ChemistryKnitweb(priv)
    h2 = Species.make("H2", {"H": 2})
    o2 = Species.make("O2", {"O": 2})
    h2o = Species.make("H2O", {"H": 2, "O": 1})
    rxn = Reaction(
        reactants=(Term(h2, 2), Term(o2, 1)),
        products=(Term(h2o, 2),),
        kinetics=(("pre_exponential_milli", 5000), ("activation_energy_j_per_mol", 71000)),
    )
    att = kw.emit(rxn)
    assert att.verify(author_field="author")
    assert att.record["kinetics"] == [
        ["activation_energy_j_per_mol", 71000],
        ["pre_exponential_milli", 5000],
    ]


@pytest.mark.knitweb
def test_composition_order_is_normalised_for_cid_stability():
    """CID stability (#210): a Species is a multiset of (element, count) pairs, so building
    it with composition in any order must converge on one canonical form — and therefore
    one record CID. Without this, a raw-constructed (or cross-language) species could emit a
    divergent CID for the same logical molecule, forking the content-addressed Web."""
    canonical_form = (("H", 2), ("O", 1))
    via_make = Species.make("H2O", {"O": 1, "H": 2})   # make() sorts its dict input
    via_raw = Species("H2O", (("O", 1), ("H", 2)))     # raw constructor, unsorted on input
    assert via_make.composition == canonical_form
    assert via_raw.composition == canonical_form       # normalised at construction

    def frag(s: Species) -> dict:
        return {"species": s.formula, "coeff": 1,
                "composition": [list(p) for p in s.composition], "charge": s.charge}
    assert canonical.cid(frag(via_make)) == canonical.cid(frag(via_raw))


@pytest.mark.knitweb
def test_duplicate_elements_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        Species("bad", (("H", 1), ("H", 2)))


@pytest.mark.knitweb
def test_float_element_count_is_rejected():
    with pytest.raises(TypeError, match="count"):
        Species("bad", (("H", 1.5),))  # type: ignore[arg-type]


@pytest.mark.knitweb
def test_bool_element_count_is_rejected():
    with pytest.raises(TypeError, match="count"):
        Species("bad", (("H", True),))  # type: ignore[arg-type]


@pytest.mark.knitweb
def test_float_charge_is_rejected():
    with pytest.raises(TypeError, match="charge"):
        Species.make("Na+", {"Na": 1}, charge=1.0)  # type: ignore[arg-type]


@pytest.mark.knitweb
def test_float_coefficient_is_rejected():
    with pytest.raises(TypeError, match="coefficient"):
        Term(Species.make("H2", {"H": 2}), 1.5)  # type: ignore[arg-type]


@pytest.mark.knitweb
def test_bool_coefficient_is_rejected():
    with pytest.raises(TypeError, match="coefficient"):
        Term(Species.make("H2", {"H": 2}), True)  # type: ignore[arg-type]


@pytest.mark.knitweb
def test_float_kinetics_metadata_is_rejected():
    h2 = Species.make("H2", {"H": 2})
    o2 = Species.make("O2", {"O": 2})
    h2o = Species.make("H2O", {"H": 2, "O": 1})
    with pytest.raises(TypeError, match="kinetic"):
        Reaction(
            reactants=(Term(h2, 2), Term(o2, 1)),
            products=(Term(h2o, 2),),
            kinetics=(("pre_exponential_milli", 5000.5),),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Cross-implementation CID byte-vector (#210 sign-off reference)
# ---------------------------------------------------------------------------
# A fixed (test-only) author key makes the whole reaction record deterministic, so
# the canonical-CBOR bytes and CIDv1 below are a FROZEN known-answer vector: any
# other implementation of this schema (e.g. molgang's PHP/Python reaction emitter)
# must reproduce these EXACT bytes and this EXACT CID for "2 H2 + O2 -> 2 H2O"
# signed by this author, or the content-addressed Web would fork. One vector pins
# the entire cross-impl surface at once — canonical bytewise key ordering, minimal
# integer encoding, array framing, and the dag-cbor/sha2-256/multibase-base32 CID.
_VECTOR_AUTHOR_PRIV = "11" * 32  # TEST KEY ONLY — never a real signing key
_VECTOR_AUTHOR_ADDR = "pls1acgjmtdc45sccnrjuokpyucp6edu5yidvm"
_VECTOR_RECORD = {
    "kind": "reaction-knowledge",
    "equation": "2 H2 + O2 -> 2 H2O",
    "reactants": [
        {"species": "H2", "coeff": 2, "composition": [["H", 2]], "charge": 0},
        {"species": "O2", "coeff": 1, "composition": [["O", 2]], "charge": 0},
    ],
    "products": [
        {"species": "H2O", "coeff": 2, "composition": [["H", 2], ["O", 1]], "charge": 0},
    ],
    "author": _VECTOR_AUTHOR_ADDR,
    "balanced": True,
}
_VECTOR_ENCODE_HEX = (
    "a6646b696e64727265616374696f6e2d6b6e6f776c6564676566617574686f72782"
    "6706c73316163676a6d74646334357363636e726a756f6b70797563703665647535"
    "796964766d6862616c616e636564f5686571756174696f6e72322048322"
    "02b204f32202d3e20322048324f6870726f647563747381a465636f65666602666"
    "368617267650067737065636965736348324f6b636f6d706f736974696f6e82826"
    "1480282614f01697265616374616e747382a465636f65666602666368617267650"
    "067737065636965736248326b636f6d706f736974696f6e8182614802a465636f6"
    "566660166636861726765006773706563696573624f326b636f6d706f736974696"
    "f6e8182614f02"
)
_VECTOR_CID = "bafyreifkpbmrhaypnp7dr6qeytxzdbjp3zork6fi346b33ceysq5e7totu"


@pytest.mark.knitweb
def test_cross_impl_cid_byte_vector_is_stable():
    """Frozen known-answer vector for the #210 cross-implementation CID sign-off."""
    # 1. The fixed author key is deterministic (no RNG on the signed path).
    assert crypto.address(crypto.public_from_private(_VECTOR_AUTHOR_PRIV)) == _VECTOR_AUTHOR_ADDR
    # 2. The reference impl emits EXACTLY the golden record shape...
    record = ChemistryKnitweb(_VECTOR_AUTHOR_PRIV).to_record(_water_synthesis())
    assert record == _VECTOR_RECORD
    # 3. ...which canonical-encodes to the EXACT golden bytes...
    assert canonical.encode(record).hex() == _VECTOR_ENCODE_HEX
    # 4. ...and content-addresses to the EXACT golden CIDv1.
    assert canonical.cid(record) == _VECTOR_CID
    # 5. The golden bytes are truly canonical — they re-decode to the same record.
    assert canonical.decode(bytes.fromhex(_VECTOR_ENCODE_HEX)) == _VECTOR_RECORD
