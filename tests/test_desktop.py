"""Desktop ownership must not interrupt a service started elsewhere."""

import socket
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest

from bookanalyst.desktop import launch
from bookanalyst.service import WorkspaceService, ServiceBusy, find_service, wait_for_service


def test_service_lifecycle_and_single_workspace(tmp_path):
    with WorkspaceService(tmp_path, 0) as service:
        thread = threading.Thread(target=service.run)
        thread.start()
        try:
            assert wait_for_service(tmp_path, 5) == service.url
            with httpx.Client(trust_env=False) as client:
                assert client.get(service.url + "/").status_code == 200
                assert client.get(service.url + "/api/health").json()["workspace"] == str(tmp_path.resolve())
            with pytest.raises(ServiceBusy):
                with WorkspaceService(tmp_path, 0):
                    pytest.fail("A second service must not recover the first service's jobs")
            assert find_service(tmp_path) == service.url
        finally:
            service.server.should_exit = True
            thread.join(10)
        assert not thread.is_alive()
    assert find_service(tmp_path) is None
    assert not (tmp_path / ".bookanalyst/service.json").exists()


def test_occupied_port_does_not_initialize_store(tmp_path):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with pytest.raises(OSError):
            with WorkspaceService(tmp_path, occupied.getsockname()[1]):
                pass
    assert not (tmp_path / ".bookanalyst/settings").exists()
    with WorkspaceService(tmp_path, 0):
        pass  # A failed launch must release the workspace lock.


class Event:
    def __iadd__(self, callback):
        self.callback = callback
        return self


def fake_webview(monkeypatch, check):
    window = SimpleNamespace(events=SimpleNamespace(closing=Event()))
    view = SimpleNamespace(settings={})

    def create_window(title, url, **kwargs):
        window.url = url
        return window

    view.create_window = create_window
    view.start = lambda **kwargs: check(window, view)
    monkeypatch.setitem(sys.modules, "webview", view)


def test_desktop_reuses_service_without_stopping_it(tmp_path, monkeypatch):
    with WorkspaceService(tmp_path, 0) as service:
        thread = threading.Thread(target=service.run)
        thread.start()
        try:
            wait_for_service(tmp_path, 5)

            def check(window, view):
                assert window.url == service.url + "/?desktop=1"
                assert window.events.closing.callback() is True
                assert view.settings["ALLOW_DOWNLOADS"] is True

            fake_webview(monkeypatch, check)
            launch(tmp_path, 0)
            assert thread.is_alive()
            assert find_service(tmp_path) == service.url
        finally:
            service.server.should_exit = True
            thread.join(10)


def test_desktop_stops_owned_service_when_window_fails(tmp_path, monkeypatch):
    def fail(window, view):
        assert find_service(tmp_path)
        raise RuntimeError("window failed")

    fake_webview(monkeypatch, fail)
    with pytest.raises(RuntimeError, match="window failed"):
        launch(tmp_path, 0)
    assert find_service(tmp_path) is None
    with WorkspaceService(tmp_path, 0):
        pass


def test_desktop_uses_free_port_when_another_program_is_listening(tmp_path, monkeypatch):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]

        def check(window, view):
            url = find_service(tmp_path)
            assert url and url != f"http://127.0.0.1:{port}"
            assert window.url == url + "/?desktop=1"

        fake_webview(monkeypatch, check)
        launch(tmp_path, port)
    assert find_service(tmp_path) is None
