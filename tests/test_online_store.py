import fakeredis
from feature_store import online_store as onl


def test_write_then_read_roundtrip(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(onl, "get_redis_client", lambda: fake)
    onl.write_entity(1, {"txn_count_7d": 3.0, "plan_encoded": 2.0})
    got = onl.get_entity(1)
    assert got["txn_count_7d"] == 3.0 and got["plan_encoded"] == 2.0
    assert onl.get_entity(999) is None
