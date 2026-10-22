"""I1 — modelo y servicio de SOLICITUDES de Operaciones (REG-CH-01) sobre la entidad única `Búsqueda`.

Cubre: contrato y normalización REG-CH-01, fechas (timestamps UTC / fechas de negocio ART), idempotencia por `submission_id` (incl. conflicto de payload y reintentos con resultado remoto
ambiguo), validación de UUID y seguridad de rutas, matriz de transiciones, revisión optimista y carreras, compensación de persistencia, búsquedas históricas, protección de las operaciones
actuales de CCHH (y campos inmutables), proyección segura hacia Operaciones, imposibilidad de borrado físico y mapeo de excepciones a respuestas controladas (sin HTTP 500).
Aislamiento: `tmp_path` + `remote_store` falso en memoria. Sin Spaces, SharePoint, Entra ni DigitalOcean."""
from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from core import remote_store
from webapp.main import app
from webapp.services import auth_service, busqueda_repository as repo, human_capital_service as svc, profile_repository
from webapp.services import human_capital_catalog_repository as catalog_repo

CCHH_EMAIL = "cchh-i1-test@danwor.com"
OPS_A, OPS_B = "ops.a@danwor.com", "ops.b@danwor.com"
SID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
SID2 = "8d1f1a6e-2b53-4c1d-9b1e-6a3a5c8e7f10"


class FakeRemote:
    """remote_store falso en memoria. `enabled=False` => comportamiento local (push OK). Con `enabled=True`: `push_script` decide cada PUT (lista de bools; agotada => True)."""

    def __init__(self):
        self.enabled = False
        self.objects: dict[str, bytes] = {}
        self.push_script: list = []          # cada item: True (guarda y ok) | False (NO guarda) | "lost" (guarda pero responde False)
        self.read_error = False
        self.delete_fails = False
        self.pushes: list[str] = []

    def push_path(self, path):
        self.pushes.append(Path(path).name)
        mode = self.push_script.pop(0) if self.push_script else True
        if mode is True or mode == "lost":
            self.objects[Path(path).name] = Path(path).read_bytes()
        return mode is True

    def read_object_bytes(self, path):
        if self.read_error:
            raise remote_store.RemoteReadError("indeterminable")
        return self.objects.get(Path(path).name)

    def delete_path(self, path):
        if self.delete_fails:
            return False
        self.objects.pop(Path(path).name, None)
        return True


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", CCHH_EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    fake = FakeRemote()
    rs = repo.remote_store
    monkeypatch.setattr(rs, "enabled", lambda: fake.enabled)
    monkeypatch.setattr(rs, "push_path", fake.push_path)
    monkeypatch.setattr(rs, "read_object_bytes", fake.read_object_bytes)
    monkeypatch.setattr(rs, "delete_path", fake.delete_path)
    monkeypatch.setattr(rs, "await_restore", lambda timeout: "completed")
    seed_catalog(area_consultora=["SAP", "Cloud"], seniority=["SSR", "SR", "JR"], modalidad_contratacion=["Full-Time", "Part-Time"])
    return fake


@pytest.fixture()
def remote(env):
    return env


def seed_catalog(**cats):
    snap = catalog_repo.build_snapshot({n: [{"valor": v, "orden": i + 1} for i, v in enumerate(vals)] for n, vals in cats.items()},
                                       refreshed_by="test", source={"drive_id": "d", "item_id": "i"})
    p = catalog_repo.snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


def req(**over) -> dict:
    base = {
        "nombre_puesto": "Consultor SAP FI", "tipo_busqueda": "proyecto", "proyecto": "Migración S/4", "cantidad_recursos": 2,
        "dedicacion_valor": "40", "dedicacion_unidad": "horas_por_semana", "duracion_tipo": "meses", "duracion_meses": 6,
        "area_consultora": "SAP", "ubicacion": "Buenos Aires", "seniority": ["SSR", "SR"], "modalidad": "Híbrido", "dias_presenciales": 2,
        "lugar_trabajo": "Oficina del cliente, CABA", "responsabilidades": "Configurar FI.", "skills_excluyentes": "SAP FI, S/4HANA",
        "habilidades_blandas": "Comunicación", "ingles": "Intermedio", "certificaciones": "", "tipo_contrato": "Full-Time",
        "fecha_inicio_prevista": "2026-11-02", "comentarios": "",
    }
    base.update(over)
    return base


def send(sid=SID, actor=OPS_A, **over) -> dict:
    return svc.create_solicitud(sid, req(**over), actor)


def doc(bid=SID) -> dict:
    return repo.get(bid)


def rev(bid=SID) -> int:
    return doc(bid)["revision"]


def files() -> list[Path]:
    d = config.BASE_DIR / "human-capital" / "busquedas"
    return sorted(d.glob("*")) if d.exists() else []


def client() -> TestClient:
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": CCHH_EMAIL, "name": "C T", "first_name": "C", "family_name": "T"}))
    return c


def to_state(estado: str, sid=SID) -> dict:
    """Lleva una solicitud NUEVA al estado pedido por el camino legítimo."""
    send(sid)
    if estado == "enviada":
        pass
    elif estado == "observada":
        svc.observe_solicitud(sid, rev(sid), "Falta el proyecto", CCHH_EMAIL)
    elif estado == "reenviada":
        svc.observe_solicitud(sid, rev(sid), "Falta el proyecto", CCHH_EMAIL)
        svc.resubmit_solicitud(sid, rev(sid), req(comentarios="corregido"), OPS_A)
    elif estado == "en_gestion":
        svc.take_solicitud(sid, rev(sid), CCHH_EMAIL)
    elif estado == "cancelada":
        svc.cancel_solicitud(sid, rev(sid), OPS_A)
    else:
        raise AssertionError(estado)
    return doc(sid)


# ═══════════════════════ contrato y normalización REG-CH-01 ═══════════════════════
def test_a_valid_submission_persists_the_full_contract_in_the_single_busqueda_entity():
    view = send()
    d = doc()
    assert d["busqueda_id"] == SID == d["solicitud"]["submission_id"] and d["revision"] == 1 and d["estado"] == "activo" and d["candidatos"] == [] and d["fecha_cierre"] is None
    assert d["origen"]["tipo"] == "operaciones" and d["origen"]["creado_por"] == OPS_A and d["pedido_formal_recibido"] is True and d["schema_version"] == 1
    s = d["solicitud"]
    assert s["estado"] == "enviada" and s["solicitante"] == OPS_A and s["enviada_en"] == s["ultimo_envio_en"] and s["observacion"] is None
    assert [h["transicion"] for h in s["historial"]] == ["enviar"] and s["historial"][0]["actor_tipo"] == "operaciones" and s["historial"][0]["desde"] is None
    r = d["requerimiento"]
    assert r["dedicacion"] == {"valor": 40, "unidad": "horas_por_semana"} and r["duracion"] == {"tipo": "meses", "meses": 6} and r["seniority"] == ["SSR", "SR"]
    assert r["presencialidad"] == {"dias": 2, "lugar": "Oficina del cliente, CABA"} and r["cantidad_recursos"] == 2 and r["tipo_contrato"] == "Full-Time" and r["ingles"] == "Intermedio"
    assert (d["nombre_puesto"], d["area_consultora"], d["modalidad"]) == (r["nombre_puesto"], r["area_consultora"], r["modalidad"]) == ("Consultor SAP FI", "SAP", "Híbrido")   # espejo
    assert view["busqueda_id"] == SID and view["estado_solicitud"] == "enviada" and view["puede_editar"] and not view["solo_lectura"]
    assert len(files()) == 1


