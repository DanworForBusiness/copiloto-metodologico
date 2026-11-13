from html.parser import HTMLParser

import pytest

from tests.webapp.test_hub_metrics_access import _operations_client


class HeroParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.parents = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "p" and "full-page-subtitle" in attrs.get("class", "").split():
            self.parents.append(self.stack[-1])
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, attrs.get("class", "")))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


@pytest.mark.parametrize("path,text", [
    ("/operaciones/staffing/asignaciones", "Analítica operativa de asignaciones por consultor y proyecto"),
    ("/operaciones/reporting/integral", "Analítica integral de mantenimiento, proyectos, rentabilidad"),
])
def test_subtitle_reuses_reference_hero_container(monkeypatch, path, text):
    client = _operations_client(monkeypatch, "ops@example.com", operations_allowed="ops@example.com", metrics_allowed="other@example.com", enabled_verticals="operations")
    response = client.get(path)
    assert response.status_code == 200
    assert text in response.text
    parser = HeroParser()
    parser.feed(response.text)
    assert parser.parents == [("div", "hc-profiles-hero-text")]
    if path.endswith("asignaciones"):
        assert "data-opsb-hero-desc" in response.text
        assert 'id="opsb-viewswitch"' in response.text


@pytest.mark.parametrize("path,text", [
    ("/operaciones/solicitudes/nueva", "Cargando metadata en Sharepoint..."),
    ("/operaciones/solicitudes/1/editar", "Cargando los datos de Sharepoint..."),
    ("/operaciones/solicitudes", "Cargando solicitudes…"),
])
def test_solicitudes_loading_copy_matches_context(monkeypatch, path, text):
    client = _operations_client(monkeypatch, "ops@example.com", operations_allowed="ops@example.com", metrics_allowed="other@example.com", enabled_verticals="operations")
    response = client.get(path)
    assert response.status_code == 200
    assert text in response.text
