"""Proof for the X.com-style chat GUI (scripts/chat_gui.py).

Exercises the `ChatService` end to end with the deterministic fallback lens (no API
key required): a question is metered in PLS via a real Knit to the spider, the Q&A is
woven into the fabric, and the timeline view reflects it. Also locks the
insufficient-funds guard.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# scripts/ is not an installed package — load the module by path.
_CHAT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "chat_gui.py"
_spec = importlib.util.spec_from_file_location("knitweb_chat_gui", _CHAT_PATH)
chat_gui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chat_gui)


def _service(ask_cost=1):
    # Force the fallback lens regardless of the ambient environment so the proof is
    # deterministic and never makes a network call.
    lens = chat_gui.KnitwebChatLens()
    lens._tried = True  # short-circuit _client_or_none → returns None → fallback
    lens._client = None
    return chat_gui.ChatService(ask_cost=ask_cost, lens=lens)


def test_ask_meters_pls_and_weaves_into_fabric():
    svc = _service(ask_cost=2)
    start = svc.me("alice")["pulses"]
    spider_start = svc.me(svc.spider)["pulses"]

    res = svc.ask("alice", "What is a Fiber?")

    # An answer was produced (fallback engine, but a real answer string).
    assert res["answer"]
    assert res["model"] == "knitweb-fallback"
    assert res["cid"]

    # PLS moved from asker to the answering spider — a real signed Knit.
    assert res["pulses"] == start - 2
    assert svc.me("alice")["pulses"] == start - 2
    assert svc.me(svc.spider)["pulses"] == spider_start + 2

    # The Q&A is woven into the shared web and shows up on the timeline.
    assert res["nodes"] > 0
    feed = svc.feed()
    assert any(p["text"] == "What is a Fiber?" and p["by"] == "alice" for p in feed)
    assert svc.stats()["pulses"] == 1


def test_feed_is_newest_first_and_stats_track_count():
    svc = _service(ask_cost=0)  # free asks → focus on ordering/state
    svc.ask("bob", "first question")
    svc.ask("bob", "second question")
    feed = svc.feed()
    assert [p["text"] for p in feed][:2] == ["second question", "first question"]
    assert svc.stats()["pulses"] == 2


def test_insufficient_pulses_is_rejected_and_nothing_is_woven():
    svc = _service(ask_cost=10_000)  # far above the faucet seed
    nodes_before = svc.stats()["nodes"]
    with pytest.raises(chat_gui.InsufficientPulses):
        svc.ask("carol", "can I ask for free?")
    # No charge, no weave on the failed ask.
    assert svc.stats()["nodes"] == nodes_before
    assert svc.stats()["pulses"] == 0


def test_empty_question_is_rejected():
    svc = _service()
    with pytest.raises(ValueError):
        svc.ask("dave", "   ")