def test_area_consultora_is_the_area_of_belonging_and_area_departamento_is_not_used():
    send()
    d = doc()
    assert d["area_consultora"] == "SAP" and d["area_departamento"] is None and "area_departamento" not in d["requerimiento"]


def test_normalization_of_text_numbers_units_and_conditional_fields():
    send(nombre_puesto="  Consultor   SAP  ", dedicacion_valor="20,5", dedicacion_unidad="porcentaje_jornada", seniority="SR", tipo_busqueda="interna", proyecto="ignorado",
         modalidad="Remoto", dias_presenciales=3, lugar_trabajo="no aplica", duracion_tipo="indefinida", duracion_meses=99, cantidad_recursos="3")
    r = doc()["requerimiento"]
    assert r["nombre_puesto"] == "Consultor SAP" and r["dedicacion"] == {"valor": 20.5, "unidad": "porcentaje_jornada"} and r["seniority"] == ["SR"]
    assert r["proyecto"] is None and r["presencialidad"] is None and r["duracion"] == {"tipo": "indefinida", "meses": None} and r["cantidad_recursos"] == 3
    assert r["modalidad"] == "Remoto" and doc()["modalidad"] == "Remoto"


@pytest.mark.parametrize("modalidad", ["Híbrido", "On site"])
def test_presencial_modalities_require_the_exact_place(modalidad):
    for empty in (None, "", "   "):
        with pytest.raises(ValueError):
            send(modalidad=modalidad, lugar_trabajo=empty)
    assert files() == []


def test_hybrid_requires_1_to_5_presencial_days_and_the_place():
    for bad in (None, "", 0, 6, 7, 8, -1, "x", 2.5, True):
        with pytest.raises(ValueError):
            send(modalidad="Híbrido", dias_presenciales=bad)
    assert files() == []
    for n in (1, 5, "3"):
        sid = str(uuid.uuid4())
        assert send(sid=sid, modalidad="Híbrido", dias_presenciales=n, lugar_trabajo="Oficina CABA")["requerimiento"]["presencialidad"] == {"dias": int(n), "lugar": "Oficina CABA"}


@pytest.mark.parametrize("dias", [None, "", 0, 3, 7, 99, "x"])
def test_on_site_requires_the_place_but_not_the_presencial_days(dias):
    v = send(modalidad="On site", dias_presenciales=dias, lugar_trabajo="Planta del cliente")
    assert v["requerimiento"]["presencialidad"] == {"dias": None, "lugar": "Planta del cliente"}
    assert doc()["requerimiento"]["presencialidad"] == {"dias": None, "lugar": "Planta del cliente"}


def test_on_site_days_do_not_change_the_idempotency_fingerprint():
    send(modalidad="On site", dias_presenciales=3, lugar_trabajo="Planta")
    again = send(modalidad="On site", dias_presenciales=None, lugar_trabajo="Planta")   # mismo contenido normalizado: replay, no conflicto
    assert again["busqueda_id"] == SID and len(files()) == 1


@pytest.mark.parametrize("ubicacion", ["Indistinto", "indistinto", "Buenos Aires", "Remoto LATAM"])
@pytest.mark.parametrize("modalidad", ["Remoto", "Híbrido", "On site"])
def test_ubicacion_is_required_in_every_modality_and_indistinto_is_accepted(modalidad, ubicacion):
    v = send(modalidad=modalidad, ubicacion=ubicacion)
    assert v["requerimiento"]["ubicacion"] == ubicacion and doc()["requerimiento"]["ubicacion"] == ubicacion


@pytest.mark.parametrize("modalidad", ["Remoto", "Híbrido", "On site"])
@pytest.mark.parametrize("empty", [None, "", "   "])
def test_ubicacion_cannot_be_empty_even_when_remote(modalidad, empty):
    with pytest.raises(ValueError, match="ubicación"):
        send(modalidad=modalidad, ubicacion=empty)
    assert files() == []


def test_remote_needs_no_presencialidad_inputs():
    v = send(modalidad="Remoto", dias_presenciales=None, lugar_trabajo="")
    assert v["requerimiento"]["presencialidad"] is None


@pytest.mark.parametrize("unit, hi", [("horas_por_mes", 744), ("horas_por_semana", 168), ("porcentaje_jornada", 100)])
def test_dedication_units_and_their_limits(unit, hi):
    assert send(dedicacion_unidad=unit, dedicacion_valor=hi)["requerimiento"]["dedicacion"] == {"valor": hi, "unidad": unit}
    with pytest.raises(ValueError):
        send(sid=SID2, dedicacion_unidad=unit, dedicacion_valor=hi + 1)


def test_hours_per_day_is_not_an_accepted_dedication_unit():
    assert "horas_por_dia" not in svc.DEDICACION_UNIDADES and set(svc.DEDICACION_UNIDADES) == {"horas_por_mes", "horas_por_semana", "porcentaje_jornada"}
    with pytest.raises(ValueError):
        send(dedicacion_unidad="horas_por_dia", dedicacion_valor=8)
    assert files() == []


@pytest.mark.parametrize("field", ["nombre_puesto", "tipo_busqueda", "cantidad_recursos", "dedicacion_valor", "dedicacion_unidad", "duracion_tipo", "area_consultora", "ubicacion", "seniority",
                                   "modalidad", "responsabilidades", "skills_excluyentes", "ingles", "tipo_contrato", "fecha_inicio_prevista"])
def test_every_required_field_is_enforced(field):
    for empty in (None, "", "   ", []):
        with pytest.raises(ValueError):
            send(**{field: empty})
    assert files() == []


