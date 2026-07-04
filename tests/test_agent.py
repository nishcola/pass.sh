import base64
import threading
import time

import pytest

from passsh import agent


# --- pure message-handler tests (no sockets) ---


def test_set_key_then_get_key_round_trip():
    session = agent._Session()
    key = b"\x01" * 32
    kdf_params = {"algorithm": "argon2id"}

    set_response = agent._handle_message(
        session,
        {"op": "set_key", "key": base64.b64encode(key).decode(), "kdf_params": kdf_params},
        idle_timeout=300.0,
    )
    assert set_response == {"ok": True}

    get_response = agent._handle_message(session, {"op": "get_key"}, idle_timeout=300.0)
    assert get_response["ok"] is True
    assert base64.b64decode(get_response["key"]) == key
    assert get_response["kdf_params"] == kdf_params


def test_get_key_without_prior_set_is_locked():
    session = agent._Session()
    response = agent._handle_message(session, {"op": "get_key"}, idle_timeout=300.0)
    assert response == {"ok": False, "reason": "locked"}


def test_get_key_after_idle_timeout_is_locked():
    session = agent._Session()
    agent._handle_message(
        session,
        {"op": "set_key", "key": base64.b64encode(b"k" * 32).decode(), "kdf_params": {}},
        idle_timeout=300.0,
    )
    session.last_activity -= 1000  # simulate the past without sleeping in the test

    response = agent._handle_message(session, {"op": "get_key"}, idle_timeout=300.0)
    assert response == {"ok": False, "reason": "locked"}
    assert session.key is None  # expiry wipes the cached key


def test_get_key_refreshes_activity_timer():
    session = agent._Session()
    agent._handle_message(
        session,
        {"op": "set_key", "key": base64.b64encode(b"k" * 32).decode(), "kdf_params": {}},
        idle_timeout=300.0,
    )
    stale_time = session.last_activity - 250
    session.last_activity = stale_time

    agent._handle_message(session, {"op": "get_key"}, idle_timeout=300.0)
    assert session.last_activity > stale_time


def test_lock_wipes_key():
    session = agent._Session()
    agent._handle_message(
        session,
        {"op": "set_key", "key": base64.b64encode(b"k" * 32).decode(), "kdf_params": {}},
        idle_timeout=300.0,
    )
    response = agent._handle_message(session, {"op": "lock"}, idle_timeout=300.0)
    assert response == {"ok": True}
    assert session.key is None


def test_unknown_op():
    session = agent._Session()
    response = agent._handle_message(session, {"op": "bogus"}, idle_timeout=300.0)
    assert response == {"ok": False, "reason": "unknown op"}


# --- real socket integration test (agent runs in a thread, not a subprocess) ---


@pytest.fixture
def running_agent(tmp_path):
    vault_path = tmp_path / "vault.json"
    idle_timeout = 0.3
    thread = threading.Thread(target=agent._serve, args=(vault_path, idle_timeout), daemon=True)
    thread.start()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not agent._socket_path(vault_path).exists():
        time.sleep(0.01)

    yield vault_path

    agent.lock(vault_path)
    thread.join(timeout=3.0)


def test_start_session_and_get_cached_key_over_real_socket(running_agent):
    vault_path = running_agent
    key = b"\x42" * 32
    kdf_params = {"algorithm": "argon2id", "time_cost": 3}

    agent.start_session(vault_path, key, kdf_params)

    cached = agent.get_cached_key(vault_path)
    assert cached is not None
    cached_key, cached_kdf_params = cached
    assert cached_key == key
    assert cached_kdf_params == kdf_params


def test_session_expires_after_idle_timeout(running_agent):
    vault_path = running_agent
    agent.start_session(vault_path, b"\x42" * 32, {"algorithm": "argon2id"})

    assert agent.get_cached_key(vault_path) is not None
    time.sleep(0.6)  # past the 0.3s idle_timeout configured for this fixture
    assert agent.get_cached_key(vault_path) is None


def test_get_cached_key_with_no_agent_running(tmp_path):
    vault_path = tmp_path / "no-agent-vault.json"
    assert agent.get_cached_key(vault_path) is None
