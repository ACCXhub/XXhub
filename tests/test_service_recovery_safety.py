import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from autody.cli import _install_service_control_middleware
from autody.failures import failure_detail


def test_service_shutdown_requires_the_private_tray_token(monkeypatch):
    app = FastAPI()
    stopped = threading.Event()
    monkeypatch.setenv("AUTODY_SERVICE_CONTROL_TOKEN", "secret-token")
    _install_service_control_middleware(app, stopped.set)
    client = TestClient(app)

    assert client.post("/api/service-shutdown").status_code == 403
    assert client.post(
        "/api/service-shutdown",
        headers={"X-AutoDy-Control-Token": "wrong-token"},
    ).status_code == 403
    response = client.post(
        "/api/service-shutdown",
        headers={"X-AutoDy-Control-Token": "secret-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"stopping": True}
    assert stopped.wait(1.0) is True


def test_recovered_binding_changes_only_current_action_not_historical_reason():
    historical = failure_detail(
        "binding_stale",
        stage="target_binding_resolved",
        send_attempts=0,
        binding_valid=False,
        account_scope_matches=True,
    )
    assert historical.safe_retry_available is False
    assert historical.model_dump()["suggested_action"] == "reassociate"

    current = historical.model_copy(
        update={"binding_valid": True, "account_scope_matches": True}
    )
    payload = current.model_dump(mode="json")

    assert current.reason_code == "binding_stale"
    assert current.user_summary_zh == historical.user_summary_zh
    assert payload["safe_retry_available"] is True
    assert payload["suggested_action"] == "retry"
    assert payload["suggested_action_zh"] == "仅重试此目标"


def test_uncertain_or_sent_failure_never_becomes_retryable_after_rebinding():
    uncertain = failure_detail(
        "confirmation_failed_uncertain",
        stage="confirmation_observed",
        send_attempts=1,
        binding_valid=True,
        account_scope_matches=True,
    )

    assert uncertain.safe_retry_available is False
    assert uncertain.model_dump()["suggested_action"] == "details"
