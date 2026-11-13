import importlib

import pytest

from tests.webapp.test_hub_metrics_access import _operations_client


@pytest.mark.parametrize("path", [
    "/operaciones/metricas", "/admin/activity", "/admin/activity/trend",
    "/admin/activity/stories", "/admin/activity/tree-runs",
])
@pytest.mark.parametrize("htmx", [False, True])
def test_operations_membership_never_exposes_metrics_data(monkeypatch, path, htmx):
    client = _operations_client(monkeypatch, "ops@example.com", operations_allowed="ops@example.com", metrics_allowed="metrics@example.com", enabled_verticals="operations")
    main = importlib.import_module("webapp.main")
    def forbidden_read(*args, **kwargs):
        pytest.fail("An unauthorized request must not read or aggregate metrics")
    monkeypatch.setattr(main, "_activity_dashboard_context", forbidden_read)
    monkeypatch.setattr(main.activity_analytics, "build_summary", forbidden_read)
    response = client.get(path, headers={"HX-Request": "true"} if htmx else {})
    assert response.status_code == 403
    assert response.json() == {"detail": "No autorizado para Usage Metrics"}


@pytest.mark.parametrize("htmx", [False, True])
def test_disabled_operations_metrics_gate_precedes_data_read(monkeypatch, htmx):
    client = _operations_client(monkeypatch, "ops@example.com", operations_allowed="ops@example.com", metrics_allowed="ops@example.com", enabled_verticals="copilot")
    main = importlib.import_module("webapp.main")
    monkeypatch.setattr(main, "_activity_dashboard_context", lambda *a, **k: pytest.fail("Disabled surface must not read metrics"))
    response = client.get("/operaciones/metricas", headers={"HX-Request": "true"} if htmx else {})
    assert response.status_code == 403
    assert response.json() == {"detail": "Usage Metrics no está habilitado para Operations"}