@pytest.mark.parametrize("over", [
    {"tipo_busqueda": "otro"}, {"tipo_busqueda": "proyecto", "proyecto": ""}, {"cantidad_recursos": 0}, {"cantidad_recursos": 100}, {"cantidad_recursos": "x"}, {"cantidad_recursos": True},
    {"cantidad_recursos": 1.5}, {"dedicacion_valor": 0}, {"dedicacion_valor": -3}, {"dedicacion_valor": "abc"}, {"duracion_meses": 0}, {"duracion_meses": 121}, {"duracion_tipo": "años"},
    {"modalidad": "Presencial"}, {"ingles": "Nativo"}, {"tipo_contrato": "Temporal"}, {"fecha_inicio_prevista": "2026-13-01"}, {"fecha_inicio_prevista": "02/11/2026"},
    {"area_consultora": "Fantasía"}, {"seniority": ["Senior Extremo"]}, {"dias_presenciales": 0}, {"dias_presenciales": 6}, {"dias_presenciales": 8}, {"nombre_puesto": "x" * 151},
    {"responsabilidades": "x" * 4001}, {"nombre_puesto": 123}, {"seniority": [3]},
])
def test_invalid_values_are_rejected_before_anything_is_written(over):
    with pytest.raises(ValueError):
        send(**over)
    assert files() == []


def test_a_planned_start_date_in_the_past_is_allowed():
    assert send(fecha_inicio_prevista="2020-01-15")["requerimiento"]["fecha_inicio_prevista"] == "2020-01-15"


def test_a_non_dict_payload_is_rejected():
    for bad in (None, [], "x", 3):
        with pytest.raises(ValueError):
            svc.create_solicitud(SID, bad, OPS_A)


# ═══════════════════════ fechas: técnicos UTC, de negocio ART ═══════════════════════
def _freeze(monkeypatch, utc_dt):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc_dt.astimezone(tz) if tz else utc_dt
    monkeypatch.setattr(repo, "datetime", FakeDT)


def test_business_dates_use_buenos_aires_and_technical_timestamps_stay_utc(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 10, 22, 1, 30, tzinfo=timezone.utc))      # 22:30 del 21/10 en Buenos Aires
    send()
    d = doc()
    assert d["fecha_apertura"] == "2026-10-21" and d["solicitud"]["enviada_en"].startswith("2026-10-22T01:30") and d["created_at"].startswith("2026-10-22T01:30")
    assert d["solicitud"]["enviada_en"].endswith("+00:00")
    svc.cancel_solicitud(SID, rev(), OPS_A)
    assert doc()["fecha_cierre"] == "2026-10-21" and doc()["solicitud"]["cancelada_en"].startswith("2026-10-22T01:30")


def test_fecha_apertura_is_fixed_from_the_first_submission_through_edits_and_resubmissions(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc))
    send()
    svc.edit_solicitud(SID, rev(), req(comentarios="v2"), OPS_A)
    assert doc()["fecha_apertura"] == "2026-10-20"
    svc.observe_solicitud(SID, rev(), "Aclarar", CCHH_EMAIL)
    _freeze(monkeypatch, datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc))
    svc.resubmit_solicitud(SID, rev(), req(comentarios="v3"), OPS_A)
    d = doc()
    assert d["fecha_apertura"] == "2026-10-20" and d["solicitud"]["ultimo_envio_en"].startswith("2026-10-25") and d["solicitud"]["enviada_en"].startswith("2026-10-20")


def test_close_uses_the_business_date_too(monkeypatch):
    b = svc.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"}, actor=CCHH_EMAIL)
    _freeze(monkeypatch, datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc))
    assert svc.close_busqueda(b["busqueda_id"])["fecha_cierre"] == "2026-10-21"


# ═══════════════════════ idempotencia por submission_id ═══════════════════════
def test_the_same_submission_twice_returns_the_same_request_without_duplicating():
    first = send()
    again = send()
    assert again["busqueda_id"] == first["busqueda_id"] and again["revision"] == first["revision"] == 1 and len(files()) == 1 and len(doc()["solicitud"]["historial"]) == 1


def test_the_same_id_with_different_content_is_a_conflict_and_the_stored_request_is_untouched():
    send()
    before = repo.get(SID)
    with pytest.raises(repo.BusquedaSubmissionConflictError):
        send(nombre_puesto="Otro perfil")
    assert repo.get(SID) == before and len(files()) == 1


def test_the_same_id_by_a_different_requester_is_a_conflict():
    send()
    with pytest.raises(repo.BusquedaSubmissionConflictError):
        send(actor=OPS_B)


def test_an_id_that_belongs_to_a_non_solicitud_busqueda_is_a_conflict_and_that_busqueda_is_untouched():
    b = svc.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"}, actor=CCHH_EMAIL)
    before = repo.get(b["busqueda_id"])
    with pytest.raises(repo.BusquedaSubmissionConflictError):
        send(sid=b["busqueda_id"])
    assert repo.get(b["busqueda_id"]) == before


def test_a_replay_of_an_accepted_submission_does_not_depend_on_catalogs_that_changed_later():
    send()
    seed_catalog(area_consultora=["Otra"], seniority=["X"])                    # SAP / SSR / SR ya no están en los catálogos vigentes
    with pytest.raises(ValueError):
        send(sid=SID2)                                                          # un envío NUEVO con esos valores sí falla...
    again = send()                                                              # ...pero el reenvío del ya aceptado devuelve el existente
    assert again["busqueda_id"] == SID and len(files()) == 1 and rev() == 1


def test_a_different_or_malformed_payload_for_an_accepted_id_is_a_conflict_never_silently_accepted_nor_a_validation_error():
    send()
    seed_catalog(area_consultora=["Otra"], seniority=["X"])
    for bad in (req(comentarios="distinto"), req(dedicacion_unidad="horas_por_dia"), {"nombre_puesto": "x"}, {}):
        with pytest.raises(repo.BusquedaSubmissionConflictError):
            svc.create_solicitud(SID, bad, OPS_A)
    assert rev() == 1


@pytest.mark.parametrize("state", ["en_gestion", "cancelada", "observada"])
def test_replaying_the_original_submission_later_returns_the_current_state_and_changes_nothing(state):
    to_state(state)
    before = repo.get(SID)
    view = send()
    assert view["estado_solicitud"] == state and repo.get(SID) == before


def test_replaying_after_an_edit_returns_the_current_edited_request_and_the_edited_content_is_a_conflict():
    send()
    svc.edit_solicitud(SID, rev(), req(comentarios="editado"), OPS_A)
    assert send()["requerimiento"]["comentarios"] == "editado"                 # el payload ORIGINAL sigue siendo equivalente (huella inmutable)
    with pytest.raises(repo.BusquedaSubmissionConflictError):
        send(comentarios="editado")                                            # el contenido editado NO es el envío original


# ═══════════════════════ UUID y seguridad de rutas ═══════════════════════
@pytest.mark.parametrize("bad", [None, 123, "", "   ", "abc", "../../etc/passwd", "..\\..\\x", "3f2504e0-4f89-41d3-9a0c", "00000000-0000-0000-0000-000000000000", "{3f2504e0-4f89-41d3-9a0c-0305e82c3301}",
                                 "urn:uuid:3f2504e0-4f89-41d3-9a0c-0305e82c3301", "3f2504e04f8941d39a0c0305e82c3301", "3f2504e0-4f89-41d3-9a0c-0305e82c3301/../x", "x" * 300])
