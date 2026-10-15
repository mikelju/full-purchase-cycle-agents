"""Change 013, C3: local Phoenix tracing is opt-in and never set up inside pytest or `npm run check`."""

import subprocess
import sys

import pytest

from purchase_cycle import config


@pytest.fixture
def opted_in(monkeypatch):
    """Ask for Phoenix tracing outside pytest and npm, with the register call mocked."""
    calls = []
    monkeypatch.setenv("PHOENIX_TRACING", "true")
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    monkeypatch.delenv("npm_lifecycle_event", raising=False)
    monkeypatch.setattr(config, "_phoenix_provider", None)
    import openinference.instrumentation.langchain as langchain_instrumentation
    import phoenix.otel

    provider = object()

    def fake_register(**kwargs):
        calls.append(("register", kwargs))
        return provider

    class FakeInstrumentor:
        def instrument(self, tracer_provider):
            calls.append(("instrument", tracer_provider))

    monkeypatch.setattr(phoenix.otel, "register", fake_register)
    monkeypatch.setattr(langchain_instrumentation, "LangChainInstrumentor", FakeInstrumentor)
    return calls, provider


def test_off_without_the_variable(opted_in, monkeypatch):
    calls, _ = opted_in
    monkeypatch.delenv("PHOENIX_TRACING")
    assert config.configure_phoenix_tracing() is None
    monkeypatch.setenv("PHOENIX_TRACING", "false")
    config.configure_tracing("replay")
    assert calls == []


@pytest.mark.parametrize("script", ["check", "check:puerta", "check:python", "eval"])
def test_off_inside_npm_run_check_even_when_asked_for(opted_in, monkeypatch, script):
    calls, _ = opted_in
    monkeypatch.setenv("npm_lifecycle_event", script)
    assert config.configure_phoenix_tracing() is None
    assert calls == []


def test_off_inside_pytest_even_when_asked_for(opted_in, monkeypatch):
    calls, _ = opted_in
    monkeypatch.setenv("PYTEST_VERSION", "9")
    config.configure_tracing("replay")
    assert calls == []


def test_on_when_opted_in_sends_to_the_local_server_once(opted_in):
    calls, provider = opted_in
    assert config.configure_phoenix_tracing() is provider
    config.configure_tracing("live")  # a second set-up is a no-op
    assert calls == [
        (
            "register",
            {
                "project_name": "purchase-cycle",
                "endpoint": "http://127.0.0.1:6006/v1/traces",
                "batch": False,
                "verbose": False,
            },
        ),
        ("instrument", provider),
    ]


def test_nothing_is_imported_without_the_variable():
    # A fresh interpreter with the variable unset must not load Phoenix or OpenTelemetry.
    code = (
        "import os, sys; os.environ.pop('PHOENIX_TRACING', None); "
        "from purchase_cycle import config; config.configure_tracing('replay'); "
        "loaded = [m for m in sys.modules if m.split('.')[0] in ('phoenix', 'openinference', 'opentelemetry')]; "
        "print(loaded)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
