"""«Reclasificar» (línea de negocio) desde el tile del Catálogo: precedencia, restaurar, permisos, aislamiento, auditoría, ids históricos y no interferencia.

Precedencia (owner): (1) asignación desde el tile → (2) histórica del flujo (`manual.linea_de_negocio`, solo lectura) → (3) automática HIGH → (4) sin clasificar.
«Restaurar» retira SOLO la del tile. Datos sintéticos bajo ``tmp_path``; sin Datasphere ni Spaces.
"""
from __future__ import annotations

import hashlib
import json
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import webapp.main as main
from webapp import access
from webapp.services import auth_service, catalog_governance as gov, catalog_lob_assignments as cla, domain_classification_service as dcs

OWNER, ANA, READER = "owner@danwor.com", "ana@danwor.com", "lector@danwor.com"
ORIGIN = {"Origin": "http://testserver"}
URL = "/catalog/flow/lob"
REPO = Path(__file__).resolve().parents[2]
FLOW = "---\nauto:\n  tipo: flujo\n  dominio: {dom}\n  artefactos: [{art}]\nmanual:\n  cliente: {cli}\n{extra}---\n# demo\n"


def _client(email):
    c = TestClient(main.app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _write_flow(root: Path, cli: str, dom: str, art: str, flow_lob: str | None = None) -> Path:
    d = root / cli / dom
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"flujo_{dom}.md"
    p.write_text(FLOW.format(dom=dom, art=art, cli=cli, extra=f"  linea_de_negocio: {flow_lob}\n" if flow_lob else ""), encoding="utf-8")
    return p


@pytest.fixture()
def world(monkeypatch, tmp_path):
    root = tmp_path / "catalog_local"
    _write_flow(root, "Contreras", "Turnover", "AM_Turnover")
    _write_flow(root, "Kensing", "Gastos", "4AM_FNC_GL", flow_lob="FINANCE")          # asignación HISTÓRICA en el flujo
    monkeypatch.setattr(config, "BASE_DIR", REPO)
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(root))
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", f"{OWNER},{ANA}")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", READER)
    main._invalidate_catalog_cache()
    yield types.SimpleNamespace(root=root)
    main._invalidate_catalog_cache()


def entry(cliente):
    return next(e for e in main._catalog_entries()[1] if e.cliente == cliente)


def eff(cliente):
    e = entry(cliente)
    return dcs.effective_lob_by_entry([e], {e.entry_id: None})[e.entry_id]


def post(email, cliente, lob, **kw):
    return _client(email).post(URL, data={"flow": kw.get("flow") or entry(cliente).entry_id, "lob": lob}, headers=kw.get("headers", ORIGIN))


# ── asignar / reasignar / restaurar ──
def test_assign_reassign_and_restore_from_the_tile(world):
    assert eff("Contreras")["source"] == "UNCLASSIFIED"
    r = post(OWNER, "Contreras", "HUMAN_RESOURCES")
    assert r.status_code == 200 and r.json()["label"] == "Recursos Humanos" and r.headers["HX-Trigger"] == "catalog-lob-changed"
    e = eff("Contreras")
    assert (e["source"], e["line_of_business"], e["manual_origin"], e["manual_actor"]) == ("MANUAL", "HUMAN_RESOURCES", "TILE", OWNER)
    assert post(OWNER, "Contreras", "FINANCE").json()["line_of_business"] == "FINANCE"
    r = post(OWNER, "Contreras", "RESTORE")
    assert r.json()["source"] == "UNCLASSIFIED" and eff("Contreras")["manual_origin"] is None
    rec = cla.record_for(entry("Contreras"))
    assert rec["mode"] == cla.CLEARED and [h["mode"] for h in rec["history"]] == ["ASSIGNED", "ASSIGNED", "CLEARED"]        # auditoría completa


# ── precedencia frente a la histórica del flujo ──
def test_tile_wins_over_the_historical_flow_assignment_and_restore_shows_it_again_untouched(world):
    flow_file = Path(entry("Kensing").path)
    before = flow_file.read_bytes()
    e = eff("Kensing")
    assert (e["line_of_business"], e["manual_origin"], e["flow_lob"]) == ("FINANCE", "FLOW", "FINANCE")
    post(OWNER, "Kensing", "SALES")
    e = eff("Kensing")
    assert (e["line_of_business"], e["manual_origin"], e["flow_lob"]) == ("SALES", "TILE", "FINANCE")                     # la histórica sigue visible
    post(OWNER, "Kensing", "RESTORE")
    e = eff("Kensing")
    assert (e["line_of_business"], e["manual_origin"]) == ("FINANCE", "FLOW")                                             # restaurar NO la oculta
    assert flow_file.read_bytes() == before                                                                                # ni la modifica


def test_the_dialog_previews_what_remains_after_restore(world):
    post(OWNER, "Kensing", "SALES")
    html = _client(OWNER).get("/catalog/client?cliente=Kensing").text
    assert 'data-catalog-reclassify' in html and "<p>Reclasificar</p>" in html and "data-state" not in html     # el listado no embebe clasificaciones
    r = _client(OWNER).get(URL, params={"flow": entry("Kensing").entry_id})
    state = r.json()
    assert r.headers["cache-control"] == "no-store"
    assert (state["current_label"], state["has_tile"], state["flow_label"], state["restore_label"], state["restore_origin"]) == \
           ("Ventas", True, "Finanzas", "Finanzas", "asignación histórica del flujo")
    assert _client(ANA).get(URL, params={"flow": entry("Kensing").entry_id}).status_code == 403
    assert TestClient(main.app).get(URL, params={"flow": entry("Kensing").entry_id}).status_code == 401


