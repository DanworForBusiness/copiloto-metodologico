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


# ═══════════════ verificación 1 · identidad histórica: ante ambigüedad, falla segura ═══════════════
def _orphan(world, name, cliente, am, lob="SALES"):
    d = world.root / cla.ASSIGN_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps({"schema": 1, "cliente": cliente, "terminal_am": am, "flow_path": f"{cliente}/Viejo_{name}/flujo_Viejo.md",
                                                "mode": "ASSIGNED", "line_of_business": lob, "actor": OWNER, "at": "2026-11-01T00:00:00+00:00",
                                                "history": [{"mode": "ASSIGNED", "to": lob}]}), encoding="utf-8")


def test_an_unambiguous_orphan_applies_to_its_only_tile(world):
    _orphan(world, "viejo", "Contreras", "AM_Turnover")
    e = eff("Contreras")
    assert (e["line_of_business"], e["manual_origin"], e["relinked_from"]) == ("SALES", "TILE", "viejo")


def test_duplicate_tiles_with_the_same_model_make_an_orphan_ambiguous(world):
    """Caso real: Contreras tiene el flujo viejo «General» y «HCM_Turnover», los dos con AM_Turnover: ninguno se apropia del registro histórico."""
    _write_flow(world.root, "Contreras", "HCM_Turnover", "AM_Turnover")
    main._invalidate_catalog_cache()
    _orphan(world, "viejo", "Contreras", "AM_Turnover")
    tiles = [e for e in main._catalog_entries()[1] if e.cliente == "Contreras"]
    assert len(tiles) == 2
    effs = dcs.effective_lob_by_entry(tiles, {t.entry_id: None for t in tiles})
    assert all(v["source"] == "UNCLASSIFIED" and v["manual_origin"] is None for v in effs.values())


def test_a_retired_duplicate_does_not_count_as_a_claimant(world):
    _write_flow(world.root, "Contreras", "HCM_Turnover", "AM_Turnover")
    main._invalidate_catalog_cache()
    dup = next(e for e in main._catalog_entries()[1] if e.cliente == "Contreras" and e.dominio == "HCM_Turnover")
    gov.retire(dup, OWNER)
    _orphan(world, "viejo", "Contreras", "AM_Turnover")
    assert eff("Contreras")["line_of_business"] == "SALES"


def test_a_homonymous_model_of_another_client_never_applies(world):
    _orphan(world, "otro", "Kensing", "AM_Turnover")                    # mismo nombre de modelo, OTRO cliente
    assert eff("Contreras")["source"] == "UNCLASSIFIED"


def test_two_orphans_for_the_same_tile_are_ambiguous(world):
    _orphan(world, "viejo-1", "Contreras", "AM_Turnover", "SALES")
    _orphan(world, "viejo-2", "Contreras", "AM_Turnover", "FINANCE")
    assert eff("Contreras")["source"] == "UNCLASSIFIED"


def test_the_tile_own_record_always_wins_over_orphans(world):
    _orphan(world, "viejo", "Contreras", "AM_Turnover", "SALES")
    post(OWNER, "Contreras", "FINANCE")
    assert eff("Contreras")["line_of_business"] == "FINANCE"


# ═══════════════ verificación 2 · permisos: la política de gobierno del retiro ═══════════════
def test_permission_matrix_matches_the_governance_policy_of_retire(world):
    """Owner: cualquiera. Perfil completo: solo el tile cuyo ÚLTIMO publicador es él. Solo lectura: nunca. Mismo predicado para ícono, GET y POST."""
    eid = entry("Contreras").entry_id
    readonly = _client(READER)
    assert readonly.get(URL, params={"flow": eid}).status_code == 403 and post(READER, "Contreras", "SALES").status_code == 403
    assert "data-catalog-reclassify" not in readonly.get("/catalog/client?cliente=Contreras").text
    assert post(ANA, "Contreras", "SALES").status_code == 403                      # perfil completo SIN ser el último publicador
    gov.record_publication([entry("Contreras")], ANA, "op-test")
    assert gov.can_retire({"sub": ANA}, eid)
    assert post(ANA, "Contreras", "SALES").status_code == 200                      # último publicador: sí, igual que retirar
    assert post(ANA, "Kensing", "SALES").status_code == 403                        # pero no un tile ajeno
    for email, expected in ((OWNER, True), (ANA, True), (READER, False)):
        assert gov.can_retire({"sub": email}, eid) is expected


# ═══════════════ verificación 3 · concurrencia y auditoría ═══════════════
def test_concurrent_writes_are_serialized_and_no_history_is_lost(world, monkeypatch):
    import threading
    import time
    real_write = gov._write

    def slow_write(path, payload):
        time.sleep(0.2)                                                 # ensancha la ventana de carrera leer → escribir
        return real_write(path, payload)
    monkeypatch.setattr(gov, "_write", slow_write)
    e = entry("Contreras")
    errors = []

    def run(lob):
        try:
            cla.assign(e, lob, OWNER, terminal_am="AM_Turnover", previous=None)
        except Exception as exc:                                        # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=run, args=(lob,)) for lob in ("SALES", "FINANCE", "HUMAN_RESOURCES")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    rec = cla.record_for(e)
    assert sorted(h["to"] for h in rec["history"]) == ["FINANCE", "HUMAN_RESOURCES", "SALES"]     # ninguna operación se pierde
    assert rec["line_of_business"] == rec["history"][-1]["to"]                                   # el estado coincide con la última entrada


def test_a_busy_catalog_rejects_without_writing_anything(world, monkeypatch):
    from tools.catalog import publication_txn as txn
    monkeypatch.setattr(cla, "LOCK_TIMEOUT_S", 0.0)

    class Busy:
        def __enter__(self):
            raise txn.CatalogBusyError("ocupado")

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(txn, "writer_lock", lambda timeout=None: Busy())
    r = post(OWNER, "Contreras", "SALES")
    assert r.status_code == 409 and r.json()["ok"] is False and "otra operación" in r.json()["message"]
    assert cla.record_for(entry("Contreras")) is None and eff("Contreras")["source"] == "UNCLASSIFIED"


def test_the_write_lock_is_the_catalog_single_writer_lock():
    import inspect
    src = inspect.getsource(cla._save)
    assert "txn.writer_lock(" in src and "_save_locked" in src
