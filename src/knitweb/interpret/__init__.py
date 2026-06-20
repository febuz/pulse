"""Interpretation lobe for the knitweb read-path.

This package hosts query-time primitives used by read requests:

* ``retrieve``: deterministic candidate subgraph selection from a shared Web
* ``distill``: bounded relation selection (no model text concatenation)

The modules are intentionally small and pure-Python so they can run in edge/host
contexts without heavy compute dependencies.
"""

from .distill import Selection, distill
from .retrieve import Candidate, CandidateSet, retrieve

__all__ = [
    "Candidate",
    "CandidateSet",
    "Selection",
    "distill",
    "retrieve",
]
