"""Canonical-serialization + dedup proofs for the agent-memory node kinds (#228).

The lens "content-addressed agent memory" epic needs identical agent knowledge to
collapse to a single CID across repositories and tools. These property tests pin:

* each of the four kinds addresses as a CIDv1 over the canonical dag-cbor path;
* identical content authored in two synthetic repos -> identical CID;
* dedup: the same skill/preference text (modulo incidental whitespace and tag
  ordering) resolves to one node CID;
* dedup: ArchitectureNode components (modulo incidental whitespace and component
  ordering/duplicates) resolve to one node CID;
* distinct content -> distinct CID, and same-looking content under a different
  kind -> distinct CID (no kind-confusion);
* byte-stable round-trip through canonical encode/decode and ``node_from_record``;
* required-field / type guards reject empty or non-canonical content.
"""

import pytest

from knitweb.core import canonical
from knitweb.interpret.memory import (
    MEMORY_NODE_KINDS,
    ArchitectureNode,
    PreferenceNode,
    ProjectNode,
    SkillNode,
    node_cid,
    node_from_record,
)

_ALL_KINDS = ("agent-skill", "agent-project", "agent-preference", "agent-architecture")


def _one_of_each() -> list:
    return [
        SkillNode(name="git-rebase", body="reapply commits onto a new base"),
        ProjectNode(name="pulse", goal="best p2p codebase for knitweb", status="active"),
        PreferenceNode(scope="delivery", statement="ship lean, action-first"),
        ArchitectureNode(
            name="event-sourced-core",
            decision="collapse rule impls into one CID-deterministic event log",
            components=("ledger", "fabric"),
        ),
    ]


# -- (1) every kind addresses as a CIDv1 over the canonical path --------------
@pytest.mark.property
def test_every_kind_addresses_as_cidv1_and_is_registered():
    for node in _one_of_each():
        cid = node.cid
        assert cid == canonical.cid(node.to_record())  # property matches function
        assert cid == node_cid(node)
        assert cid.startswith("b")  # CIDv1 base32 lower
        assert node.to_record()["kind"] in _ALL_KINDS
    # the registry covers exactly the four kinds, each mapping to its class
    assert set(MEMORY_NODE_KINDS) == set(_ALL_KINDS)
    assert MEMORY_NODE_KINDS["agent-skill"] is SkillNode


# -- (2) identical content in two synthetic repos -> identical CID ------------
@pytest.mark.property
def test_identical_content_in_two_repos_addresses_identically():
    # "Repo A" and "repo B" each independently record the same skill knowledge.
    repo_a = SkillNode(name="git-rebase", body="reapply commits onto a new base")
    repo_b = SkillNode(name="git-rebase", body="reapply commits onto a new base")
    assert repo_a.cid == repo_b.cid
    # And across every kind: identical inputs -> identical CID.
    for left, right in zip(_one_of_each(), _one_of_each()):
        assert left.cid == right.cid


# -- (3) dedup: same text modulo whitespace / tag order -> one CID ------------
@pytest.mark.property
def test_dedup_is_insensitive_to_whitespace_and_tag_order():
    # Two tool files carrying the same skill, but with incidental whitespace and a
    # different tag ordering (and a duplicate tag) must collapse to one node CID.
    tool_file_1 = SkillNode(
        name="git-rebase",
        body="reapply commits onto a new base",
        tags=("vcs", "git"),
    )
    tool_file_2 = SkillNode(
        name="  git-rebase\n",
        body="\treapply commits onto a new base  ",
        tags=("git", "vcs", "git"),  # reordered + duplicate
    )
    assert tool_file_1.cid == tool_file_2.cid
    # a preference authored with trailing whitespace dedups the same way
    p1 = PreferenceNode(scope="delivery", statement="ship lean, action-first")
    p2 = PreferenceNode(scope=" delivery ", statement="ship lean, action-first\n")
    assert p1.cid == p2.cid