def test_invalid_submission_ids_never_reach_the_disk(bad):
    with pytest.raises(repo.BusquedaInvalidSubmissionIdError):
        svc.create_solicitud(bad, req(), OPS_A)
    assert files() == [] and not (config.BASE_DIR.parent / "x").exists()


def test_a_valid_uuid_in_other_notations_is_normalized_to_the_canonical_lowercase_form():
    view = svc.create_solicitud(SID.upper(), req(), OPS_A)
    assert view["busqueda_id"] == SID and [f.name for f in files()] == [f"{SID}.json"]
    assert send()["revision"] == 1 and len(files()) == 1


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "", "x" * 65, ".", "..", "a b", None, 5])
def test_get_and_path_building_reject_ids_that_cannot_be_file_names(bad):
    assert repo.get(bad) is None
    with pytest.raises(ValueError):
        repo._busqueda_path(bad)


def test_every_transition_rejects_unsafe_ids_as_not_found_without_touching_the_disk():
    for fn, args in ((svc.take_solicitud, (1, CCHH_EMAIL)), (svc.cancel_solicitud, (1, OPS_A)), (svc.observe_solicitud, (1, "m", CCHH_EMAIL))):
        with pytest.raises(repo.BusquedaNotFoundError):
            fn("../../x", *args)
    assert files() == []


# ═══════════════════════ reintentos remotos y compensación de persistencia ═══════════════════════
def test_a_lost_acknowledgement_on_create_whose_cleanup_removes_the_remote_copy_is_recreated_cleanly_on_retry(remote):
    remote.enabled = True
    remote.push_script = ["lost", "lost"]                                      # el PUT llegó pero nunca se confirmó; el cleanup best-effort borra la copia remota
    with pytest.raises(repo.BusquedaRemoteOutcomeUnknownError):
        send()
    assert files() == [] and f"{SID}.json" not in remote.objects
    again = send()                                                              # mismo submission_id: se crea UNA vez, sin duplicar
    assert again["busqueda_id"] == SID and len(files()) == 1 and rev() == 1 and len(doc()["solicitud"]["historial"]) == 1 and f"{SID}.json" in remote.objects


def test_a_lost_acknowledgement_on_create_whose_cleanup_fails_is_reconciled_on_retry_without_duplicating_or_overwriting(remote):
    remote.enabled = True
    remote.delete_fails = True                                                  # la copia remota SOBREVIVE al intento fallido
    remote.push_script = ["lost", "lost"]
    with pytest.raises(repo.BusquedaRemoteOutcomeUnknownError):
        send()
    assert files() == [] and f"{SID}.json" in remote.objects
    stored = remote.objects[f"{SID}.json"]
    pushes = len(remote.pushes)
    again = send()                                                              # reintento: reconcilia con el bucket, NO vuelve a crear ni a pisar
    assert again["busqueda_id"] == SID and remote.objects[f"{SID}.json"] == stored and len(remote.pushes) == pushes and len(files()) == 1 and rev() == 1
    with pytest.raises(repo.BusquedaSubmissionConflictError):
        send(nombre_puesto="Otro")                                              # y la identidad sigue protegida tras la reconciliación


def test_the_repository_itself_reconciles_with_the_bucket_before_creating_even_without_the_service_precheck(remote):
    remote.enabled = True
    remote.delete_fails = True
    remote.push_script = ["lost", "lost"]
    shaped = svc._shape_requerimiento(req())
    with pytest.raises(repo.BusquedaRemoteOutcomeUnknownError):
        repo.create_solicitud(SID, shaped, OPS_A)
    stored, pushes = remote.objects[f"{SID}.json"], len(remote.pushes)
    again = repo.create_solicitud(SID, shaped, OPS_A)                           # reintento directo al repositorio: reconcilia, no crea de nuevo
    assert again["busqueda_id"] == SID and remote.objects[f"{SID}.json"] == stored and len(remote.pushes) == pushes and len(files()) == 1
    with pytest.raises(repo.BusquedaSubmissionConflictError):
        repo.create_solicitud(SID, svc._shape_requerimiento(req(comentarios="otro")), OPS_A)


def test_a_definitive_remote_failure_on_create_leaves_nothing_and_a_later_retry_creates_it(remote):
    remote.enabled = True
    remote.push_script = [False, False]
    with pytest.raises(repo.BusquedaRemoteOutcomeUnknownError):
        send()
    assert files() == [] and remote.objects == {}
    assert send()["busqueda_id"] == SID and f"{SID}.json" in remote.objects and rev() == 1


def test_an_undeterminable_remote_never_creates_blindly(remote):
    remote.enabled = True
    remote.read_error = True
    with pytest.raises(repo.BusquedaRemoteOutcomeUnknownError):
        send()
    assert files() == [] and remote.pushes == []


def test_an_unreadable_remote_object_is_never_overwritten(remote):
    remote.enabled = True
    remote.objects[f"{SID}.json"] = b"{no es json"
    with pytest.raises(repo.BusquedaRemoteOutcomeUnknownError):
        send()
    assert remote.objects[f"{SID}.json"] == b"{no es json" and files() == [] and remote.pushes == []


def test_a_transition_with_a_failed_remote_put_restores_the_previous_version_and_the_retry_does_not_duplicate_history(remote):
    send()
    remote.enabled = True
    before = repo.get(SID)
    remote.push_script = [False, True]                                          # el cambio no sube; la compensación (versión anterior) sí
    with pytest.raises(repo.BusquedaStorageError):
        svc.take_solicitud(SID, rev(), CCHH_EMAIL)
    assert repo.get(SID) == before and len(doc()["solicitud"]["historial"]) == 1
    svc.take_solicitud(SID, rev(), CCHH_EMAIL)                                  # reintento: una sola entrada nueva
    d = doc()
    assert d["solicitud"]["estado"] == "en_gestion" and [h["transicion"] for h in d["solicitud"]["historial"]] == ["enviar", "tomar"] and d["revision"] == 2


def test_a_transition_with_an_ambiguous_remote_outcome_restores_local_and_reports_unknown(remote):
    send()
    remote.enabled = True
    before = repo.get(SID)
    remote.push_script = [False, False]
    with pytest.raises(repo.BusquedaRemoteOutcomeUnknownError):
        svc.cancel_solicitud(SID, rev(), OPS_A)
    assert repo.get(SID) == before


def test_without_remote_configuration_writes_fail_deterministically_and_leave_no_residue(remote, monkeypatch):
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: False)
    with pytest.raises(repo.BusquedaConfigIncompleteError):
        send()
    assert files() == []