# ── permisos, origen y validación ──
def test_permissions_origin_and_validation(world):
    eid = entry("Contreras").entry_id
    assert TestClient(main.app).post(URL, data={"flow": eid, "lob": "SALES"}, headers=ORIGIN).status_code == 401
    assert post(ANA, "Contreras", "SALES").status_code == 403                               # sin ser el último publicador: igual que retirar
    assert post(OWNER, "Contreras", "SALES", headers={"Origin": "http://evil.example"}).status_code == 403
    assert post(OWNER, "Contreras", "NO_EXISTE").status_code == 400
    assert post(OWNER, "Contreras", "SALES", flow="no-existe-flujo").status_code == 404
    assert "data-catalog-reclassify" not in _client(ANA).get("/catalog/client?cliente=Contreras").text


def test_the_client_comes_from_the_entry_never_from_the_form(world):
    _client(OWNER).post(URL, data={"flow": entry("Contreras").entry_id, "lob": "SALES", "cliente": "Kensing"}, headers=ORIGIN)
    rec = cla.record_for(entry("Contreras"))
    assert rec["cliente"] == "Contreras" and cla.record_for(entry("Kensing")) is None


# ── compatibilidad con identificadores históricos ──
def test_an_assignment_survives_a_tile_id_change_and_migrates_on_next_write(world):
    post(OWNER, "Contreras", "HUMAN_RESOURCES")
    old_id = entry("Contreras").entry_id
    rec = json.loads((world.root / cla.ASSIGN_DIR / f"{old_id}.json").read_text(encoding="utf-8"))
    rec["terminal_am"] = "AM_Turnover"                                                       # lo resuelve el assessment; acá se fija
    (world.root / cla.ASSIGN_DIR / f"{old_id}.json").write_text(json.dumps(rec), encoding="utf-8")
    old_dir = world.root / "Contreras" / "Turnover"                                          # el dominio se renombra (p. ej. corrección de «General»)
    new_dir = world.root / "Contreras" / "HCM_Turnover"
    new_dir.mkdir(parents=True)
    (new_dir / "flujo_HCM_Turnover.md").write_text((old_dir / "flujo_Turnover.md").read_text(encoding="utf-8").replace("dominio: Turnover", "dominio: HCM_Turnover"), encoding="utf-8")
    for p in old_dir.iterdir():
        p.unlink()
    old_dir.rmdir()
    main._invalidate_catalog_cache()
    e = eff("Contreras")
    assert entry("Contreras").entry_id != old_id and (e["line_of_business"], e["manual_origin"], e["relinked_from"]) == ("HUMAN_RESOURCES", "TILE", old_id)
    post(OWNER, "Contreras", "SALES")
    assert not (world.root / cla.ASSIGN_DIR / f"{old_id}.json").exists()                     # el huérfano se migró al id actual
    assert len(cla.record_for(entry("Contreras"))["history"]) == 2


def test_two_orphans_for_the_same_model_are_ambiguous_and_ignored(world):
    d = world.root / cla.ASSIGN_DIR
    d.mkdir(parents=True)
    for i in (1, 2):
        (d / f"viejo-{i}.json").write_text(json.dumps({"schema": 1, "cliente": "Contreras", "terminal_am": "AM_Turnover", "flow_path": f"Contreras/X{i}/flujo_X{i}.md",
                                                       "mode": "ASSIGNED", "line_of_business": "SALES"}), encoding="utf-8")
    assert eff("Contreras")["source"] == "UNCLASSIFIED"


# ── no interferencia ──
def test_reclassifying_never_touches_the_curated_flow_pool_or_assessments(world, tmp_path):
    pool = world.root / ".csn_cache" / "Contreras" / "views"
    pool.mkdir(parents=True)
    (pool / "AM_Turnover.json").write_text("{}", encoding="utf-8")
    governed = tmp_path / "governed"
    governed.mkdir()
    (governed / "x.json").write_text("{}", encoding="utf-8")

    def snapshot():
        return {p: hashlib.sha256(p.read_bytes()).hexdigest() for base in (world.root, governed) for p in base.rglob("*")
                if p.is_file() and cla.ASSIGN_DIR not in p.parts}
    before = snapshot()
    post(OWNER, "Contreras", "HUMAN_RESOURCES")
    post(OWNER, "Kensing", "SALES")
    post(OWNER, "Kensing", "RESTORE")
    assert snapshot() == before


def test_a_storage_failure_reports_and_applies_nothing(world, monkeypatch):
    monkeypatch.setattr(gov, "_write", lambda path, payload: (_ for _ in ()).throw(gov.GovernanceStorageError("x")))
    r = post(OWNER, "Contreras", "SALES")
    assert r.status_code == 503 and r.json()["ok"] is False and eff("Contreras")["source"] == "UNCLASSIFIED"


def test_destinations_are_only_the_governed_taxonomy():
    from tools.catalog.business_classification import LINES_OF_BUSINESS
    from webapp.templating import templates
    assert [c["value"] for c in templates.env.globals["lob_choices"]()] == [x["id"] for x in LINES_OF_BUSINESS]
