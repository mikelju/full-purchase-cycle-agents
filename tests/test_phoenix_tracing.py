"""Change 013, C3: local Phoenix tracing is opt-in and never set up inside pytest or `npm run check`."""

import json
import re
import subprocess
import sys

import pytest

from purchase_cycle import config


@pytest.fixture
def opted_in(monkeypatch):
    """Ask for Phoenix tracing outside pytest and npm, with the LangChain instrumentation mocked."""
    calls = []
    monkeypatch.setenv("PHOENIX_TRACING", "true")
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    monkeypatch.delenv("npm_lifecycle_event", raising=False)
    monkeypatch.setattr(config, "_phoenix_provider", None)
    import openinference.instrumentation.langchain as langchain_instrumentation

    class FakeInstrumentor:
        def instrument(self, tracer_provider):
            calls.append(("instrument", tracer_provider))

    monkeypatch.setattr(langchain_instrumentation, "LangChainInstrumentor", FakeInstrumentor)
    return calls


def test_off_without_the_variable(opted_in, monkeypatch):
    calls = opted_in
    monkeypatch.delenv("PHOENIX_TRACING")
    assert config.configure_phoenix_tracing() is None
    monkeypatch.setenv("PHOENIX_TRACING", "false")
    config.configure_tracing("replay")
    assert calls == [] and config._phoenix_provider is None


def check_scripts_that_run_python():
    """Every npm script `npm run check` reaches, following `npm run <name>` recursively, that runs Python or uv."""
    scripts = json.loads((config.ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    reached, pending = set(), ["check"]
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending += re.findall(r"npm run (?:--\S+ )*([\w:.-]+)", scripts[name])
    return sorted(name for name in reached if re.search(r"\b(uv|uvx|python)\b", scripts[name]))


def test_every_python_step_of_npm_run_check_is_found():
    assert {"check:python", "eval"} <= set(check_scripts_that_run_python())


@pytest.mark.parametrize("script", sorted({"check", "check:puerta", *check_scripts_that_run_python()}))
def test_off_inside_npm_run_check_even_when_asked_for(opted_in, monkeypatch, script):
    calls = opted_in
    monkeypatch.setenv("npm_lifecycle_event", script)
    assert config.configure_phoenix_tracing() is None
    assert calls == []


def test_off_inside_pytest_even_when_asked_for(opted_in, monkeypatch):
    calls = opted_in
    monkeypatch.setenv("PYTEST_VERSION", "9")
    config.configure_tracing("replay")
    assert calls == []


def test_on_when_opted_in_sends_to_the_local_server_once(opted_in):
    from phoenix.otel import PROJECT_NAME, BatchSpanProcessor

    calls = opted_in
    provider = config.configure_phoenix_tracing()
    config.configure_tracing("live")  # a second set-up is a no-op
    assert calls == [("instrument", provider)]
    assert provider.resource.attributes[PROJECT_NAME] == "purchase-cycle"
    # One batched processor, so a run never blocks on each span; a short timeout caps the flush at exit.
    (processor,) = provider._active_span_processor._span_processors
    assert isinstance(processor, BatchSpanProcessor)
    exporter = processor._batch_processor._exporter
    assert exporter._endpoint == "http://127.0.0.1:6006/v1/traces"
    assert exporter._timeout == 1
    provider.shutdown()


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


def test_launcher_keeps_the_server_on_loopback_with_the_assistant_off():
    from purchase_cycle import phoenix_server

    env = phoenix_server.SERVER_ENV
    assert env["PHOENIX_HOST"] == "127.0.0.1"
    assert env["PHOENIX_WORKING_DIR"] == str(config.ROOT / "data" / "phoenix")
    assert env["PHOENIX_TELEMETRY_ENABLED"] == "false"
    for name in (
        "PHOENIX_DISABLE_AGENT_ASSISTANT",
        "PHOENIX_AGENTS_DISABLE_BASH",
        "PHOENIX_AGENTS_DISABLE_WEB_ACCESS",
        "PHOENIX_AGENTS_DISABLE_GITHUB",
    ):
        assert env[name] == "true"
    # The MCP server answers any browser origin without authentication; code sandboxes run code.
    assert env["PHOENIX_ENABLE_MCP_SERVER"] == "false"
    assert env["PHOENIX_ALLOWED_SANDBOX_PROVIDERS"] == "NONE"
    # Without trusted origins Phoenix does not check `Origin`, so any web page could post to the API.
    assert env["PHOENIX_CSRF_TRUSTED_ORIGINS"] == "http://127.0.0.1:6006,http://localhost:6006"


def test_launcher_disables_the_grpc_receiver_or_refuses_to_start():
    from purchase_cycle import phoenix_server

    class FakeGrpcServer:
        def __init__(self, enqueue_span, port, disabled=False):
            self.disabled = disabled

    phoenix_server.disable_grpc_receiver(FakeGrpcServer)
    assert FakeGrpcServer(None, port=4317, disabled=False).disabled is True

    class ChangedGrpcServer:
        def __init__(self, enqueue_span, port):
            pass

    with pytest.raises(SystemExit):
        phoenix_server.disable_grpc_receiver(ChangedGrpcServer)
