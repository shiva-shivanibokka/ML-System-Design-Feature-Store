import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from feature_store import online_store as onl


class _DeadRedis:
    """Every call fails the way an unreachable host does."""

    def __getattr__(self, _name):
        def _boom(*_args, **_kwargs):
            raise RedisConnectionError("Name or service not known")

        return _boom


def test_write_then_read_roundtrip(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(onl, "get_redis_client", lambda: fake)
    onl.write_entity(1, {"txn_count_7d": 3.0, "plan_encoded": 2.0})
    got = onl.get_entity(1)
    assert got["txn_count_7d"] == 3.0 and got["plan_encoded"] == 2.0
    assert onl.get_entity(999) is None


def test_online_store_size_uses_maintained_index_not_scan(monkeypatch):
    """get_online_store_size() must reflect writes/deletes via the maintained
    SET index (SCARD), not a full keyspace SCAN."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(onl, "get_redis_client", lambda: fake)

    onl.write_entity(1, {"txn_count_7d": 1.0})
    onl.write_entity(2, {"txn_count_7d": 2.0})
    onl.write_entities_pipeline([(3, {"txn_count_7d": 3.0})])
    assert onl.get_online_store_size() == 3

    onl.delete_entity(2)
    assert onl.get_online_store_size() == 2


def test_reads_degrade_to_miss_when_online_store_is_unreachable(monkeypatch):
    """An unreachable Redis must look like a cache miss, not raise.

    Serving already handles a miss by falling through to the DuckDB on-demand
    path. When the hosted Valkey instance was deleted, these calls raised
    ConnectionError instead — which is not a miss — so the documented fallback
    never ran and every serving request returned 500 while a complete offline
    store sat there able to answer them.
    """
    monkeypatch.setattr(onl, "get_redis_client", _DeadRedis)

    assert onl.get_entity(1) is None
    assert onl.get_entities_batch([1, 2, 3]) == {1: None, 2: None, 3: None}
    assert onl.get_online_store_size() == 0


def test_writes_still_fail_loudly_when_online_store_is_unreachable(monkeypatch):
    """Writes must NOT be swallowed.

    A materialization run that reports success while storing nothing empties the
    online store silently; the first symptom is a cache hit rate drifting to
    zero with no error anywhere.
    """
    monkeypatch.setattr(onl, "get_redis_client", _DeadRedis)

    with pytest.raises(RedisConnectionError):
        onl.write_entity(1, {"txn_count_7d": 1.0})
    with pytest.raises(RedisConnectionError):
        onl.write_entities_pipeline([(1, {"txn_count_7d": 1.0})])