# ═══════════════════════ matriz de transiciones ═══════════════════════
ESTADOS = ["enviada", "observada", "en_gestion", "cancelada"]
OPS = {"editar": lambda sid, r: svc.edit_solicitud(sid, r, req(comentarios="e"), OPS_B),
       "reenviar": lambda sid, r: svc.resubmit_solicitud(sid, r, req(comentarios="r"), OPS_B),
       "cancelar": lambda sid, r: svc.cancel_solicitud(sid, r, OPS_B, "ya no se necesita"),
       "observar": lambda sid, r: svc.observe_solicitud(sid, r, "Falta información", CCHH_EMAIL),
       "tomar": lambda sid, r: svc.take_solicitud(sid, r, CCHH_EMAIL)}
HASTA = {"editar": "enviada", "reenviar": "enviada", "cancelar": "cancelada", "observar": "observada", "tomar": "en_gestion"}


def test_the_transition_table_is_exactly_the_approved_matrix():
    T = repo.SOLICITUD_TRANSICIONES
    assert set(T) == {"enviar", "editar", "observar", "reenviar", "tomar", "cancelar"}
    assert {k: (v["actor"], v["desde"], v["hasta"]) for k, v in T.items()} == {
        "enviar": ("operaciones", (None,), "enviada"), "editar": ("operaciones", ("enviada",), "enviada"), "observar": ("cchh", ("enviada",), "observada"),
        "reenviar": ("operaciones", ("observada",), "enviada"), "tomar": ("cchh", ("enviada",), "en_gestion"), "cancelar": ("operaciones", ("enviada", "observada"), "cancelada")}
    assert repo.SOLICITUD_ESTADOS == ("enviada", "observada", "en_gestion", "cancelada") and "reenviada" not in repo.SOLICITUD_ESTADOS


@pytest.mark.parametrize("estado", ESTADOS)
@pytest.mark.parametrize("op", list(OPS))
def test_full_transition_matrix(estado, op):
    to_state(estado)
    before = repo.get(SID)
    allowed = estado in repo.SOLICITUD_TRANSICIONES[op]["desde"]
    if allowed:
        out = OPS[op](SID, before["revision"])
        d = repo.get(SID)
        assert d["solicitud"]["estado"] == HASTA[op] and d["revision"] == before["revision"] + 1
        h = d["solicitud"]["historial"][-1]
        assert h["transicion"] == op and h["desde"] == estado and h["hasta"] == HASTA[op] and h["actor_tipo"] == repo.SOLICITUD_TRANSICIONES[op]["actor"] and h["fecha"] and h["actor"]
        assert out is not None
    else:
        with pytest.raises(repo.BusquedaInvalidTransitionError):
            OPS[op](SID, before["revision"])
        assert repo.get(SID) == before                                          # inválida: nada cambia (ni revisión ni historial)


def test_transition_side_effects_are_exact():
    send()
    d0 = doc()
    svc.edit_solicitud(SID, rev(), req(nombre_puesto="Consultor FI v2", modalidad="Remoto"), OPS_B)
    d = doc()
    assert d["requerimiento"]["nombre_puesto"] == "Consultor FI v2" == d["nombre_puesto"] and d["modalidad"] == "Remoto" and d["fecha_apertura"] == d0["fecha_apertura"]
    assert d["solicitud"]["ultimo_envio_en"] == d0["solicitud"]["ultimo_envio_en"] and d["solicitud"]["historial"][-1]["actor"] == OPS_B     # cualquier usuario de Operaciones gestiona
    svc.observe_solicitud(SID, rev(), "  Aclarar el alcance  ", CCHH_EMAIL)
    d = doc()
    assert d["solicitud"]["observacion"]["motivo"] == "Aclarar el alcance" and d["solicitud"]["observacion"]["por"] == CCHH_EMAIL and d["solicitud"]["historial"][-1]["motivo"] == "Aclarar el alcance"
    svc.resubmit_solicitud(SID, rev(), req(nombre_puesto="Consultor FI v3"), OPS_A)
    d = doc()
    assert d["solicitud"]["observacion"] is None and d["solicitud"]["ultimo_envio_en"] > d["solicitud"]["enviada_en"] and d["nombre_puesto"] == "Consultor FI v3"
    assert [h["transicion"] for h in d["solicitud"]["historial"]] == ["enviar", "editar", "observar", "reenviar"]
    svc.take_solicitud(SID, rev(), CCHH_EMAIL)
    d = doc()
    assert d["solicitud"]["tomada_por"] == CCHH_EMAIL and d["solicitud"]["tomada_en"] and d["estado"] == "activo" and d["requerimiento"]["nombre_puesto"] == "Consultor FI v3"


def test_cancel_closes_the_busqueda_keeps_the_trace_and_the_reason_is_optional():
    send()
    svc.cancel_solicitud(SID, rev(), OPS_A)                                     # sin motivo
    d = doc()
    assert d["estado"] == "cerrado" and d["fecha_cierre"] and d["solicitud"]["estado"] == "cancelada" and d["solicitud"]["motivo_cancelacion"] is None
    h = d["solicitud"]["historial"][-1]
    assert h["transicion"] == "cancelar" and h["actor"] == OPS_A and h["fecha"] and "motivo" not in h and d["solicitud"]["cancelada_por"] == OPS_A and d["solicitud"]["cancelada_en"]
    send(SID2)
    svc.cancel_solicitud(SID2, rev(SID2), OPS_A, "  Cambio de prioridades  ")
    assert doc(SID2)["solicitud"]["motivo_cancelacion"] == "Cambio de prioridades" and doc(SID2)["solicitud"]["historial"][-1]["motivo"] == "Cambio de prioridades"


def test_an_observation_requires_a_reason_and_every_operation_requires_an_actor():
    send()
    for bad in (None, "", "   ", 5):
        with pytest.raises(ValueError):
            svc.observe_solicitud(SID, rev(), bad, CCHH_EMAIL)
    for bad_actor in (None, "", "  ", 3):
        for fn in (lambda a: svc.take_solicitud(SID, rev(), a), lambda a: svc.cancel_solicitud(SID, rev(), a), lambda a: svc.edit_solicitud(SID, rev(), req(), a),
                   lambda a: svc.observe_solicitud(SID, rev(), "m", a)):
            with pytest.raises(ValueError):
                fn(bad_actor)
    assert rev() == 1 and len(doc()["solicitud"]["historial"]) == 1
    with pytest.raises(ValueError):
        svc.observe_solicitud(SID, rev(), "m" * 1001, CCHH_EMAIL)


def test_a_non_solicitud_busqueda_rejects_every_transition():
    b = svc.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"}, actor=CCHH_EMAIL)
    for name, fn in OPS.items():
        with pytest.raises(repo.BusquedaInvalidTransitionError):
            fn(b["busqueda_id"], b["revision"])


