"""Live port congestion overlay: operator-reported updates vs demo snapshot.

Covers: set_port_update → overlay reflects values + honest provenance;
untouched ports stay "demo"; clear/reset → back to the static snapshot;
validation (pct 0..100, customs >= 0); base PORTS is never mutated.
"""

import pytest

from app import data, store


@pytest.fixture(autouse=True)
def _clean_port_updates():
    store.clear_port_updates()
    yield
    store.clear_port_updates()


def test_operator_update_overrides_snapshot():
    row = store.set_port_update(
        "aktau",
        congestion_pct=88,
        customs_queue_h=14.5,
        note="очередь на паромной переправе",
    )
    assert row["source"] == "operator-reported"
    assert row["updated_at"]

    live = data.get_ports_live()
    p = live["aktau"]
    assert p["congestion_pct"] == 88
    assert p["customs_queue_h"] == 14.5
    assert p["source"] == "operator-reported"
    assert p["live"] is True
    assert p["updated_at"] == row["updated_at"]
    assert p["node"] == "aktau"  # base fields preserved

    assert data.get_port("aktau") == p


def test_other_ports_stay_demo():
    store.set_port_update("aktau", 88, 14.5)
    live = data.get_ports_live()
    for port_id, base in data.PORTS.items():
        if port_id == "aktau":
            continue
        p = live[port_id]
        assert p["congestion_pct"] == base["congestion_pct"]
        assert p["customs_queue_h"] == base["customs_queue_h"]
        assert p["source"] == "demo"
        assert p["live"] is False
        assert p["updated_at"] is None


def test_clear_returns_to_demo_snapshot():
    store.set_port_update("baku", 90, 12)
    assert data.get_port("baku")["live"] is True
    store.clear_port_updates()
    for port_id, base in data.PORTS.items():
        p = data.get_port(port_id)
        assert p["congestion_pct"] == base["congestion_pct"]
        assert p["customs_queue_h"] == base["customs_queue_h"]
        assert p["source"] == "demo"
        assert p["live"] is False
        assert p["updated_at"] is None


def test_upsert_overwrites_previous_update():
    store.set_port_update("enzeli", 50, 6)
    row = store.set_port_update("enzeli", 75, 9.5, note="повторное сообщение")
    p = data.get_port("enzeli")
    assert p["congestion_pct"] == 75
    assert p["customs_queue_h"] == 9.5
    assert p["updated_at"] == row["updated_at"]
    assert len(store.get_port_updates()) == 1


def test_validation_rejects_bad_values():
    with pytest.raises(ValueError):
        store.set_port_update("aktau", 101, 5)
    with pytest.raises(ValueError):
        store.set_port_update("aktau", -1, 5)
    with pytest.raises(ValueError):
        store.set_port_update("aktau", 50, -0.5)
    assert store.get_port_updates() == {}


def test_get_port_unknown_raises():
    with pytest.raises(KeyError):
        data.get_port("no-such-port")


def test_base_ports_not_mutated():
    before = {k: dict(v) for k, v in data.PORTS.items()}
    store.set_port_update("aktau", 99, 20)
    data.get_ports_live()
    data.get_port("aktau")
    assert data.PORTS == before


def test_reset_demo_clears_updates():
    store.set_port_update("makhachkala", 80, 10)
    store.reset_demo()
    assert store.get_port_updates() == {}
    assert data.get_port("makhachkala")["source"] == "demo"
