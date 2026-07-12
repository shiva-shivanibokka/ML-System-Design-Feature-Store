import fakeredis

from feature_store import online_store as onl


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