def test_resubmitted_indicator_and_categories_are_derived_not_states():
    assert to_state("enviada")["solicitud"]["estado"] == "enviada" and svc.get_solicitud_view(SID)["reenviada"] is False and svc.get_solicitud_view(SID)["categoria"] == "pendiente"
    to_state("reenviada", SID2)
    v = svc.get_solicitud_view(SID2)
    assert v["estado_solicitud"] == "enviada" and v["reenviada"] is True and v["reenvios"] == 1 and v["categoria"] == "pendiente"
    svc.take_solicitud(SID2, rev(SID2), CCHH_EMAIL)
    v = svc.get_solicitud_view(SID2)
    assert v["reenviada"] is False and v["reenvios"] == 1 and v["categoria"] == "gestionada"


# ═══════════════════════ revisión optimista y carreras ═══════════════════════
@pytest.mark.parametrize("bad", [None, True, "1", 1.0, [], {}])
def test_expected_revision_is_mandatory_and_must_be_an_integer(bad):
    send()
    for fn in (lambda: svc.take_solicitud(SID, bad, CCHH_EMAIL), lambda: svc.cancel_solicitud(SID, bad, OPS_A), lambda: svc.edit_solicitud(SID, bad, req(), OPS_A)):
        with pytest.raises(ValueError):
            fn()
    assert rev() == 1


def test_a_stale_revision_is_a_concurrency_error_for_every_transition():
    send()
    svc.edit_solicitud(SID, 1, req(comentarios="otro"), OPS_B)                  # alguien avanzó la revisión
    for fn in (lambda: svc.edit_solicitud(SID, 1, req(), OPS_A), lambda: svc.cancel_solicitud(SID, 1, OPS_A), lambda: svc.take_solicitud(SID, 1, CCHH_EMAIL),
               lambda: svc.observe_solicitud(SID, 1, "m", CCHH_EMAIL)):
        with pytest.raises(repo.BusquedaConcurrentModificationError):
            fn()
    assert rev() == 2 and doc()["solicitud"]["estado"] == "enviada"


def _race(a, b):
    barrier = threading.Barrier(2)

    def run(fn):
        barrier.wait()
        try:
            return ("ok", fn())
        except Exception as exc:                                                # noqa: BLE001
            return ("err", exc)
    with ThreadPoolExecutor(2) as ex:
        return [f.result() for f in (ex.submit(run, a), ex.submit(run, b))]


@pytest.mark.parametrize("a, b", [("tomar", "tomar"), ("tomar", "editar"), ("tomar", "cancelar"), ("cancelar", "editar"), ("observar", "tomar")])
def test_races_on_the_same_revision_have_exactly_one_winner_and_a_consistent_history(a, b):
    send()
    r0 = rev()
    out = _race(lambda: OPS[a](SID, r0), lambda: OPS[b](SID, r0))
    assert sorted(o[0] for o in out) == ["err", "ok"]
    loser = next(o[1] for o in out if o[0] == "err")
    assert isinstance(loser, (repo.BusquedaConcurrentModificationError, repo.BusquedaInvalidTransitionError))
    d = doc()
    assert d["revision"] == r0 + 1 and len(d["solicitud"]["historial"]) == 2 and d["solicitud"]["estado"] in ("enviada", "observada", "en_gestion", "cancelada")


def test_concurrent_identical_submissions_create_exactly_one_request():
    out = _race(lambda: send(), lambda: send())
    assert [o[0] for o in out] == ["ok", "ok"] and len(files()) == 1 and rev() == 1 and len(doc()["solicitud"]["historial"]) == 1


# ═══════════════════════ búsquedas históricas y alta directa de CCHH ═══════════════════════
def _historic() -> dict:
    """Documento con la forma previa a I1: sin origen/solicitud/requerimiento."""
    b = svc.create_busqueda({"nombre_puesto": "Histórica", "fecha_apertura": "2026-01-10", "area_consultora": "SAP", "modalidad": "Remoto"}, actor=CCHH_EMAIL)
    p = config.BASE_DIR / "human-capital" / "busquedas" / f"{b['busqueda_id']}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    for k in ("origen", "solicitud", "requerimiento"):
        d.pop(k, None)
    p.write_text(json.dumps(d), encoding="utf-8")
    return repo.get(b["busqueda_id"])


def test_historic_busquedas_keep_working_without_migration_and_no_author_is_invented():
    h = _historic()
    bid = h["busqueda_id"]
    assert "origen" not in h and "solicitud" not in h and repo.origen_tipo(h) is None and repo.solicitud_of(h) is None and svc.solicitud_view(h) is None
    assert repo.es_gestionada(h) and repo.solicitud_categoria(h) is None and bid in {b["busqueda_id"] for b in svc.list_busquedas()} and bid not in {v["busqueda_id"] for v in svc.list_solicitudes_view()}
    pid = profile_repository.create({"first_name": "C", "last_name": "I", "email": "c@danwor.com"})["profile_id"]
    assert svc.update_busqueda(bid, h["revision"], {"nombre_puesto": "Histórica 2", "fecha_apertura": "2026-02-02", "area_consultora": "SAP", "pedido_formal_recibido": True})["fecha_apertura"] == "2026-02-02"
    assert svc.add_candidate_to_busqueda(bid, pid)["candidatos"][0]["profile_id"] == pid
    closed = svc.close_busqueda(bid)
    assert closed["estado"] == "cerrado" and "origen" not in repo.get(bid) and "solicitud" not in repo.get(bid)
    with pytest.raises(repo.BusquedaInvalidTransitionError):
        svc.take_solicitud(bid, repo.get(bid)["revision"], CCHH_EMAIL)


def test_direct_cchh_creation_records_its_origin_with_the_authenticated_actor():
    b = svc.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"}, actor="  Hc.User@Danwor.com ")
    assert b["origen"]["tipo"] == "cchh" and b["origen"]["creado_por"] == "hc.user@danwor.com" and b["origen"]["creado_en"] and "solicitud" not in b and "requerimiento" not in b
    assert repo.es_gestionada(b) and repo.solicitud_categoria(b) is None
    assert svc.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})["origen"]["creado_por"] is None            # sin actor no se inventa uno


def test_the_cchh_create_route_passes_the_authenticated_user_as_the_author():
    r = client().post("/cchh/busquedas/nueva", data={"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"})
    assert r.status_code == 303
    bid = r.headers["location"].rsplit("/", 1)[-1]
    assert repo.get(bid)["origen"] == {"tipo": "cchh", "creado_por": CCHH_EMAIL, "creado_en": repo.get(bid)["origen"]["creado_en"]}


def test_direct_altas_do_not_need_or_complete_reg_ch_01():
    b = svc.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"}, actor=CCHH_EMAIL)
    assert "requerimiento" not in b and "solicitud" not in b and b["pedido_formal_recibido"] is False


