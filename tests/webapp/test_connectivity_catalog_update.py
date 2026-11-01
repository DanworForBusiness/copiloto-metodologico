"""«+» del Catálogo de conexiones → actualización real del Catálogo: comparación con lo publicado, assessment F1, aprobación del dueño y publicación
con el generador existente (sin bajas, sin duplicados, `manual:` intacto, sin credenciales). Catálogo y pool en tmp; sin SAP, sin OAuth, sin red."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tools.catalog import generate_space_catalog as gsc
from webapp import access
from webapp.main import app
from webapp.services import auth_service, catalog_reads
from webapp.services import connectivity_catalog_update as cup

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "tests" / "fixtures" / "connectivity_reconciliation" / "Sintetico" / "Conexiones"
TRAPS = ("mrk_user", "mrk_password_value")
ORIGIN = {"origin": "http://testserver"}
STEM = "01_Arquitectura_y_Conectividad"
INV = {"status": "COMPLETE", "exhaustiveness": "UNVERIFIABLE", "snapshot_id": "s1", "captured_at": "2026-11-02T10:00:00Z"}


def _raw():
    listing = json.loads((SRC / "conn_space_DEV.json").read_text(encoding="utf-8"))
    details = {("DEV", d["name"]): d for d in (json.loads(p.read_text(encoding="utf-8")) for p in sorted(SRC.glob("conn_space_DEV_*.json")))}
    return listing, details


@pytest.fixture()
def cat(tmp_path, monkeypatch):
    """Catálogo publicado con UNA conexión (la primera del listado) + su ficha con un bloque `manual:` cargado a mano."""
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)                      # raíces canónicas del Hub = tmp (dependencia explícita, sin variables de entorno)
    root, pool = cup.canonical_roots()
    conn = pool / "Sintetico" / "Conexiones"
    conn.mkdir(parents=True)
    listing, details = _raw()
    first = listing[0]["name"]
    (conn / "conn_space_DEV.json").write_text(json.dumps(listing[:1]), encoding="utf-8")
    shutil.copy(SRC / f"conn_space_DEV_{first}.json", conn / f"conn_space_DEV_{first}.json")
    gsc.generate_all(pool, root)
    md = root / "arquitectura" / "Sintetico" / "conexiones" / f"DEV__{gsc._slug(first)}.md"
    md.write_text(md.read_text(encoding="utf-8").replace("manual: {}", "manual:\n  owner_note: decisión del dueño"), encoding="utf-8")
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "consultor@danwor.com")
    from tools.catalog import catalog_service
    from tools.catalog.connectivity_governance import observation as obs
    monkeypatch.setattr(cup, "_published", lambda client: obs.from_published_catalog(catalog_service.load_architecture_connections(str(root)), client=client))
    catalog_reads._catalog_architecture_connections.cache_clear()
    cup._PENDING.clear()
    yield {"root": root, "pool": pool, "first": first, "md": md}
    catalog_reads._catalog_architecture_connections.cache_clear()
    cup._PENDING.clear()


def _prepare(executor="owner@danwor.com", details=None):
    listing, det = _raw()
    if details is not None:
        det = details
    return cup.prepare("op1", executor=executor, client="Sintetico", inventory=INV, listings={"DEV": listing}, details=det)


def _client(email):
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": email, "first_name": "T", "family_name": "U"}))
    return c


def test_comparison_classifies_and_runs_the_f1_assessment(cat):
    view = _prepare()
    by = {r["name"]: r for r in view["rows"]}
    assert by[cat["first"]]["class"] == cup.UNCHANGED and not by[cat["first"]]["selectable"]
    news = [r for r in view["rows"] if r["class"] == cup.NEW]
    assert len(news) == 2 and all(r["selectable"] and r["f1"]["labels"] for r in news)
    assert any("homologación" in l for l in view["limits"])
    assert not any(t in json.dumps(list(cup._PENDING["op1"]["docs"].values())) for t in TRAPS)        # credenciales fuera antes de retener


def test_changed_and_unreadable_connections(cat):
    listing, det = _raw()
    det = dict(det)
    det[("DEV", cat["first"])] = {**det[("DEV", cat["first"])], "businessName": "Renombrada"}
    det.pop(("DEV", listing[1]["name"]))
    by = {r["name"]: r for r in _prepare(details=det)["rows"]}
    assert by[cat["first"]]["class"] == cup.CHANGED and "descripción" in by[cat["first"]]["changed"]
    assert by[listing[1]["name"]]["class"] == cup.INSUFFICIENT and not by[listing[1]["name"]]["selectable"]


def test_approve_creates_and_updates_without_deletions_duplicates_or_lost_manual(cat):
    listing, det = _raw()
    det = dict(det)
    det[("DEV", cat["first"])] = {**det[("DEV", cat["first"])], "businessName": "Renombrada"}
    view = _prepare(details=det)
    ids = [r["id"] for r in view["rows"] if r["selectable"]]
    out = cup.approve("op1", actor="owner@danwor.com", selected=ids, rationale="alta inicial")
    assert len(out["published"]) == 3
    pool_listing = json.loads((cat["pool"] / "Sintetico" / "Conexiones" / "conn_space_DEV.json").read_text(encoding="utf-8"))
    assert sorted(i["name"] for i in pool_listing) == sorted(i["name"] for i in listing)              # sin duplicados
    conns = cat["root"] / "arquitectura" / "Sintetico" / "conexiones"
    assert len(list(conns.glob("DEV__*.md"))) == 3
    text = cat["md"].read_text(encoding="utf-8")
    assert "Renombrada" in text and "owner_note: decisión del dueño" in text                            # actualizada, `manual:` intacto
    assert not any(t in p.read_text(encoding="utf-8", errors="ignore") for p in cat["root"].rglob("*") if p.is_file() for t in TRAPS)
    assert "op1" not in cup._PENDING


def test_approve_rejects_unselectable_rows_and_never_deletes(cat):
    view = _prepare()
    before = sorted(p.name for p in cat["root"].rglob("*.md"))
    unchanged = next(r["id"] for r in view["rows"] if r["class"] == cup.UNCHANGED)
    with pytest.raises(cup.UpdateRejected):
        cup.approve("op1", actor="owner@danwor.com", selected=[unchanged], rationale="x")
    with pytest.raises(cup.UpdateRejected):
        cup.approve("op1", actor="owner@danwor.com", selected=[view["rows"][0]["id"]], rationale=" ")
    with pytest.raises(cup.UpdateRejected):
        cup.approve("op1", actor="otro@danwor.com", selected=[view["rows"][0]["id"]], rationale="x")
    assert sorted(p.name for p in cat["root"].rglob("*.md")) == before


def test_failed_publication_rolls_back(cat, monkeypatch):
    view = _prepare()
    ids = [r["id"] for r in view["rows"] if r["selectable"]]
    snapshot = {p: p.read_bytes() for p in cat["root"].rglob("*") if p.is_file() and ".publication" not in str(p)}
    monkeypatch.setattr(gsc, "write_client_catalog", lambda *a, **k: (_ for _ in ()).throw(OSError("disco")))
    with pytest.raises(OSError):
        cup.approve("op1", actor="owner@danwor.com", selected=ids, rationale="x")
    assert {p: p.read_bytes() for p in snapshot} == snapshot
    assert len(json.loads((cat["pool"] / "Sintetico" / "Conexiones" / "conn_space_DEV.json").read_text(encoding="utf-8"))) == 1


def test_routes_review_for_executor_and_approval_owner_only(cat):
    _prepare(executor="consultor@danwor.com")
    url = f"/modules/{STEM}/connectivity-inventory/review/op1"
    assert _client("owner@danwor.com").get(url).status_code == 404                                    # es de otra ejecución
    r = _client("consultor@danwor.com").get(url)
    assert r.status_code == 200 and "data-conn-review" in r.text and 'name="selected"' not in r.text
    ids = [x["id"] for x in cup._PENDING["op1"]["view"]["rows"] if x["selectable"]]
    a = _client("consultor@danwor.com").post(f"/modules/{STEM}/connectivity-inventory/approve",
                                            data={"operation_id": "op1", "selected": ids, "rationale": "x"}, headers=ORIGIN)
    assert a.status_code == 403
    _prepare()
    owner = _client("owner@danwor.com")
    assert 'name="selected"' in owner.get(url).text
    ok = owner.post(f"/modules/{STEM}/connectivity-inventory/approve", data={"operation_id": "op1", "selected": ids, "rationale": "alta"}, headers=ORIGIN)
    assert ok.status_code == 200 and ok.json()["status"] == "PUBLISHED" and len(ok.json()["published"]) == 2
    from tools.catalog import catalog_service
    assert len(catalog_service.load_architecture_connections(str(cat["root"]))) == 3                    # visibles en el Catálogo publicado


def test_progress_log_starts_non_empty_and_records_phases():
    js = (REPO / "webapp" / "static" / "connectivity_inventory.js").read_text(encoding="utf-8")
    assert 'log.textContent = status.dataset.runningText + "\\n"' in js and 'log.textContent += "» " + event.v' in js


def test_sharepoint_regeneration_preserves_connections_published_from_plus(cat, monkeypatch):
    """La regeneración desde SharePoint (fetch incremental + poda) no puede deshacer en silencio lo aprobado con el «+»: los archivos del pool
    escritos por la aprobación viajan como `skip_rels` y quedan intactos; el Catálogo regenerado conserva las conexiones publicadas."""
    import sys
    from tests.test_catalog_apis_only_sync import FakeGraph
    sys.path.insert(0, str(REPO / "tools" / "catalog"))
    import catalog_service as cs
    import generate_catalog as gen
    listing, det = _raw()
    det = dict(det)
    det[("DEV", cat["first"])] = {**det[("DEV", cat["first"])], "businessName": "Renombrada"}
    view = _prepare(details=det)
    cup.approve("op1", actor="owner@danwor.com", selected=[r["id"] for r in view["rows"] if r["selectable"]], rationale="alta")
    conn = cat["pool"] / "Sintetico" / "Conexiones"
    protected = cup.protected_rels(cat["pool"])
    assert {f"Sintetico/Conexiones/conn_space_DEV.json"} | {f"Sintetico/Conexiones/conn_space_DEV_{n}.json" for n in ("ERP_ABAP", "PG_JDBC", "SVC_ODATA")} <= protected
    before = {p.name: p.read_bytes() for p in conn.iterdir()}
    # SharePoint conoce la versión VIEJA (listado de 1 conexión, detalle sin renombrar) y ya la había bajado antes (manifest).
    old_detail = (SRC / f"conn_space_DEV_{cat['first']}.json").read_text(encoding="utf-8")
    old_listing = json.dumps(listing[:1])
    (cat["pool"] / ".download_manifest").write_text(json.dumps({"folder": {
        "Sintetico/Conexiones/conn_space_DEV.json": "L1", f"Sintetico/Conexiones/conn_space_DEV_{cat['first']}.json": "D1",
        "Sintetico/Conexiones/conn_space_DEV_PG_JDBC.json": "P1"}}), encoding="utf-8")
    g = FakeGraph()
    folder = g.folder(g.folder("root", "Sintetico"), "Conexiones")
    g.file(folder, "conn_space_DEV.json", old_listing, "L2")                       # cambió en SharePoint → se descargaría encima
    g.file(folder, f"conn_space_DEV_{cat['first']}.json", old_detail, "D2")        # ídem: revertiría la actualización aprobada
    monkeypatch.setattr(cs, "_graph_get_json", g.get_json)                         # PG_JDBC no está en SharePoint → se podaría
    monkeypatch.setattr(cs, "_download_text", g.download)
    gen.fetch_csn_folder("https://x.sharepoint.com/:f:/s/x/csn", "tok", cat["pool"], skip_rels=protected)
    assert {p.name: p.read_bytes() for p in conn.iterdir()} == before                # nada sobrescrito ni podado
    gsc.generate_all(cat["pool"], cat["root"])
    mds = cat["root"] / "arquitectura" / "Sintetico" / "conexiones"
    assert len(list(mds.glob("DEV__*.md"))) == 3 and "Renombrada" in cat["md"].read_text(encoding="utf-8")
    src = (REPO / "webapp" / "services" / "admin_tools.py").read_text(encoding="utf-8")
    assert "gen.fetch_csn_folder(csn_url, graph_token, cache, skip_rels=protected)" in src
