"""knitweb.gateway.App — the turnkey app layer (identity, economy, persistent web, provenance)."""
from knitweb.gateway import App


def test_actor_is_stable_and_faucet_seeded():
    a = App().actor("device:abc")
    assert a["pulses"] == 50 and a["address"].startswith("pls1")
    assert App().actor("device:abc")["address"] == a["address"]   # same id → same wallet


def test_transfer_moves_pulses():
    app = App()
    r = app.transfer("u1", "u2", 10)
    assert r["from"] == 40 and r["to"] == 60 and r["knit"]


def test_attest_and_link_grow_a_persistent_web(tmp_path):
    f = str(tmp_path / "web.json")
    app = App("molgang", store=f)
    app.attest("u1", {"kind": "reaction", "formula": "H2O"})
    app.link("V2O5", "vanadium pentoxide", "is", weight=3)
    st = app.web_state()
    assert st["nodes"] >= 3 and st["edges"] >= 1
    app2 = App("molgang", store=f)                 # fresh instance rebuilds from the store
    assert app2.web_state()["nodes"] == st["nodes"]
    assert app2.web_state()["state_root"] == st["state_root"]


def test_balances_persist_across_instances(tmp_path):
    f = str(tmp_path / "e.json")
    a = App(store=f); a.actor("x"); a.transfer("x", "y", 10)
    b = App(store=f)
    assert b.balance("x") == 40 and b.balance("y") == 60


def test_validate_quorum():
    assert App().validate(["confirm", "confirm", "confirm"])["released"] is True
    assert App().validate(["mismatch", "mismatch", "mismatch"])["released"] is False


def test_anchor_provenance():
    app = App(); app.attest("u", {"x": 1})
    pr = app.anchor()
    assert pr["ual"].startswith("did:dkg:knitweb/") and pr["verified"]
