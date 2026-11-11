from unittest.mock import MagicMock, patch

import pytest

from flashcart import cloudrun

INSTANCE = "ritru-fc-dev-01:asia-south1:flashcart-dev-inventory"


def process(code=None):
    value = MagicMock()
    value.poll.return_value = code
    value.returncode = code
    return value


def test_wrong_instance_never_starts_proxy(monkeypatch):
    monkeypatch.setenv("FLASHCART_SQL_INSTANCE", "another-project:region:database")
    with patch.object(cloudrun.subprocess, "Popen") as start:
        with pytest.raises(ValueError):
            cloudrun.run("api")
        start.assert_not_called()


def test_completed_worker_stops_proxy_and_does_not_share_app_secrets(monkeypatch):
    monkeypatch.setenv("FLASHCART_SQL_INSTANCE", INSTANCE)
    monkeypatch.setenv("FLASHCART_API_KEY", "test-secret")
    monkeypatch.setenv("FLASHCART_DATABASE_URL", "test-dsn")
    proxy, worker = process(), process(0)
    with (
        patch.object(cloudrun.subprocess, "Popen", side_effect=[proxy, worker]) as start,
        patch.object(cloudrun.socket, "create_connection", return_value=MagicMock()),
    ):
        assert cloudrun.run("worker") == 0
    assert "--auto-iam-authn" in start.call_args_list[0].args[0]
    assert "FLASHCART_API_KEY" not in start.call_args_list[0].kwargs["env"]
    assert "FLASHCART_DATABASE_URL" not in start.call_args_list[0].kwargs["env"]
    proxy.terminate.assert_called_once()
    worker.terminate.assert_not_called()


def test_proxy_failure_never_starts_application(monkeypatch):
    monkeypatch.setenv("FLASHCART_SQL_INSTANCE", INSTANCE)
    proxy = process(1)
    with patch.object(cloudrun.subprocess, "Popen", return_value=proxy) as start:
        with pytest.raises(RuntimeError, match="before startup"):
            cloudrun.run("api")
    assert start.call_count == 1


def test_proxy_failure_stops_running_application(monkeypatch):
    monkeypatch.setenv("FLASHCART_SQL_INSTANCE", INSTANCE)
    proxy, api = process(), process()
    proxy.poll.side_effect = [None, 1, 1]
    with (
        patch.object(cloudrun.subprocess, "Popen", side_effect=[proxy, api]),
        patch.object(cloudrun.socket, "create_connection", return_value=MagicMock()),
    ):
        with pytest.raises(RuntimeError, match="while application"):
            cloudrun.run("api")
    api.terminate.assert_called_once()


def test_migration_uses_database_password_instead_of_iam_login(monkeypatch):
    monkeypatch.setenv("FLASHCART_SQL_INSTANCE", INSTANCE)
    proxy, migration = process(), process(0)
    with (
        patch.object(cloudrun.subprocess, "Popen", side_effect=[proxy, migration]) as start,
        patch.object(cloudrun.socket, "create_connection", return_value=MagicMock()),
    ):
        assert cloudrun.run("migrate") == 0
    assert "--auto-iam-authn" not in start.call_args_list[0].args[0]
    assert start.call_args_list[1].args[0][-1] == "--bootstrap-dev"