@pytest.mark.property
def test_architecture_components_dedup_and_normalization():
    # ArchitectureNode.components goes through the same _norm_labels (sort + dedupe)
    # as tags: components differing only by whitespace, ordering, and duplicates must
    # collapse to one node CID, so architectural knowledge dedups regardless of input.
    arch1 = ArchitectureNode(
        name="event-sourced-core",
        decision="collapse rule impls into one CID-deterministic event log",
        components=("  ledger ", "fabric"),
    )
    arch2 = ArchitectureNode(
        name="event-sourced-core",
        decision="collapse rule impls into one CID-deterministic event log",
        components=("fabric", "ledger", "ledger"),  # reordered + duplicate + ws
    )
    assert arch1.cid == arch2.cid


# -- (4) distinct content -> distinct CID -------------------------------------
@pytest.mark.property
def test_distinct_content_changes_the_cid():
    base = SkillNode(name="git-rebase", body="reapply commits onto a new base")
    assert base.cid != SkillNode(name="git-merge", body="reapply commits onto a new base").cid
    assert base.cid != SkillNode(name="git-rebase", body="join two histories").cid
    assert base.cid != SkillNode(name="git-rebase", body="reapply commits onto a new base", tags=("vcs",)).cid


# -- (5) same-looking content under a different kind -> distinct CID ----------
@pytest.mark.property
def test_kind_disambiguates_otherwise_identical_records():
    # name/body vs scope/statement differ structurally, but a kind tag must keep
    # even structurally-similar records from colliding.
    skill = SkillNode(name="x", body="y")
    proj = ProjectNode(name="x", goal="y")
    assert skill.cid != proj.cid
    assert skill.to_record()["kind"] != proj.to_record()["kind"]


# -- (6) byte-stable round-trip -----------------------------------------------
@pytest.mark.property
def test_canonical_round_trip_is_byte_stable():
    for node in _one_of_each():
        record = node.to_record()
        # canonical encode/decode round-trips the record unchanged
        assert canonical.decode(canonical.encode(record)) == record
        # reconstructing the node and re-recording reproduces the same record + CID
        rebuilt = node_from_record(record)
        assert rebuilt.to_record() == record
        assert rebuilt.cid == node.cid


@pytest.mark.property
def test_node_from_record_rejects_unknown_kind():
    with pytest.raises(ValueError):
        node_from_record({"kind": "not-a-memory-kind", "name": "x", "body": "y"})
    with pytest.raises(TypeError):
        node_from_record(["not", "a", "dict"])


# -- (7) required-field / type guards -----------------------------------------
@pytest.mark.property
def test_required_fields_reject_empty_or_whitespace_only():
    for bad in (
        SkillNode(name="", body="y"),
        SkillNode(name="x", body="   "),
        ProjectNode(name="x", goal="\n"),
        PreferenceNode(scope="", statement="y"),
        ArchitectureNode(name=" ", decision="d"),
    ):
        with pytest.raises(ValueError):
            bad.to_record()


@pytest.mark.property
def test_non_str_fields_are_rejected():
    with pytest.raises(TypeError):
        SkillNode(name=123, body="y").to_record()  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SkillNode(name="x", body="y", tags=("ok", 7)).to_record()  # type: ignore[list-item]


# -- (8) optional fields use conditional omission (CID stays byte-stable) -----
@pytest.mark.property
def test_optional_fields_are_omitted_when_unset():
    # a tagless skill's record has no "tags" key, so adding the field later to the
    # schema would not perturb existing CIDs.
    rec = SkillNode(name="x", body="y").to_record()
    assert "tags" not in rec
    # an unset project status / empty architecture components are likewise omitted
    assert "status" not in ProjectNode(name="x", goal="y").to_record()
    assert "components" not in ArchitectureNode(name="x", decision="d").to_record()
    # and an unset-status project addresses identically whether status="" or omitted
    assert ProjectNode(name="x", goal="y").cid == ProjectNode(name="x", goal="y", status="").cid
