"""Domain knitwebs — plugins that emit signed, attestable knowledge into the Web.

A *domain knitweb* turns domain facts (a chemical reaction, a financial statement, a
supply-chain event) into content-addressed, ECDSA-signed records that weave into
the fabric and validate at read time. Knitwebs are **plugins, never core**: they
depend only on the already-stable primitives (canonical, crypto, fabric.attest,
fabric.web) and add no new trust assumptions to the settlement path.

The contract every knitweb upholds:

  * emit only **physically/economically sound** records — each knitweb gates on a
    domain invariant before signing (so a peer can trust a signed record's *shape*,
    and re-check its *soundness* deterministically);
  * keep the signed record **integer-only** (no floats on the canonical path);
  * sign with the author's key so authorship is verifiable (``fabric.attest``).
"""
