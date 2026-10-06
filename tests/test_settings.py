"""User-provided configuration and secrets.

Two things are being pinned here. One is the override layer's arithmetic: what
wins, and that a bad value cannot be installed. The other is that a secret never
leaves the process in a readable form — not in a response body, not in a log
line — because the whole point of the intake routes is that someone types a real
key into them.
"""

from __future__ import annotations

import logging

import pytest
from designagent import app as app_module
from designagent import config as config_module
from designagent.config import (
    Settings,
    apply_overrides,
    clear_overrides,
    describe,
    get_settings,
    install_settings,
    mask,
    secret_values,
    sources,
)
from designagent.runtime import changed_fields, needs_rebuild
from pydantic import SecretStr, ValidationError

KEY = "sk-ant-test-0123456789-abcdefghij"
OTHER_KEY = "sk-ant-test-9876543210-zyxwvutsrq"


# --- the override layer ----------------------------------------------------


def test_precedence_env_then_override_then_clear(monkeypatch):
    monkeypatch.setenv("DESIGNAGENT_MODEL", "model-from-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)

    base = get_settings()
    assert base.model == "model-from-env"
    assert base.llm_key == KEY
    assert sources()["model"] == "env"

    applied = apply_overrides({"model": "model-from-user", "orbit_broker_url": "https://b:8443"})
    assert applied.model == "model-from-user"
    assert applied.orbit_broker_url == "https://b:8443"
    # Untouched fields keep coming from the environment.
    assert applied.llm_key == KEY
    assert sources()["model"] == "override"
    assert sources()["orbit_broker_url"] == "override"
    assert sources()["max_tokens"] == "default"

    restored = clear_overrides()
    assert restored.model == "model-from-env"
    assert restored.orbit_broker_url == ""


def test_an_override_can_clear_a_value(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    assert get_settings().llm_available
    assert not apply_overrides({"anthropic_api_key": ""}).llm_available


def test_an_invalid_override_is_rejected_without_installing():
    before = get_settings().fold_backend
    with pytest.raises(ValidationError):
        apply_overrides({"fold_backend": "quantum-oracle"})
    assert get_settings().fold_backend == before


def test_aliased_fields_are_settable_by_field_name():
    """`populate_by_name` carries the override layer; without it these are no-ops."""
    applied = apply_overrides(
        {"orbit_broker_url": "https://h:1", "orbit_broker_token": "tok-0123456789"}
    )
    assert applied.orbit_broker_url == "https://h:1"
    assert applied.orbit_token == "tok-0123456789"


def test_install_settings_is_what_a_pool_worker_calls():
    """The pool initializer. A worker that derived its own would miss overrides."""
    handed = Settings(anthropic_api_key=KEY, model="handed-to-the-worker")
    install_settings(handed)
    assert get_settings() is handed
    assert get_settings().llm_key == KEY


# --- masking ---------------------------------------------------------------


def test_mask_shows_the_shape_not_the_secret():
    assert mask(KEY) == "sk-ant-…ghij"
    assert KEY[7:-4] not in mask(KEY)
    assert mask("") == ""
    # Too short to reveal any of: a 6-character token would be mostly given away.
    assert mask("abc123") == "………"


def test_describe_never_carries_a_secret_value():
    install_settings(Settings(anthropic_api_key=KEY, orbit_broker_token="tok-0123456789"))
    rendered = repr(describe())
    assert KEY not in rendered
    assert "tok-0123456789" not in rendered
    assert describe()["llm"]["anthropic_api_key"]["present"] is True


def test_the_log_filter_scrubs_a_live_secret():
    install_settings(Settings(anthropic_api_key=KEY))
    assert KEY in secret_values()

    record = logging.LogRecord(
        name="x", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="provider rejected request with x-api-key: %s", args=(KEY,), exc_info=None,
    )
    assert app_module._ScrubSecrets().filter(record)
    assert KEY not in record.getMessage()
    assert "[redacted]" in record.getMessage()


# --- what has to restart ---------------------------------------------------


def test_a_pool_visible_change_needs_a_rebuild():
    base = Settings()
    assert needs_rebuild(base, base.model_copy(update={"anthropic_api_key": KEY}))
    assert needs_rebuild(base, base.model_copy(update={"fold_backend": "local"}))
    assert changed_fields(base, base.model_copy(update={"model": "x"})) == {"model"}


def test_an_orbit_only_change_does_not():
    base = Settings()
    assert not needs_rebuild(base, base.model_copy(update={"orbit_broker_url": "https://b:1"}))
    assert not needs_rebuild(base, base.model_copy(update={"orbit_enabled": True}))


# --- the routes ------------------------------------------------------------


def test_get_settings_reports_presence_and_source(client):
    body = client.get("/api/settings").json()
    entry = body["credentials"]["llm"]["anthropic_api_key"]
    assert entry["present"] is False
    assert body["llm_available"] is False
    assert body["overrides"] == []


def test_put_applies_an_orbit_change_in_place(client, monkeypatch):
    """An Orbit credential is read only in this process, so nothing is rebuilt."""
    builds = []

    async def counting_build(settings=None):
        builds.append(settings)
        return client.runtime

    monkeypatch.setattr(app_module, "build_runtime", counting_build)

    response = client.put(
        "/api/settings",
        json={"orbit_broker_url": "https://broker:8443", "orbit_psij_executor": "slurm"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] is True
    assert "pool" not in body["restarted"]
    assert builds == []  # no rebuild
    assert client.runtime.settings.orbit_broker_url == "https://broker:8443"
    # The graph sees it without being recompiled.
    assert client.runtime.deps.settings.orbit_psij_executor == "slurm"


def test_put_a_key_rebuilds_the_pool(client, monkeypatch):
    """A worker's settings are fixed at fork, so a new key needs a new pool."""
    builds = []

    async def counting_build(settings=None):
        builds.append(settings)
        client.runtime.settings = settings
        return client.runtime

    async def no_close():
        return None

    monkeypatch.setattr(app_module, "build_runtime", counting_build)
    monkeypatch.setattr(client.runtime, "close", no_close)

    response = client.put("/api/settings", json={"anthropic_api_key": KEY})
    assert response.status_code == 200, response.text
    assert response.json()["restarted"] == ["pool", "graph"]
    assert len(builds) == 1
    assert builds[0].llm_key == KEY


def test_no_response_ever_contains_the_key(client, monkeypatch):
    async def no_close():
        return None

    async def rebuild(settings=None):
        # What the real one does: the new runtime carries the new settings.
        client.runtime.settings = settings
        return client.runtime

    monkeypatch.setattr(app_module, "build_runtime", rebuild)
    monkeypatch.setattr(client.runtime, "close", no_close)
    assert client.put("/api/settings", json={"anthropic_api_key": KEY}).status_code == 200

    for path in ("/api/settings", "/api/health"):
        text = client.get(path).text
        assert KEY not in text
        assert "ghij" in text  # the masked hint is there instead


def test_put_refuses_while_tasks_are_running(client, monkeypatch):
    monkeypatch.setattr(
        client.runtime.manager,
        "snapshot",
        lambda session_id=None: [{"id": "t-1", "state": "RUNNING"}],
    )
    response = client.put("/api/settings", json={"orbit_enabled": True})
    assert response.status_code == 409
    assert response.json()["running"] == ["t-1"]
    assert client.runtime.settings.orbit_enabled is False

    forced = client.put("/api/settings", json={"orbit_enabled": True, "force": True})
    assert forced.status_code == 200


def test_an_unknown_field_is_refused(client):
    assert client.put("/api/settings", json={"data_dir": "/etc"}).status_code == 422


def test_every_reported_field_is_also_writable(client):
    """`CREDENTIAL_FIELDS` and `SettingsUpdate` must not drift apart.

    Reporting a field the API then refuses is how the `mpnn` group and three
    `orbit` fields ended up readable but not settable: `SettingsUpdate` is
    `extra="forbid"`, so a `PUT` naming one returned 422.
    """
    from designagent.app import SettingsUpdate
    from designagent.config import CREDENTIAL_FIELDS

    reported = {name for fields in CREDENTIAL_FIELDS.values() for name, _ in fields}
    writable = set(SettingsUpdate.model_fields) - {"force"}
    assert not (reported - writable), f"reported but not writable: {sorted(reported - writable)}"


def test_put_applies_the_proteinmpnn_command(client, monkeypatch):
    """The field the UI needs to point at a local CPU install."""

    async def no_build(settings=None):
        raise AssertionError("an mpnn change must not rebuild the pool")

    monkeypatch.setattr(app_module, "build_runtime", no_build)

    response = client.put(
        "/api/settings",
        json={"mpnn_command": "/venv/bin/python /sw/protein_mpnn_run.py",
              "mpnn_sampling_temp": 0.2},
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True
    # The orchestrator reads these off `deps.settings` on every turn.
    assert client.runtime.deps.settings.mpnn_command.endswith("protein_mpnn_run.py")
    assert client.runtime.deps.settings.mpnn_sampling_temp == 0.2


def test_the_staging_ceilings_are_settable(client, monkeypatch):
    """`OrbitInterface` takes them at construction, so this restarts it."""

    async def no_build(settings=None):
        raise AssertionError("an orbit change must not rebuild the pool")

    monkeypatch.setattr(app_module, "build_runtime", no_build)

    response = client.put(
        "/api/settings",
        json={"orbit_artifact_max_bytes": 2_000_000, "orbit_job_gpus": 0},
    )
    assert response.status_code == 200, response.text
    assert client.runtime.settings.orbit_artifact_max_bytes == 2_000_000
    assert client.runtime.settings.orbit_job_gpus == 0


def test_a_non_loopback_bind_requires_a_token(client):
    client.runtime.settings = client.runtime.settings.model_copy(
        update={"bind_host": "0.0.0.0"}
    )
    assert client.put("/api/settings", json={"orbit_enabled": True}).status_code == 403

    client.runtime.settings = client.runtime.settings.model_copy(
        update={"admin_token": SecretStr("shared-secret")}
    )
    assert client.put("/api/settings", json={"orbit_enabled": True}).status_code == 403
    ok = client.put(
        "/api/settings",
        json={"orbit_enabled": True},
        headers={"X-Designagent-Admin": "shared-secret"},
    )
    assert ok.status_code == 200


def test_delete_clears_the_overrides(client):
    client.put("/api/settings", json={"orbit_endpoint": "ep-1"})
    assert client.runtime.settings.orbit_endpoint == "ep-1"
    assert client.get("/api/settings").json()["overrides"] == ["orbit_endpoint"]

    assert client.delete("/api/settings").status_code == 200
    assert client.runtime.settings.orbit_endpoint == ""


def test_test_route_probes_without_storing(client, monkeypatch):
    async def fake_probe(settings=None):
        assert settings.llm_key == OTHER_KEY  # the candidate, not the installed one
        return {"llm": {"state": "rejected", "detail": "the API key was rejected: 401"}}

    monkeypatch.setattr(app_module, "probe_all", fake_probe)
    body = client.post("/api/settings/test", json={"anthropic_api_key": OTHER_KEY}).json()
    assert body["probes"]["llm"]["state"] == "rejected"
    # Nothing was adopted.
    assert client.runtime.settings.llm_available is False
    assert config_module.overrides() == {}


# --- what reaches the chat -------------------------------------------------


def test_internal_llm_calls_are_tagged_nostream(monkeypatch):
    """The classifier's JSON must not be appended to the chat as the reply.

    LangGraph's messages stream emits a whole message on `on_llm_end` whether or
    not the model streamed, so without this tag `app.py` forwards the intent
    classifier's and the round planner's JSON as `token` frames.
    """
    from designagent import llm as llm_module

    built: list[dict] = []

    class FakeChat:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setitem(
        __import__("sys").modules, "langchain_anthropic", _module(ChatAnthropic=FakeChat)
    )
    settings = Settings(anthropic_api_key=KEY)

    llm_module.build_llm(settings)
    assert built[-1]["tags"] == [llm_module.NOSTREAM_TAG]

    llm_module.build_llm(settings, stream=True)
    assert "tags" not in built[-1]


def test_temperature_is_only_sent_when_asked(monkeypatch):
    """claude-sonnet-5-5 rejects a non-default temperature outright."""
    from designagent import llm as llm_module

    built: list[dict] = []

    class FakeChat:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setitem(
        __import__("sys").modules, "langchain_anthropic", _module(ChatAnthropic=FakeChat)
    )
    llm_module.build_llm(Settings(anthropic_api_key=KEY))
    assert "temperature" not in built[-1]

    llm_module.build_llm(Settings(anthropic_api_key=KEY), temperature=0.7)
    assert built[-1]["temperature"] == 0.7


def _module(**attrs):
    import types

    module = types.ModuleType("langchain_anthropic")
    for name, value in attrs.items():
        setattr(module, name, value)
    return module
