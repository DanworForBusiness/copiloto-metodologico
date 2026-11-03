from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service


def test_transition_matrix():
    assert service.allowed_destinations(None) == ("Pendiente",)
    assert service.allowed_destinations("") == ("Pendiente",)
    assert service.allowed_destinations("Pendiente") == ("Planificado", "Suspendido")
    assert service.allowed_destinations("Planificado") == ("Pendiente", "En curso", "Suspendido")
    assert service.allowed_destinations("En curso") == ("Planificado", "Suspendido", "Finalizado")
    assert service.allowed_destinations("Suspendido") == ("Pendiente", "Planificado", "En curso")
    assert service.allowed_destinations("Finalizado") == ()
    assert service.allowed_destinations("Desconocido") == ()


def test_read_follows_graph_pages(monkeypatch):
    base = repository.GRAPH
    list_url = f"{base}/sites/example,site,web/lists/{repository.LIST_ID}"
    next_link = f"{list_url}/items?$skiptoken=next"
    responses = {
        f"{base}/sites/danwor.sharepoint.com:/sites/Gestion": {"id": "example,site,web"},
        f"{list_url}/items?$expand=fields": {"value": [{"id": "1"}], "@odata.nextLink": next_link},
        next_link: {"value": [{"id": "2"}]},
    }
    monkeypatch.setattr(repository, "graph_get_json", lambda url, token: responses[url])
    assert repository.read_requests("test-token") == [{"id": "1"}, {"id": "2"}]


def test_global_search_and_filter_uses_functional_date():
    def row(item_id, date, priority):
        return {"id": item_id, "fields": {
            service.CREATION_DATE: date,
            service.DESCRIPTION: "<b>Arquitectura</b> &amp; datos",
            "Prioridad": priority,
        }}

    rows = [row("1", "2026-01-01", "Alta"), row("2", "2026-02-01", "Alta"),
            row("3", "2026-03-01", "Baja")]
    assert [item["id"] for item in service.select_rows(rows, "arquitectura", {"Prioridad": "Alta"})] == ["2", "1"]
