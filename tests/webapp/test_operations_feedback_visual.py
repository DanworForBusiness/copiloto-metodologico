"""Severity is driven by the outcome, including independent workflow/mail results."""
from html.parser import HTMLParser

import pytest

from tests.webapp.test_hub_metrics_access import _operations_client
from webapp.templating import templates


class Notices(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.notices = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if "callout--feedback" in classes:
            self.notices.append(set(classes))


@pytest.mark.parametrize("state,tone", [
    ("saved", "success"), ("deleted", "success"), ("conflict", "punch"),
    ("uncertain", "punch"), ("error", "warn"), ("invalid", "warn"), ("auth", "warn"),
])
def test_workflow_severity_follows_semantics_not_http_success(state, tone):
    html = templates.env.get_template("partials/solicitudes_feedback.html").render(
        state=state, message="Resultado", item_id="1", rejection=False,
    )
    assert "callout--" + tone in Notices(html).notices[0]


@pytest.mark.parametrize("state,tone", [
    ("accepted", "success"), ("failed", "warn"), ("config_incomplete", "warn"),
    ("error", "warn"), ("uncertain", "punch"), ("no_recipients", "punch"),
    ("disabled", "punch"), ("pending", "punch"), ("unknown", "punch"),
])
def test_confirmed_workflow_and_notification_have_separate_severities(state, tone):
    html = templates.env.get_template("partials/solicitudes_feedback.html").render(
        state="saved", message="Rechazo confirmado", item_id="1", rejection=True,
        notification_state=state, notification_message="Situación del correo",
    )
    notices = Notices(html).notices
    assert len(notices) == 2
    assert "callout--success" in notices[0]
    assert "callout--" + tone in notices[1]
    assert f'data-solicitudes-notification="{state}"' in html


@pytest.mark.parametrize("state,tone", [("saved", "success"), ("auth", "warn"), ("uncertain", "punch"), ("review", "punch")])
def test_creation_feedback_severity(state, tone):
    html = templates.env.get_template("partials/solicitudes_create_feedback.html").render(state=state, message="Resultado")
    assert "callout--" + tone in Notices(html).notices[0]


def test_missing_token_in_assignments_is_a_styled_error_without_graph(monkeypatch):
    from webapp.routers import staffing
    monkeypatch.setattr(staffing, "StaffingService", lambda: pytest.fail("Missing token must not query SharePoint"))
    client = _operations_client(monkeypatch, "ops@example.com", operations_allowed="ops@example.com", metrics_allowed="other@example.com", enabled_verticals="operations")
    response = client.post("/operaciones/staffing/asignaciones/data", data={"graph_token": ""})
    assert response.status_code == 200  # HTMX fragment, not business success
    assert 'data-error-kind="auth"' in response.text and 'role="alert"' in response.text
    assert "Volvé a iniciar sesión" in response.text and "dashboard-empty" in response.text
    assert '<p class="empty-panel">No pudimos' not in response.text
