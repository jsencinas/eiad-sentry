"""The viewer must never listen on every interface unless explicitly asked."""

import subprocess

from sentry import web


class Result:
    def __init__(self, stdout):
        self.stdout = stdout


def fake_run(stdout=None, error=None):
    def run(*args, **kwargs):
        if error:
            raise error
        return Result(stdout)

    return run


def test_uses_tailscale_address(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run("100.74.216.50\n"))
    assert web.resolve_host(None) == "100.74.216.50"


def test_falls_back_to_localhost_without_tailscale(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(error=FileNotFoundError()))
    assert web.resolve_host(None) == "127.0.0.1"


def test_ignores_non_tailscale_addresses(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run("10.43.50.144\n"))
    assert web.resolve_host(None) == "127.0.0.1"


def test_explicit_host_wins(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run("100.74.216.50\n"))
    assert web.resolve_host("0.0.0.0") == "0.0.0.0"
