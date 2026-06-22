"""Interpretation lobe for the knitweb read-path.

This package hosts query-time primitives used by read requests:

* ``retrieve``: deterministic candidate subgraph selection from a shared Web
* ``distill``: bounded relation selection (no model text concatenation)
* ``memory``: content-addressed agent-memory node kinds (skill/project/preference/
  architecture) that collapse identical knowledge to one CID across repos

The modules are intentionally small and pure-Python so they can run in edge/host
contexts without heavy compute dependencies.
"""

from .distill import Selection, distill
from .memory import (
    ArchitectureNode,
    MemoryNode,
    PreferenceNode,
    ProjectNode,
    SkillNode,
    node_cid,
    node_from_record,
)
from .retrieve import Candidate, CandidateSet, retrieve

__all__ = [
    "ArchitectureNode",
    "Candidate",
    "CandidateSet",
    "MemoryNode",
    "PreferenceNode",
    "ProjectNode",
    "Selection",
    "SkillNode",
    "distill",
    "node_cid",
    "node_from_record",
    "retrieve",
]