# ═══════════════════════ protección de las operaciones actuales de CCHH ═══════════════════════
def _cchh_ops(sid: str, pid: str):
    d = doc(sid)
    yield "update", lambda: svc.update_busqueda(sid, d["revision"], {"nombre_puesto": "X", "fecha_apertura": d["fecha_apertura"], "area_consultora": d["area_consultora"]})
    yield "add", lambda: svc.add_candidate_to_busqueda(sid, pid)
    yield "close", lambda: svc.close_busqueda(sid)
    yield "move", lambda: svc.move_application_stage(sid, "no-existe", "Entrevista CCHH", None)
    yield "remove", lambda: svc.remove_application(sid, "no-existe")


@pytest.mark.parametrize("estado", ["enviada", "observada", "cancelada"])
def test_cchh_cannot_edit_close_or_add_candidates_on_a_request_that_is_not_in_gestion(estado):
    to_state(estado)
    pid = profile_repository.create({"first_name": "C", "last_name": "I", "email": "c@danwor.com"})["profile_id"]
    before = repo.get(SID)
    for name, fn in _cchh_ops(SID, pid):
        with pytest.raises(repo.BusquedaInvalidTransitionError):
            fn()
    assert repo.get(SID) == before                                              # ni una escritura


def test_cchh_operations_work_once_the_request_is_in_gestion_and_the_closing_stays_coherent():
    to_state("en_gestion")
    pid = profile_repository.create({"first_name": "C", "last_name": "I", "email": "c@danwor.com"})["profile_id"]
    assert svc.add_candidate_to_busqueda(SID, pid)["candidatos"]
    d = doc()
    svc.update_busqueda(SID, d["revision"], {"nombre_puesto": "Ajuste CCHH", "fecha_apertura": d["fecha_apertura"], "area_consultora": "Cloud", "modalidad": "Remoto", "descripcion": "nota interna"})
    d = doc()
    assert d["nombre_puesto"] == "Ajuste CCHH" and d["area_consultora"] == "Cloud" and d["descripcion"] == "nota interna"
    assert d["requerimiento"]["nombre_puesto"] == "Consultor SAP FI" and d["requerimiento"]["area_consultora"] == "SAP"          # lo pedido NO cambia
    assert d["origen"]["tipo"] == "operaciones" and d["solicitud"]["estado"] == "en_gestion" and "submission_fingerprint" in d["solicitud"]
    closed = svc.close_busqueda(SID)
    assert closed["estado"] == "cerrado" and closed["solicitud"]["estado"] == "en_gestion"


def test_fecha_apertura_and_pedido_formal_cannot_be_changed_by_the_general_edit_when_the_origin_is_operations():
    to_state("en_gestion")
    d = doc()
    out = svc.update_busqueda(SID, d["revision"], {"nombre_puesto": "N", "fecha_apertura": "1999-01-01", "area_consultora": "SAP", "pedido_formal_recibido": False})
    assert out["fecha_apertura"] == d["fecha_apertura"] and out["pedido_formal_recibido"] is True and out["nombre_puesto"] == "N"
    # la regla vale también directo en el repositorio
    out = repo.update(SID, out["revision"], {"fecha_apertura": "1999-01-01", "pedido_formal_recibido": False, "descripcion": "x"})
    assert out["fecha_apertura"] == d["fecha_apertura"] and out["pedido_formal_recibido"] is True and out["descripcion"] == "x"


def test_a_direct_cchh_busqueda_keeps_its_fields_editable():
    b = svc.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"}, actor=CCHH_EMAIL)
    out = svc.update_busqueda(b["busqueda_id"], b["revision"], {"nombre_puesto": "Dev", "fecha_apertura": "2026-09-05", "pedido_formal_recibido": True})
    assert out["fecha_apertura"] == "2026-09-05" and out["pedido_formal_recibido"] is True


def test_a_cchh_edit_of_a_historical_or_direct_busqueda_never_loses_new_structures():
    to_state("en_gestion")
    d = doc()
    svc.update_busqueda(SID, d["revision"], {"nombre_puesto": "A", "fecha_apertura": d["fecha_apertura"], "area_consultora": "SAP"})
    out = doc()
    assert {"origen", "solicitud", "requerimiento"} <= set(out) and out["solicitud"]["historial"] == d["solicitud"]["historial"] and out["requerimiento"] == d["requerimiento"]


# ═══════════════════════ listados y métricas (preparación para I3) ═══════════════════════
def test_services_distinguish_pending_observed_cancelled_and_effectively_managed_requests():
    to_state("enviada", "11111111-1111-4111-8111-111111111111")
    to_state("reenviada", "22222222-2222-4222-8222-222222222222")
    to_state("observada", "33333333-3333-4333-8333-333333333333")
    to_state("cancelada", "44444444-4444-4444-8444-444444444444")
    to_state("en_gestion", "55555555-5555-4555-8555-555555555555")
    h = _historic()
    direct = svc.create_busqueda({"nombre_puesto": "Directa", "fecha_apertura": "2026-09-01"}, actor=CCHH_EMAIL)
    assert svc.resumen_solicitudes() == {"pendiente": 2, "observada": 1, "gestionada": 1, "cancelada": 1, "reenviadas": 1, "total": 5}
    managed = {b["busqueda_id"] for b in svc.list_busquedas_gestionadas()}
    assert managed == {"55555555-5555-4555-8555-555555555555", h["busqueda_id"], direct["busqueda_id"]}
    assert len(svc.list_busquedas()) == 7                                       # list_busquedas NO cambia: sigue devolviendo todo (I3 decide la superficie)
    assert len(svc.list_solicitudes_view()) == 5


# ═══════════════════════ proyección segura hacia Operaciones ═══════════════════════
VIEW_KEYS = {"busqueda_id", "solicitud_id", "revision", "estado_solicitud", "estado", "fecha_apertura", "fecha_cierre", "nombre_puesto", "area_consultora", "modalidad", "solicitante",
             "enviada_en", "ultimo_envio_en", "observacion", "tomada_en", "cancelada_en", "motivo_cancelacion", "requerimiento", "historial", "categoria", "reenviada", "reenvios",
             "puede_editar", "puede_corregir", "puede_cancelar", "solo_lectura"}


def test_the_operations_projection_is_an_exact_whitelist_without_candidates_or_internal_data():
    to_state("en_gestion")
    pid = profile_repository.create({"first_name": "Cand", "last_name": "Secreto", "email": "secreto@candidato.com"})["profile_id"]
    d = doc()
    svc.update_busqueda(SID, d["revision"], {"nombre_puesto": "N", "fecha_apertura": d["fecha_apertura"], "area_consultora": "SAP", "jd_texto": "JD INTERNO", "palabras_clave": "CLAVES INTERNAS",
                                             "descripcion": "DESCRIPCION INTERNA"})
    svc.add_candidate_to_busqueda(SID, pid)
    view = svc.get_solicitud_view(SID)
    assert set(view) == VIEW_KEYS
    blob = json.dumps(view, ensure_ascii=False)
    for secret in (pid, "Secreto", "secreto@candidato.com", "JD INTERNO", "CLAVES INTERNAS", "DESCRIPCION INTERNA", "candidatos", "origen", "pedido_formal_recibido", "tomada_por", CCHH_EMAIL):
        assert secret not in blob
    assert OPS_A in blob                                                        # los actores de Operaciones sí se ven
    row = next(v for v in svc.list_solicitudes_view() if v["busqueda_id"] == SID)
    assert set(row) == VIEW_KEYS and CCHH_EMAIL not in json.dumps(row)


