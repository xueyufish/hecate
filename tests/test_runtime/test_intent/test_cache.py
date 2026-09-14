"""Tests for the intent decision cache (6.23)."""

from __future__ import annotations

import time
import uuid

from hecate.runtime.intent.cache import (
    CachedDecision,
    IntentDecisionCache,
    fingerprint,
    normalize_utterance,
)

DECISION = CachedDecision(label="billing", confidence=0.9, gated=False, underlying_source="few_shot")


def test_normalize_utterance_is_case_and_whitespace_insensitive():
    assert normalize_utterance("  Refund   MY Order ") == normalize_utterance("refund my order")


def test_cache_hit_and_expiry():
    cache = IntentDecisionCache(ttl_seconds=0.05)
    key = cache.make_key("refund my order", uuid.uuid4(), "ctx")
    cache.put(key, DECISION)
    assert cache.get(key) == DECISION
    time.sleep(0.06)
    assert cache.get(key) is None


def test_key_binds_evidence_version_and_context():
    version_a = uuid.uuid4()
    version_b = uuid.uuid4()
    assert IntentDecisionCache.make_key("x", version_a, "ctx1") == IntentDecisionCache.make_key("x", version_a, "ctx1")
    assert IntentDecisionCache.make_key("x", version_a, "ctx1") != IntentDecisionCache.make_key("x", version_b, "ctx1")
    assert IntentDecisionCache.make_key("x", version_a, "ctx1") != IntentDecisionCache.make_key("x", version_a, "ctx2")


def test_lru_eviction():
    cache = IntentDecisionCache(max_size=2)
    keys = [f"k{i}" for i in range(3)]
    for key in keys:
        cache.put(key, DECISION)
    assert cache.get(keys[0]) is None  # evicted
    assert cache.get(keys[2]) == DECISION


def test_fingerprint_is_stable_and_input_insensitive():
    assert fingerprint({"b": 1, "a": 2}) == fingerprint({"a": 2, "b": 1})
    assert fingerprint("x") != fingerprint("y")
