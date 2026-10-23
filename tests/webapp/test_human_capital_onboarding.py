"""test_human_capital_onboarding.py — Onboarding (core): entidad, trigger de creación, checklist, listado/edición y señal en Búsquedas.

Dominio verbatim de la app de referencia (app-seleccion: tabla `onboarding`, `lib/actions/onboarding.ts`, `lib/onboarding.ts`): una fila por candidatura que queda
Finalizado + Ingreso, creada SOLO automáticamente (idempotente), nunca borrada si el resultado cambia, con 17 ítems de checklist canónicos y `completado` recalculado al
tildar. MISMO patrón de aislamiento/durabilidad-en-test que el resto de CCHH.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (
    auth_service, busqueda_repository, entrevista_repository, human_capital_service, onboarding_checklist, onboarding_repository, profile_repository,
)
from webapp.services import human_capital_catalog_repository as catalog_repo

_AUTHORIZED_EMAIL = "hc-onboarding-test@danwor.com"

# Los 17 ítems canónicos TAL CUAL `ITEMS_ONBOARDING` de la referencia (lib/types.ts), en su orden.
REFERENCE_ITEMS = [
    ("ddjj_domicilio", "DDJJ de Domicilio"), ("cv_formato_danwor", "CV en formato Danwor"), ("nota_convenio_colectivo", "Nota de convenio Colectivo"),
    ("oferta_empleo", "Oferta de empleo / Carta oferta de servicios"), ("nda", "NDA"), ("dni", "DNI"), ("foto", "Foto"),
    ("titulos_certificados", "Títulos o Certificados"), ("preocupacional", "Preocupacional"), ("alta_arca", "Alta de ARCA"),
    ("nota_herramientas", "Nota de herramientas"), ("sensibilizacion_iso", "Sensibilización ISO"), ("requerimiento_perfil", "Requerimiento de perfil"),
    ("comunidad_wp", "Comunidad de WP"), ("alta_legajo_diamo", "Alta en legajo DIAMO"), ("alta_timenotes", "Alta en Timenotes"),
    ("verificacion_referencias", "Verificación de referencias laborales"),
]


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _AUTHORIZED_EMAIL)
    monkeypatch.setattr(auth_service, "HC_SENSITIVE_DATA_ALLOWED_USERS", _AUTHORIZED_EMAIL)       # «Oferta cerrada» es dato sensible: estos tests ejercitan el camino AUTORIZADO (el restringido vive en test_onboarding_offer_protection.py)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")     # estos tests simulan fallos/confirmaciones de Spaces: ejercitan el contrato REMOTE (el modo es explícito)
    for mod in (onboarding_repository, entrevista_repository, busqueda_repository, profile_repository):
        monkeypatch.setattr(mod.remote_store, "push_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "delete_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "enabled", lambda: False)


def _client() -> TestClient:
    session = auth_service.create_session({"email": _AUTHORIZED_EMAIL, "name": "O T", "first_name": "O", "family_name": "T"})
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _seed_area_consultora(*values):
    snap = catalog_repo.build_snapshot({"area_consultora": [{"valor": v, "orden": i + 1} for i, v in enumerate(values)]},
                                       refreshed_by="test", source={"drive_id": "d", "item_id": "i"})
    p = catalog_repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


def _profile(first="Cand", last="Idato") -> str:
    return profile_repository.create({"first_name": first, "last_name": last, "email": "c@danwor.com"})["profile_id"]


def _busqueda(nombre="Analista BI", **over) -> str:
    return human_capital_service.create_busqueda({"nombre_puesto": nombre, "fecha_apertura": "2026-09-01", **over})["busqueda_id"]


def _add(busqueda_id: str, profile_id: str) -> str:
    b = human_capital_service.add_candidate_to_busqueda(busqueda_id, profile_id)
    return next(a["application_id"] for a in b["candidatos"] if a["profile_id"] == profile_id)


def _hire(busqueda_id: str, first="Cand", last="Idato") -> tuple[str, str]:
    """Candidatura que llega a Finalizado + Ingreso en UNA sola operación. Devuelve (profile_id, application_id)."""
    pid = _profile(first, last)
    aid = _add(busqueda_id, pid)
    human_capital_service.move_application_stage(busqueda_id, aid, "Finalizado", "Ingreso")
    return pid, aid


# ── definición del checklist ─────────────────────────────────────────────────

def test_the_checklist_definition_is_the_17_canonical_reference_items_in_order():
    assert list(onboarding_checklist.CHECKLIST_ITEMS) == REFERENCE_ITEMS and len(onboarding_checklist.ITEM_KEYS) == 17


def test_progress_is_derived_from_the_state_and_missing_keys_count_as_unchecked():
    assert onboarding_checklist.progress({}) == {"hechos": 0, "total": 17, "porcentaje": 0, "completo": False}
    assert onboarding_checklist.progress(None)["hechos"] == 0
    half = {k: True for k in onboarding_checklist.ITEM_KEYS[:9]}
    assert onboarding_checklist.progress(half) == {"hechos": 9, "total": 17, "porcentaje": 53, "completo": False}
    assert onboarding_checklist.progress({k: True for k in onboarding_checklist.ITEM_KEYS})["completo"] is True
    assert onboarding_checklist.progress({"no_existe": True})["hechos"] == 0            # solo cuentan los ítems canónicos


# ── trigger de creación (centralizado, idempotente, robusto) ─────────────────

def test_finalizado_without_result_creates_nothing_then_setting_ingreso_creates_it():
    bid = _busqueda()
    pid = _profile()
    aid = _add(bid, pid)
    human_capital_service.move_application_stage(bid, aid, "Finalizado", None)
    assert onboarding_repository.list() == []                                            # Finalizado SIN resultado: nada
    human_capital_service.move_application_stage(bid, aid, "Finalizado", "Ingreso")      # el resultado se asigna DESPUÉS
    (o,) = onboarding_repository.list()
    assert (o["application_id"], o["profile_id"], o["busqueda_id"]) == (aid, pid, bid)
    assert o["checklist"] == {} and o["completado"] is False and o["revision"] == 1 and o["oferta_cerrada"] is None


def test_finalizado_plus_ingreso_in_a_single_operation_creates_it_and_repeating_never_duplicates():
    bid = _busqueda()
    _pid, aid = _hire(bid)
    (first,) = onboarding_repository.list()
    human_capital_service.move_application_stage(bid, aid, "Finalizado", "Ingreso")      # misma mutación otra vez
    human_capital_service.sync_onboardings_for_busqueda(busqueda_repository.get(bid))
    human_capital_service.reconcile_onboardings()
    (again,) = onboarding_repository.list()
    assert again == first and len(list(onboarding_repository._onboarding_dir().glob("*.json"))) == 1


def test_a_later_change_of_result_never_deletes_the_onboarding():
    bid = _busqueda()
    _pid, aid = _hire(bid)
    onboarding_repository.set_item(aid, "dni", True)
    human_capital_service.move_application_stage(bid, aid, "Finalizado", "Rechazado")
    human_capital_service.move_application_stage(bid, aid, "Decisión Final", None)
    (o,) = onboarding_repository.list()
    assert o["application_id"] == aid and o["checklist"]["dni"] is True                  # alineado con la referencia: no se borra


def test_only_finalizado_plus_ingreso_creates_an_onboarding():
    bid = _busqueda()
    for stage, result in (("Preselección", None), ("Entrevista CCHH", None), ("Entrevista Líder", None), ("Decisión Final", None), ("Finalizado", "Rechazado"), ("Finalizado", "Desistió")):
        aid = _add(bid, _profile())
        human_capital_service.move_application_stage(bid, aid, stage, result)
    assert onboarding_repository.list() == []
    human_capital_service.close_busqueda(bid)                                            # cerrar no inventa ingresos
    assert onboarding_repository.list() == []


def test_the_creation_condition_has_one_authoritative_definition():
    assert human_capital_service.is_ingreso_application({"etapa": "Finalizado", "resultado_final": "Ingreso"}) is True
    assert human_capital_service.is_ingreso_application({"etapa": "Finalizado", "resultado_final": None}) is False
    assert human_capital_service.is_ingreso_application({"etapa": "Decisión Final", "resultado_final": "Ingreso"}) is False
    src = open(human_capital_service.__file__, encoding="utf-8").read()
    assert src.count('"Ingreso"') <= 2                                                   # la condición vive en un solo lugar (+ el docstring), no copiada por los call sites


def test_reconcile_creates_onboardings_for_ingresos_that_already_existed_and_is_idempotent():
    bid = _busqueda()
    _pid, aid = _hire(bid)
    onboarding_repository._onboarding_path(aid).unlink()                                 # ingreso previo a la sección
    assert onboarding_repository.list() == []
    assert human_capital_service.reconcile_onboardings() == 1
    assert human_capital_service.reconcile_onboardings() == 0
    assert [o["application_id"] for o in onboarding_repository.list()] == [aid]


def test_a_storage_failure_never_breaks_the_pipeline_move_and_reconcile_recovers(monkeypatch):
    bid = _busqueda()
    pid = _profile()
    aid = _add(bid, pid)

    def boom(*a, **k):
        raise onboarding_repository.OnboardingStorageError("disco lleno")
    with monkeypatch.context() as m:
        m.setattr(onboarding_repository, "create_if_absent", boom)
        b = human_capital_service.move_application_stage(bid, aid, "Finalizado", "Ingreso")
        assert next(a for a in b["candidatos"] if a["application_id"] == aid)["resultado_final"] == "Ingreso"      # el movimiento ya se confirmó
    assert human_capital_service.reconcile_onboardings() == 1


def test_the_onboarding_references_the_existing_application_identity_with_no_extra_id():
    bid = _busqueda()
    _pid, aid = _hire(bid)
    (o,) = onboarding_repository.list()
    assert onboarding_repository._onboarding_path(aid).name == f"{aid}.json" and "onboarding_id" not in o


def test_the_area_is_inherited_from_the_interview_and_stays_in_sync_when_it_is_edited():
    _seed_area_consultora("Consultoria", "RRHH")
    bid = _busqueda(area_consultora="Consultoria")
    pid = _profile()
    aid = _add(bid, pid)
    human_capital_service.move_application_stage(bid, aid, "Entrevista CCHH", None)      # crea la Entrevista automática (área de la Búsqueda)
    human_capital_service.move_application_stage(bid, aid, "Finalizado", "Ingreso")
    assert onboarding_repository.get(aid)["area"] == "Consultoria"                       # heredada de la Entrevista
    ent = entrevista_repository.find_by_profile_and_busqueda(pid, bid)
    human_capital_service.update_entrevista(ent["entrevista_id"], ent["revision"], {"area": "RRHH"})
    o = onboarding_repository.get(aid)
    assert o["area"] == "RRHH" and o["revision"] == 2                                    # sincronizada, y un formulario abierto verá el cambio como concurrente


def test_without_interview_area_is_empty_and_interview_edits_without_onboarding_do_nothing():
    _seed_area_consultora("Consultoria", "RRHH")
    bid = _busqueda()
    _pid, aid = _hire(bid)
    assert onboarding_repository.get(aid)["area"] is None
    manual = human_capital_service.create_entrevista_manual(_profile(), {"area": "RRHH"})   # entrevista manual: sin application_id
    human_capital_service.update_entrevista(manual["entrevista_id"], manual["revision"], {"area": "Consultoria"})
    assert [o["application_id"] for o in onboarding_repository.list()] == [aid] and onboarding_repository.get(aid)["area"] is None
    pid2 = _profile()
    aid2 = _add(bid, pid2)
    human_capital_service.move_application_stage(bid, aid2, "Entrevista CCHH", None)     # tiene Entrevista pero todavía NO onboarding
    ent = entrevista_repository.find_by_profile_and_busqueda(pid2, bid)
    human_capital_service.update_entrevista(ent["entrevista_id"], ent["revision"], {"area": "RRHH"})
    assert onboarding_repository.get(aid2) is None                                       # la sincronización NO crea onboardings


# ── repositorio ──────────────────────────────────────────────────────────────

def test_ticking_items_is_atomic_does_not_bump_revision_and_recomputes_completado():
    bid = _busqueda()
    _pid, aid = _hire(bid)
    keys = onboarding_checklist.ITEM_KEYS
    for k in keys[:-1]:
        o = onboarding_repository.set_item(aid, k, True)
    assert o["completado"] is False and o["revision"] == 1 and onboarding_checklist.progress(o["checklist"])["hechos"] == 16
    o = onboarding_repository.set_item(aid, keys[-1], True)
    assert o["completado"] is True and o["revision"] == 1
    o = onboarding_repository.set_item(aid, "foto", False)                               # destildar recalcula (como el trigger de la referencia)
    assert o["completado"] is False and o["checklist"]["foto"] is False
    with pytest.raises(ValueError):
        onboarding_repository.set_item(aid, "item_inventado", True)
    with pytest.raises(onboarding_repository.OnboardingNotFoundError):
        onboarding_repository.set_item("no-existe", "dni", True)


def test_completado_is_persisted_and_stable_when_the_definition_changes(monkeypatch):
    bid = _busqueda()
    _pid, aid = _hire(bid)
    for k in onboarding_checklist.ITEM_KEYS:
        onboarding_repository.set_item(aid, k, True)
    monkeypatch.setattr(onboarding_checklist, "CHECKLIST_ITEMS", (*onboarding_checklist.CHECKLIST_ITEMS, ("nuevo_item", "Nuevo")))
    assert onboarding_repository.get(aid)["completado"] is True                           # un onboarding ya completo no vuelve atrás por sumar un ítem a la definición


def test_form_edits_use_optimistic_concurrency_and_validate_fecha():
    bid = _busqueda()
    _pid, aid = _hire(bid)
    o = human_capital_service.update_onboarding(aid, 1, {"fecha_ingreso": "2026-10-05", "oferta_cerrada": "  USD 2.400  ", "area": ""}, allow_sensitive=True)
    assert (o["fecha_ingreso"], o["oferta_cerrada"], o["area"], o["revision"]) == ("2026-10-05", "USD 2.400", None, 2)
    with pytest.raises(onboarding_repository.OnboardingConcurrentModificationError):
        human_capital_service.update_onboarding(aid, 1, {"fecha_ingreso": "2026-10-06"})
    for bad in ("", "05/10/2026", "2026-13-40"):
        with pytest.raises(ValueError):
            human_capital_service.update_onboarding(aid, 2, {"fecha_ingreso": bad})
    onboarding_repository.set_item(aid, "dni", True)                                    # tildar NO invalida el formulario abierto
    assert human_capital_service.update_onboarding(aid, 2, {"fecha_ingreso": "2026-10-07"})["revision"] == 3


def test_area_is_governed_by_area_consultora_with_the_migration_safe_contract():
    _seed_area_consultora("Consultoria", "RRHH")
    bid = _busqueda()
    _pid, aid = _hire(bid)
    onboarding_repository.set_area(aid, "OPERACIONES")                                   # legacy heredado de una entrevista vieja, fuera de catálogo
    o = onboarding_repository.get(aid)
    kept = human_capital_service.update_onboarding(aid, o["revision"], {"fecha_ingreso": o["fecha_ingreso"], "area": "OPERACIONES", "oferta_cerrada": "x"}, allow_sensitive=True)
    assert kept["area"] == "OPERACIONES"                                                  # legacy preservado si no se toca
    with pytest.raises(ValueError):
        human_capital_service.update_onboarding(aid, kept["revision"], {"fecha_ingreso": o["fecha_ingreso"], "area": "Otra Cosa"})
    assert human_capital_service.update_onboarding(aid, kept["revision"], {"fecha_ingreso": o["fecha_ingreso"], "area": "RRHH"})["area"] == "RRHH"


def test_a_failed_remote_write_rolls_back_and_reports(monkeypatch):
    bid = _busqueda()
    _pid, aid = _hire(bid)
    monkeypatch.setattr(onboarding_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(onboarding_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(onboarding_repository.remote_store, "await_restore", lambda timeout=None: None)
    with pytest.raises(onboarding_repository.OnboardingRemoteOutcomeUnknownError):
        onboarding_repository.set_item(aid, "dni", True)
    assert onboarding_repository.get(aid)["checklist"] == {}                               # el local vuelve a la versión previa


# ── rutas: listado ───────────────────────────────────────────────────────────

def test_routes_require_the_human_capital_session():
    c = TestClient(app, follow_redirects=False)
    assert c.get("/cchh/onboarding").status_code in (302, 303, 307)
    session = auth_service.create_session({"email": "x@danwor.com", "name": "X", "first_name": "X", "family_name": "Y"})
    other = TestClient(app, follow_redirects=False)
    other.cookies.set(auth_service.COOKIE_NAME, session)
    assert other.get("/cchh/onboarding").status_code == 403
    assert other.post("/cchh/onboarding/x/checklist/dni").status_code == 403


def test_list_empty_state_header_and_nav():
    html = _client().get("/cchh/onboarding").text
    assert "Sin onboardings" in html and "0 onboardings" in html and "<h1>Onboarding</h1>" in html
    assert 'href="/cchh/onboarding" class="module-link is-active"' in html and "<strong>Onboarding</strong>" in html
    assert 'data-dashboard-key="cchh-onboarding-list"' in html and 'id="hc-onboarding-table"' not in html     # sin filas no hay tabla


def test_list_renders_the_reference_columns_rows_progress_and_no_delete_action():
    bid = _busqueda("Analista BI")
    pid, aid = _hire(bid, "Ana", "Li")
    human_capital_service.update_onboarding(aid, 1, {"fecha_ingreso": "2026-10-05", "oferta_cerrada": "", "area": ""})
    for k in onboarding_checklist.ITEM_KEYS[:5]:
        onboarding_repository.set_item(aid, k, True)
    _pid2, aid2 = _hire(bid, "Beto", "Gomez")
    for k in onboarding_checklist.ITEM_KEYS:
        onboarding_repository.set_item(aid2, k, True)
    html = _client().get("/cchh/onboarding").text
    assert "2 onboardings" in html and "Ana Li" in html and "Beto Gomez" in html and "Analista BI" in html
    assert "05-10-2026" in html                                                           # presentación DD-MM-YYYY
    assert "5/17 · 29%" in html and "Completado" in html
    assert 'data-draggable-columns-key="cchh-onboarding"' in html and 'data-resizable-columns-key="cchh-onboarding"' in html
    for col in ("candidato", "puesto", "area", "ingreso", "progreso"):
        assert f'data-column-key="{col}"' in html
    assert f"/cchh/onboarding/{aid}/editar" in html and "/eliminar" not in html            # sólo Editar: la referencia no tiene delete


def test_list_filter_is_reactive_and_serves_only_the_panel_fragment():
    bid = _busqueda()
    _p1, a1 = _hire(bid, "Pend", "Iente")
    _p2, a2 = _hire(bid, "Comp", "Leto")
    for k in onboarding_checklist.ITEM_KEYS:
        onboarding_repository.set_item(a2, k, True)
    c = _client()
    full = c.get("/cchh/onboarding?estado=pendientes").text
    assert "<html" in full and "Pend Iente" in full and "Comp Leto" not in full and "1 onboarding<" in full.replace("</span>", "<")
    frag = c.get("/cchh/onboarding?estado=completos", headers={"HX-Request": "true"}).text
    assert "<html" not in frag and 'id="hc-onboarding-panel"' in frag and "Comp Leto" in frag and "Pend Iente" not in frag
    assert "✕ Limpiar" in frag and 'name="estado"' in frag
    assert "Todos" in frag and 'value="completos" selected' in frag
    assert "2 onboardings" in c.get("/cchh/onboarding?estado=inventado", headers={"HX-Request": "true"}).text          # filtro inválido = todos


def test_list_no_matches_state_differs_from_dataset_empty():
    bid = _busqueda()
    _hire(bid)
    html = _client().get("/cchh/onboarding?estado=completos", headers={"HX-Request": "true"}).text
    assert 'data-empty-kind="no_matches"' in html and "0 onboardings" in html


def _tree_snapshot():
    root = config.BASE_DIR / "human-capital"
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_navigating_the_onboarding_pages_is_read_only_and_never_backfills():
    bid = _busqueda()
    _pid, aid = _hire(bid, "Vieja", "Carga")
    onboarding_repository._onboarding_path(aid).unlink()                                  # ingreso histórico SIN onboarding (anterior a la sección)
    before = _tree_snapshot()
    c = _client()
    for url in ("/cchh/onboarding", "/cchh/onboarding?estado=pendientes", "/cchh/busquedas"):
        assert c.get(url).status_code == 200
    assert c.get("/cchh/onboarding", headers={"HX-Request": "true"}).status_code == 200
    assert onboarding_repository.list() == [] and _tree_snapshot() == before              # ni entidades nuevas ni archivos tocados
    assert "Sin onboardings" in c.get("/cchh/onboarding").text


def test_the_historical_backfill_is_an_explicit_idempotent_operation_outside_any_request():
    bid = _busqueda()
    _pid, aid = _hire(bid, "Vieja", "Carga")
    onboarding_repository._onboarding_path(aid).unlink()
    assert human_capital_service.reconcile_onboardings() == 1 and human_capital_service.reconcile_onboardings() == 0
    assert "Vieja Carga" in _client().get("/cchh/onboarding").text
    router_src = open(__import__("webapp.routers.human_capital", fromlist=["x"]).__file__, encoding="utf-8").read()
    assert "reconcile_onboardings()" not in router_src.replace("`human_capital_service.reconcile_onboardings`", "")       # ningún call site en el router


def test_a_stale_open_form_never_overwrites_the_checklist_ticked_meanwhile():
    """Form abierto en revision N -> se tilda/destilda el checklist (guardado inmediato, no bumpea revision) -> se guarda el form viejo: el checklist vigente se preserva."""
    bid = _busqueda()
    _pid, aid = _hire(bid)
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": _AUTHORIZED_EMAIL, "name": "O", "first_name": "O", "family_name": "T"}))
    html = c.get(f"/cchh/onboarding/{aid}/editar").text
    assert 'name="expected_revision" value="1"' in html                                    # el formulario queda abierto en revision 1
    for key in ("dni", "nda", "foto"):
        c.post(f"/cchh/onboarding/{aid}/checklist/{key}", data={"checked": "1"})
    c.post(f"/cchh/onboarding/{aid}/checklist/nda", data={})                                # y destilda uno
    assert onboarding_repository.get(aid)["revision"] == 1                                  # tildar no invalidó el formulario
    r = c.post(f"/cchh/onboarding/{aid}/editar", data={"fecha_ingreso": "2026-10-12", "oferta_cerrada": "USD 2.000", "area": "", "expected_revision": "1"})
    assert r.status_code == 303                                                             # el form viejo se acepta...
    o = onboarding_repository.get(aid)
    assert o["checklist"] == {"dni": True, "nda": False, "foto": True} and o["completado"] is False      # ...y el estado NUEVO del checklist sigue intacto
    assert (o["fecha_ingreso"], o["oferta_cerrada"], o["revision"]) == ("2026-10-12", "USD 2.000", 2)


def test_ticking_after_a_form_save_also_preserves_the_form_fields():
    bid = _busqueda()
    _pid, aid = _hire(bid)
    human_capital_service.update_onboarding(aid, 1, {"fecha_ingreso": "2026-10-12", "oferta_cerrada": "X", "area": ""}, allow_sensitive=True)
    onboarding_repository.set_item(aid, "dni", True)
    o = onboarding_repository.get(aid)
    assert (o["fecha_ingreso"], o["oferta_cerrada"], o["checklist"], o["revision"]) == ("2026-10-12", "X", {"dni": True}, 2)


def test_list_shows_a_deleted_profile_without_breaking():
    bid = _busqueda()
    pid, _aid = _hire(bid)
    human_capital_service.soft_delete_profile(pid)
    html = _client().get("/cchh/onboarding").text
    assert "1 onboarding" in html


# ── rutas: edición + checklist ───────────────────────────────────────────────

def test_edit_page_renders_the_data_form_and_the_17_checklist_items_with_immediate_save():
    _seed_area_consultora("Consultoria", "RRHH")
    bid = _busqueda("Analista BI")
    _pid, aid = _hire(bid, "Ana", "Li")
    onboarding_repository.set_item(aid, "dni", True)
    html = _client().get(f"/cchh/onboarding/{aid}/editar").text
    assert "Ana Li" in html and "Analista BI" in html and "Volver a Onboarding" in html and f'action="/cchh/onboarding/{aid}/editar"' in html
    assert 'name="expected_revision" value="1"' in html and 'name="fecha_ingreso"' in html and 'name="oferta_cerrada"' in html and 'name="area"' in html
    assert '<option value="Consultoria" >Consultoria</option>' in html and '<option value="RRHH" >RRHH</option>' in html
    for key, label in REFERENCE_ITEMS:
        assert f'hx-post="/cchh/onboarding/{aid}/checklist/{key}"' in html and label in html
    assert html.count("data-onboarding-item=") == 17 and 'data-onboarding-item="dni" checked' in html and "1/17 · 6%" in html
    assert "Guardar cambios" in html and "No se concretó" not in html


def test_edit_page_404_for_unknown_onboarding_and_no_manual_creation_route():
    c = _client()
    assert c.get("/cchh/onboarding/no-existe/editar").status_code == 404
    assert c.get("/cchh/onboarding/nuevo").status_code == 404 and c.post("/cchh/onboarding/nuevo").status_code in (404, 405)
    paths = {r.path for r in app.routes if "onboarding" in getattr(r, "path", "")}
    assert not any("eliminar" in p or "nuevo" in p or "nueva" in p for p in paths)         # sin alta manual ni delete


def test_toggle_route_persists_and_returns_the_checklist_fragment_with_derived_progress():
    bid = _busqueda()
    _pid, aid = _hire(bid)
    c = _client()
    frag = c.post(f"/cchh/onboarding/{aid}/checklist/nda", data={"checked": "1"}).text
    assert "<html" not in frag and 'id="hc-onboarding-checklist"' in frag and 'data-onboarding-item="nda" checked' in frag and "1/17 · 6%" in frag
    assert onboarding_repository.get(aid)["checklist"]["nda"] is True
    frag = c.post(f"/cchh/onboarding/{aid}/checklist/nda", data={}).text                    # un casillero destildado no envía el campo
    assert 'data-onboarding-item="nda" checked' not in frag and "0/17 · 0%" in frag
    assert onboarding_repository.get(aid)["checklist"]["nda"] is False
    assert c.post(f"/cchh/onboarding/{aid}/checklist/inventado", data={"checked": "1"}).status_code == 404
    assert c.post("/cchh/onboarding/no-existe/checklist/nda", data={"checked": "1"}).status_code == 404


def test_toggling_the_last_item_completes_the_onboarding_in_the_ui():
    bid = _busqueda()
    _pid, aid = _hire(bid)
    c = _client()
    for k in onboarding_checklist.ITEM_KEYS:
        frag = c.post(f"/cchh/onboarding/{aid}/checklist/{k}", data={"checked": "1"}).text
    assert "Completado · 17/17" in frag and onboarding_repository.get(aid)["completado"] is True
    assert "status-badge--activo" in c.get("/cchh/onboarding").text


def test_edit_submit_saves_and_redirects_and_reports_validation_and_concurrency():
    _seed_area_consultora("Consultoria", "RRHH")
    bid = _busqueda()
    _pid, aid = _hire(bid)
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": _AUTHORIZED_EMAIL, "name": "O", "first_name": "O", "family_name": "T"}))
    r = c.post(f"/cchh/onboarding/{aid}/editar", data={"fecha_ingreso": "2026-10-09", "oferta_cerrada": "USD 3.000", "area": "RRHH", "expected_revision": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/cchh/onboarding"
    o = onboarding_repository.get(aid)
    assert (o["fecha_ingreso"], o["oferta_cerrada"], o["area"], o["revision"]) == ("2026-10-09", "USD 3.000", "RRHH", 2)
    bad = c.post(f"/cchh/onboarding/{aid}/editar", data={"fecha_ingreso": "", "oferta_cerrada": "", "area": "", "expected_revision": "2"})
    assert bad.status_code == 200 and "No se guardó" in bad.text and "La fecha de ingreso es obligatoria" in bad.text and f'action="/cchh/onboarding/{aid}/editar"' in bad.text
    stale = c.post(f"/cchh/onboarding/{aid}/editar", data={"fecha_ingreso": "2026-10-10", "oferta_cerrada": "", "area": "", "expected_revision": "1"})
    assert "cambió desde que abriste el formulario" in stale.text
    assert c.post(f"/cchh/onboarding/{aid}/editar", data={"fecha_ingreso": "2026-10-10", "expected_revision": "x"}).status_code == 400
    assert c.post("/cchh/onboarding/no-existe/editar", data={"fecha_ingreso": "2026-10-10", "expected_revision": "1"}).status_code == 404


def test_legacy_area_is_preserved_and_flagged_out_of_catalog_in_the_form():
    _seed_area_consultora("Consultoria", "RRHH")
    bid = _busqueda()
    _pid, aid = _hire(bid)
    onboarding_repository.set_area(aid, "OPERACIONES")
    html = _client().get(f"/cchh/onboarding/{aid}/editar").text
    assert '<option value="OPERACIONES" selected>OPERACIONES (fuera de catálogo)</option>' in html and "Valor legacy fuera del catálogo vigente" in html


# ── señal en Búsquedas ───────────────────────────────────────────────────────

def test_busquedas_show_the_pending_signal_on_the_same_tile_active_and_closed_without_duplicating_it():
    b_open = _busqueda("Proceso Abierto")
    _hire(b_open, "Pend", "Uno")
    b_done = _busqueda("Proceso Completo")
    _p, a_done = _hire(b_done, "Comp", "Uno")
    for k in onboarding_checklist.ITEM_KEYS:
        onboarding_repository.set_item(a_done, k, True)
    b_closed = _busqueda("Proceso Cerrado")
    _hire(b_closed, "Pend", "Dos")
    _hire(b_closed, "Pend", "Tres")
    human_capital_service.close_busqueda(b_closed)
    html = _client().get("/cchh/busquedas").text
    assert html.count("Proceso Abierto") == 1 and html.count("Proceso Cerrado") == 1 and html.count("Proceso Completo") == 1           # una sola vez cada proceso
    assert html.count("Onboarding pendiente") == 3                                           # chip del filtro + tile abierto + tile cerrado
    assert "Onboarding pendiente · 2</span>" in html                                          # el cerrado tiene 2 pendientes
    open_tile = html.split("Proceso Abierto")[1].split("</a>")[0]
    done_tile = html.split("Proceso Completo")[1].split("</a>")[0]
    assert "Onboarding pendiente" in open_tile and "Onboarding pendiente" not in done_tile  # completo => sin señal


def test_busquedas_filter_chip_counts_and_filters_active_processes_only():
    b1 = _busqueda("Con Pendiente")
    _hire(b1)
    b2 = _busqueda("Sin Onboarding")
    _add(b2, _profile())
    c = _client()
    frag = c.get("/cchh/busquedas?atencion=onboarding_pendiente", headers={"HX-Request": "true"}).text
    assert "Con Pendiente" in frag and "Sin Onboarding" not in frag and "Onboarding pendiente · 1" in frag and "Todas · 2" in frag
    assert "is-active" in frag.split("Onboarding pendiente · 1")[0].rsplit("<button", 1)[1]
    none = c.get("/cchh/busquedas?atencion=todas", headers={"HX-Request": "true"}).text
    assert "Con Pendiente" in none and "Sin Onboarding" in none


def test_busquedas_filter_chip_is_disabled_when_there_are_no_pending_onboardings():
    _busqueda("Sola")
    frag = _client().get("/cchh/busquedas", headers={"HX-Request": "true"}).text
    chip = frag.split("Onboarding pendiente · 0")[0].rsplit("<button", 1)[1]
    assert "disabled" in chip


def test_the_pending_signal_never_breaks_busquedas_when_onboarding_storage_fails(monkeypatch):
    b = _busqueda("Resiliente")
    _hire(b)

    def boom():
        raise onboarding_repository.OnboardingStorageError("no se pudo listar")
    monkeypatch.setattr(onboarding_repository, "list", boom)
    html = _client().get("/cchh/busquedas").text
    assert "Resiliente" in html and "Onboarding pendiente · 0" in html


def test_the_pending_definition_is_an_incomplete_onboarding_related_to_the_process():
    b = _busqueda()
    _p, aid = _hire(b)
    assert human_capital_service.onboarding_pending_by_busqueda() == {b: 1}
    for k in onboarding_checklist.ITEM_KEYS:
        onboarding_repository.set_item(aid, k, True)
    assert human_capital_service.onboarding_pending_by_busqueda() == {}
    onboarding_repository.set_item(aid, "dni", False)                                      # destildar lo devuelve a pendiente
    assert human_capital_service.onboarding_pending_by_busqueda() == {b: 1}
    assert human_capital_service.busqueda_attention({"estado": "activo", "pedido_formal_recibido": True, "fecha_apertura": "2026-09-01", "candidatos": []}, 2)["onboarding_pendiente"] == 2


def test_the_shared_topbar_refresh_button_and_workspace_shell_render_on_onboarding_pages():
    bid = _busqueda()
    _pid, aid = _hire(bid)
    c = _client()
    for url in ("/cchh/onboarding", f"/cchh/onboarding/{aid}/editar"):
        html = c.get(url).text
        assert 'hx-post="/cchh/catalogos/refresh"' in html and "CAPITAL HUMANO" in html, url


def test_the_post_hero_gap_is_the_same_24px_in_the_four_cchh_sections():
    """Owner UX: divisor del hero -> primer componente, mismo margen en Candidatos, Búsquedas, Entrevistas y Onboarding."""
    from pathlib import Path
    css = (Path(config.__file__).parent / "webapp" / "static" / "human_capital.css").read_text(encoding="utf-8")
    flat = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selector in (".module-hero + #hc-profiles-panel", ".module-hero + .busquedas-list", ".module-hero + #hc-entrevistas-panel", ".module-hero + #hc-onboarding-panel", ".module-hero + #hc-publicaciones-panel"):
        rule = next(r for r in re.findall(r"([^{}]+)\{([^}]*)\}", flat) if selector in [s.strip() for s in r[0].split(",")])
        assert "margin-top: 24px" in rule[1], selector
    assert ".dashboard-section { margin: 12px 0;" in (Path(config.__file__).parent / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")   # el 12px interno colapsa dentro de los 24px


# ── barra de filtros (Estado · Área · Buscar · ✕ Limpiar) ─────────────────────

def _fixture_rows():
    """Cuatro onboardings con áreas activa / legacy / sin área, para probar los tres filtros y su combinación."""
    _seed_area_consultora("Consultoria", "RRHH")
    bid = _busqueda("Analista de Datos BI")
    bid2 = _busqueda("Desarrollador Full-Stack")
    _p1, a1 = _hire(bid, "Gimena", "Alvarez")           # pendiente · Consultoria
    _p2, a2 = _hire(bid, "Joaquin", "Martinez")         # completo  · RRHH
    _p3, a3 = _hire(bid2, "Hernan", "Lopez")            # pendiente · legacy OPERACIONES
    _p4, a4 = _hire(bid2, "Maria", "Gonzalez")          # pendiente · sin área
    human_capital_service.update_onboarding(a1, 1, {"fecha_ingreso": "2026-09-14", "oferta_cerrada": "USD 2.400", "area": "Consultoria"}, allow_sensitive=True)
    human_capital_service.update_onboarding(a2, 1, {"fecha_ingreso": "2026-08-31", "oferta_cerrada": "", "area": "RRHH"})
    human_capital_service.update_onboarding(a3, 1, {"fecha_ingreso": "2026-10-05", "oferta_cerrada": "", "area": ""})
    onboarding_repository.set_area(a3, "OPERACIONES")                                       # legacy heredado de una entrevista vieja
    human_capital_service.update_onboarding(a4, 1, {"fecha_ingreso": "2026-10-20", "oferta_cerrada": "", "area": ""})
    for k in onboarding_checklist.ITEM_KEYS:
        onboarding_repository.set_item(a2, k, True)
    for k in onboarding_checklist.ITEM_KEYS[:7]:
        onboarding_repository.set_item(a1, k, True)
    return a1, a2, a3, a4


def _names(html: str) -> list[str]:
    return re.findall(r'dw-idcell__name">([^<]*)<', html)


def _get(c, **params):
    return c.get("/cchh/onboarding", params=params, headers={"HX-Request": "true"}).text


def _static(name: str) -> str:
    from pathlib import Path
    return (Path(__import__("webapp").__file__).parent / "static" / name).read_text(encoding="utf-8")


def test_filter_bar_uses_the_shared_toolbar_with_natural_width_selects_a_flexible_search_and_clear_last():
    _fixture_rows()
    html = _get(_client())
    bar = html.split('<form class="hc-profile-filters filter-toolbar-card"', 1)[1].split("</form>", 1)[0]
    order = [bar.index(x) for x in ('id="hc-onboarding-estado"', 'id="hc-onboarding-area"', 'id="hc-onboarding-q"', "✕ Limpiar")]
    assert order == sorted(order)                                                          # Estado · Área · Buscar · ✕ Limpiar
    assert bar.count("feedback-field--auto") == 2 and bar.count("feedback-field--wide") == 1       # selects naturales, buscar flexible
    assert "feedback-field--medium" not in bar and "feedback-field--compact" not in bar and "style=" not in bar     # sin anchos artificiales
    assert bar.count('hx-include="closest form"') == 3 and 'hx-trigger="keyup changed delay:500ms"' in bar and 'class="whatsnew-filter__clear"' in bar
    css = _static("tools.css")
    assert ".hc-profile-filters .feedback-field--auto { flex: 0 0 auto; }" in css
    assert "max-width: 640px) { .hc-profile-filters .feedback-field--auto { flex: 1 1 100%; } }" in css


def test_area_filter_options_come_from_the_governed_catalog_plus_legacy_and_sin_area():
    _fixture_rows()
    html = _get(_client())
    area = html.split('id="hc-onboarding-area"', 1)[1].split("</select>", 1)[0]
    opts = re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)<', area)
    assert opts == [("", "Todas"), ("Consultoria", "Consultoria"), ("RRHH", "RRHH"), ("OPERACIONES", "OPERACIONES (fuera de catálogo)"), ("__sin_area__", "Sin área")]
    _seed_area_consultora("Consultoria", "RRHH", "Finanzas")                                  # el catálogo cambia => el filtro lo refleja, sin código
    assert 'value="Finanzas"' in _get(_client())


def test_state_filter_pendientes_and_completos():
    _fixture_rows()
    c = _client()
    assert sorted(_names(_get(c, estado="pendientes"))) == ["Gimena Alvarez", "Hernan Lopez", "Maria Gonzalez"]
    assert _names(_get(c, estado="completos")) == ["Joaquin Martinez"]
    assert len(_names(_get(c, estado="todos"))) == 4


def test_area_filter_matches_active_legacy_and_missing_areas_exactly():
    _fixture_rows()
    c = _client()
    assert _names(_get(c, area="Consultoria")) == ["Gimena Alvarez"]
    assert _names(_get(c, area="RRHH")) == ["Joaquin Martinez"]
    assert _names(_get(c, area="OPERACIONES")) == ["Hernan Lopez"]                           # un legacy NO se oculta por no estar en el catálogo
    assert _names(_get(c, area="__sin_area__")) == ["Maria Gonzalez"]
    assert len(_names(_get(c))) == 4                                                          # "Todas": ninguno oculto, legacy y sin área incluidos


def test_search_is_case_insensitive_and_covers_candidate_puesto_area_dates_progress_and_offer():
    _fixture_rows()
    c = _client()
    assert _names(_get(c, q="gimena")) == ["Gimena Alvarez"] and _names(_get(c, q="  GIMENA   alvarez ")) == ["Gimena Alvarez"]      # case + whitespace
    assert sorted(_names(_get(c, q="full-stack"))) == ["Hernan Lopez", "Maria Gonzalez"]                                              # puesto
    assert _names(_get(c, q="consultoria")) == ["Gimena Alvarez"] and _names(_get(c, q="operaciones")) == ["Hernan Lopez"]            # área (activa y legacy)
    assert _names(_get(c, q="14-09-2026")) == ["Gimena Alvarez"] and _names(_get(c, q="2026-10-05")) == ["Hernan Lopez"]              # fecha visible DMY y ISO
    assert _names(_get(c, q="7/17")) == ["Gimena Alvarez"] and _names(_get(c, q="completado")) == ["Joaquin Martinez"]                # progreso tal cual se muestra
    assert _names(_get(c, q="usd 2.400")) == ["Gimena Alvarez"]                                                                       # oferta cerrada
    assert _get(c, q="zzz-no-existe").count('data-empty-kind="no_matches"') == 1


def test_the_three_filters_combine_with_and_and_clear_resets_them():
    _fixture_rows()
    c = _client()
    assert _names(_get(c, estado="pendientes", area="Consultoria", q="Gimena")) == ["Gimena Alvarez"]
    assert _names(_get(c, estado="completos", area="Consultoria", q="Gimena")) == []         # Estado excluye
    assert _names(_get(c, estado="pendientes", area="RRHH", q="Gimena")) == []                # Área excluye
    assert _names(_get(c, estado="pendientes", area="Consultoria", q="Joaquin")) == []        # Buscar excluye
    assert sorted(_names(_get(c, estado="pendientes", q="full-stack"))) == ["Hernan Lopez", "Maria Gonzalez"]
    panel = _get(c, estado="pendientes", area="Consultoria", q="Gimena")
    assert 'value="pendientes" selected' in panel and 'value="Consultoria" selected' in panel and 'value="Gimena"' in panel          # los valores sobreviven al swap
    cleared = c.get("/cchh/onboarding", headers={"HX-Request": "true"}).text                  # ✕ Limpiar = GET sin parámetros
    assert len(_names(cleared)) == 4 and 'value="todos" selected' in cleared and 'name="q" type="text" value=""' in cleared
    assert re.search(r'class="whatsnew-filter__clear" hx-get="/cchh/onboarding\?page_size=50" hx-target="#hc-onboarding-panel"', cleared)       # sin filtros, página 1, tamaño vigente


def test_the_counter_follows_the_dashboard_section_pattern_filtered_result():
    _fixture_rows()
    c = _client()
    assert "4 onboardings" in _get(c) and "3 onboardings" in _get(c, estado="pendientes")
    assert "1 onboarding<" in _get(c, area="RRHH").replace("</span>", "<")
    assert "0 onboardings" in _get(c, q="zzz")                                                # MISMO criterio que Candidatos: `profiles | length` (resultado filtrado)


# ── badges: primitivo transversal, sin CSS duplicado ─────────────────────────

def test_onboarding_progress_uses_the_shared_status_badge_with_the_hub_tones():
    a1, _a2, _a3, _a4 = _fixture_rows()
    html = _get(_client())
    assert '<span class="status-badge status-badge--activo">Completado</span>' in html
    assert '<span class="status-badge status-badge--tentativo">7/17 · 41%</span>' in html
    assert '<span class="status-badge status-badge--inactivo">0/17 · 0%</span>' in html
    edit = _client().get(f"/cchh/onboarding/{a1}/editar").text
    assert 'class="status-badge status-badge--tentativo" data-onboarding-progress' in edit


def test_cchh_templates_use_only_the_shared_badge_primitive_for_status_and_indicators():
    from pathlib import Path
    root = Path(__import__("webapp").__file__).parent
    templates_dir = [*(root / "templates" / "human_capital").glob("*.html"), *(root / "templates" / "partials").glob("human_capital_*.html")]
    for t in templates_dir:
        text = t.read_text(encoding="utf-8")
        assert "dashboard-status-badge" not in text, t.name                                    # no el pill de CRM mezclado con el badge de estado de CCHH/Catálogo
        assert "busqueda-signal" not in text, t.name                                           # las señales ya no tienen estilo local propio
    card = (root / "templates" / "partials" / "human_capital_busqueda_card.html").read_text(encoding="utf-8")
    assert card.count('class="status-badge status-badge--') >= 3                               # estancada / sin requerimiento / onboarding pendiente


def test_cchh_css_never_redefines_the_badge_shape_only_tone_modifiers():
    css = _static("human_capital.css")
    flat = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert ".busqueda-signal" not in flat and "dashboard-status-badge" not in flat
    modifiers = [(sel, body) for sel, body in re.findall(r"([^{}]*\.status-badge[^{}]*)\{([^}]*)\}", flat) if ".status-badge--" in sel]
    assert modifiers                                                                           # existen (potential, estancada)
    for sel, body in modifiers:                                                                # modificadores de tono: nunca forma
        assert "border-radius" not in body and "padding" not in body and "font" not in body, sel


def test_the_busquedas_tile_signals_render_with_the_shared_badge_and_keep_their_text():
    b = _busqueda("Proceso con señal")
    _hire(b)
    html = _client().get("/cchh/busquedas").text
    tile = html.split("Proceso con señal")[1].split("</a>")[0]
    assert '<span class="status-badge status-badge--tentativo">Onboarding pendiente</span>' in tile and "busqueda-signal" not in tile


def test_busquedas_tile_estado_and_candidate_count_use_the_shared_status_badge_keeping_their_colours():
    b = _busqueda("Proceso Estado")
    _add(b, _profile())
    closed = _busqueda("Proceso Cerrado Estado")
    human_capital_service.close_busqueda(closed)
    html = _client().get("/cchh/busquedas").text
    tile = html.split("Proceso Estado")[1].split("</a>")[0]
    assert '<span class="status-badge status-badge--activo">Activo</span>' in tile                                  # verde del primitivo (como antes)
    assert '<span class="status-badge status-badge--count">1 candidato</span>' in tile                              # dorado conservado vía modificador de color
    assert '<span class="status-badge status-badge--inactivo">Cerrado</span>' in html.split("Proceso Cerrado Estado")[1].split("</a>")[0]
    assert "busqueda-estado" not in html and "busqueda-card__count" not in html
    detail = _client().get(f"/cchh/busquedas/{b}").text
    assert '<span class="status-badge status-badge--activo">Activo</span>' in detail and "busqueda-estado" not in detail
    css = _static("human_capital.css")
    assert "busqueda-estado" not in css and "busqueda-card__count" not in css
    assert re.search(r"\.status-badge--count \{ color: #1f1f24; background: var\(--gold\); border-color: var\(--gold\); \}", css)