def test_the_operations_projection_hides_who_in_cchh_observed_and_exposes_only_the_reason():
    to_state("observada")
    v = svc.get_solicitud_view(SID)
    assert v["observacion"] == {"motivo": "Falta el proyecto", "en": doc()["solicitud"]["observacion"]["en"]} and CCHH_EMAIL not in json.dumps(v)
    obs = [h for h in v["historial"] if h["transicion"] == "observar"][0]
    assert "actor" not in obs and obs["actor_tipo"] == "cchh" and obs["motivo"] == "Falta el proyecto"


@pytest.mark.parametrize("estado, flags", [("enviada", (True, False, True, False)), ("observada", (False, True, True, False)), ("en_gestion", (False, False, False, True)), ("cancelada", (False, False, False, True))])
def test_the_projection_tells_operations_what_it_may_do_today(estado, flags):
    to_state(estado)
    v = svc.get_solicitud_view(SID)
    assert (v["puede_editar"], v["puede_corregir"], v["puede_cancelar"], v["solo_lectura"]) == flags


def test_a_request_originated_projection_is_none_for_non_requests_and_missing_ids():
    assert svc.get_solicitud_view("no-existe") is None and svc.get_solicitud_view("../x") is None
    b = svc.create_busqueda({"nombre_puesto": "Dev", "fecha_apertura": "2026-09-01"}, actor=CCHH_EMAIL)
    assert svc.get_solicitud_view(b["busqueda_id"]) is None


# ═══════════════════════ sin borrado físico ═══════════════════════
@pytest.mark.parametrize("estado", ["enviada", "observada", "en_gestion", "cancelada"])
def test_a_request_can_never_be_physically_deleted(estado, remote):
    d = to_state(estado)
    assert svc.busqueda_is_placeholder(d) is False
    with pytest.raises(ValueError):
        svc.delete_busqueda(SID)
    with pytest.raises(repo.BusquedaInvalidTransitionError):
        repo.delete(SID)                                                        # ni salteando el servicio
    assert repo.get(SID) == d and (config.BASE_DIR / "human-capital" / "busquedas" / f"{SID}.json").exists()


def test_an_ordinary_empty_busqueda_is_still_deletable_as_before():
    b = svc.create_busqueda({"nombre_puesto": "Placeholder", "fecha_apertura": "2026-09-01"}, actor=CCHH_EMAIL)
    svc.delete_busqueda(b["busqueda_id"])
    assert repo.get(b["busqueda_id"]) is None


# ═══════════════════════ mapeo de excepciones a respuestas controladas (sin 500) ═══════════════════════
def _edit_form(d: dict, **over) -> dict:
    f = {"nombre_puesto": "Cambio", "fecha_apertura": d.get("fecha_apertura") or "2026-09-01", "area_consultora": "SAP", "expected_revision": str(d["revision"])}
    f.update(over)
    return f


@pytest.mark.parametrize("estado", ["enviada", "observada", "cancelada"])
def test_cchh_routes_answer_with_controlled_responses_on_a_request_that_is_not_in_gestion(estado):
    d = to_state(estado)
    c = client()
    base = f"/cchh/busquedas/{SID}"
    pid = profile_repository.create({"first_name": "C", "last_name": "I", "email": "c@danwor.com"})["profile_id"]
    r = c.get(f"{base}/editar")
    assert r.status_code == 303 and r.headers["location"] == base               # sin edición: de vuelta al detalle
    r = c.post(f"{base}/editar", data=_edit_form(d))
    assert r.status_code == 200 and ("cancelada" if estado == "cancelada" else "tomarla en gestión") in r.text          # error de negocio visible en el form, no 500
    assert c.post(f"{base}/candidatos", data={"profile_id": pid}).status_code == 303
    assert c.post(f"{base}/candidatos", data={"profile_id": pid}, headers={"HX-Request": "true"}).status_code == 200
    assert c.post(f"{base}/cerrar").status_code == 303
    assert c.post(f"{base}/candidatos/zzz/etapa", data={"etapa": "Entrevista CCHH"}).status_code == 303
    assert c.post(f"{base}/candidatos/zzz/eliminar").status_code == 303
    r = c.post(f"{base}/eliminar")
    assert r.status_code == 303 and r.headers["location"] == base
    assert repo.get(SID) == d                                                   # nada cambió


def test_cchh_routes_render_the_detail_and_the_list_for_requests_in_every_state():
    for i, estado in enumerate(["enviada", "observada", "en_gestion", "cancelada"]):
        to_state(estado, f"{i + 1}{i + 1}{i + 1}{i + 1}{i + 1}{i + 1}{i + 1}{i + 1}-1111-4111-8111-111111111111")
    c = client()
    assert c.get("/cchh/busquedas").status_code == 200
    for f in files():
        assert c.get(f"/cchh/busquedas/{f.stem}").status_code == 200


def test_a_stale_revision_on_the_cchh_edit_is_the_concurrency_response_not_a_business_or_server_error():
    to_state("en_gestion")
    d = doc()
    r = client().post(f"/cchh/busquedas/{SID}/editar", data=_edit_form(d, expected_revision=str(d["revision"] - 1)))
    assert r.status_code == 200 and rev() == d["revision"]


def test_the_cchh_edit_route_works_for_a_request_in_gestion_and_ignores_the_locked_fields():
    to_state("en_gestion")
    d = doc()
    r = client().post(f"/cchh/busquedas/{SID}/editar", data=_edit_form(d, fecha_apertura="1999-01-01"))
    assert r.status_code == 303
    out = doc()
    assert out["nombre_puesto"] == "Cambio" and out["fecha_apertura"] == d["fecha_apertura"] and out["pedido_formal_recibido"] is True       # el form no tilda pedido_formal: se conserva


def test_new_exception_types_are_all_covered_by_the_router_mapping():
    from webapp.routers import human_capital as router
    covered = set(router._BUSQUEDA_SOLICITUD_ERRORS)
    assert covered == {repo.BusquedaInvalidTransitionError, repo.BusquedaSubmissionConflictError, repo.BusquedaInvalidSubmissionIdError}
    assert not (covered & set(router._BUSQUEDA_SHELL_ERRORS))                    # negocio, concurrencia y persistencia siguen siendo categorías distintas
    assert not issubclass(repo.BusquedaConcurrentModificationError, tuple(covered)) and not issubclass(repo.BusquedaStorageError, tuple(covered))
    assert issubclass(repo.BusquedaInvalidSubmissionIdError, ValueError)
