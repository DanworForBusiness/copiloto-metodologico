"""Datos sintéticos de 06 Indicadores (CCHH): constructores de documentos con la forma REAL de los repositorios del Hub y el mapeo a las entradas del original.

Un único dataset determinista (`build_raw_dataset`) alimenta (a) los tests del motor y (b) el oráculo diferencial contra `lib/metricas.ts` de la app de referencia
(`tests/webapp/data/cchh_indicators/`, ver su `regenerate.py`). El dataset se versiona como JSON: los tests NO lo regeneran (no dependen de la versión de `random`).

`to_original_inputs` traduce un dataset del Hub a las formas que consume la app original. La traducción es el contrato de equivalencia de dominio:
  - candidato(original) = candidatura (elemento de `busqueda.candidatos`);
  - `fecha_cambio_etapa` de un Ingreso = primera llegada a «Finalizado» del `historial` (corrección de `app/metricas/page.tsx`), o el valor crudo sin historial;
  - `modalidad_contratacion` sale de la entrevista del par (profile, búsqueda);
  - `puesto_evaluado` de la entrevista = el puesto mostrado (nombre de la búsqueda si hay `busqueda_id`, si no el texto de la entrevista) — el Hub lo deriva y no lo persiste.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from webapp.services.human_capital_indicators import Snapshot

UTC = timezone.utc
ETAPAS = ("Preselección", "Entrevista CCHH", "Entrevista Líder", "Decisión Final", "Finalizado")
CATALOGOS_PARIDAD = {"modalidad_contratacion": ("Full time", "Part time"), "canal_publicacion": ("LinkedIn", "Flyer")}


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def day(moment: datetime) -> str:
    return moment.astimezone(UTC).date().isoformat()


# ── constructores de documentos con la forma real de los repositorios ──────────────────────────────────────

def make_app(application_id: str, profile_id: str, *, etapa: str = "Preselección", resultado: str | None = None, alta: str = "2026-01-05T10:00:00+00:00",
             movimiento: str | None = None, historial: list[dict] | None | bool = True) -> dict:
    """`historial=True` arma uno mínimo coherente; `False`/`None` lo omite (candidatura legacy); una lista se usa tal cual."""
    movimiento = movimiento or alta
    doc: dict[str, Any] = {"application_id": application_id, "profile_id": profile_id, "etapa": etapa, "resultado_final": resultado,
                           "fecha_cambio_etapa": movimiento, "created_at": alta}
    if historial is True:
        doc["historial"] = [{"etapa": "Preselección", "fecha": alta}] + ([{"etapa": etapa, "fecha": movimiento}] if etapa != "Preselección" else [])
    elif isinstance(historial, list):
        doc["historial"] = historial
    return doc


def make_busqueda(busqueda_id: str, nombre: str, *, estado: str = "activo", apertura: str = "2026-01-02", cierre: str | None = None, candidatos: list[dict] | None = None) -> dict:
    return {"busqueda_id": busqueda_id, "nombre_puesto": nombre, "estado": estado, "fecha_apertura": apertura, "fecha_cierre": cierre,
            "candidatos": list(candidatos or []), "revision": 1, "created_at": apertura + "T09:00:00+00:00"}


def make_entrevista(entrevista_id: str, profile_id: str, *, busqueda_id: str | None = None, application_id: str | None = None, fecha: str | None = None,
                    concretada: bool = True, modalidad: str | None = None, puesto: str | None = None, created_at: str = "2026-01-10T10:00:00+00:00") -> dict:
    return {"entrevista_id": entrevista_id, "profile_id": profile_id, "busqueda_id": busqueda_id, "application_id": application_id, "fecha_entrevista": fecha,
            "concretada": concretada, "modalidad_contratacion": modalidad, "puesto_evaluado": puesto, "created_at": created_at, "revision": 1}


def make_publicacion(publicacion_id: str, canal: str, *, avisos: int = 1, periodo: str = "2026-Q1") -> dict:
    return {"publicacion_id": publicacion_id, "canal": canal, "cantidad_avisos": avisos, "periodo": periodo, "posicion": "x", "busqueda_id": None}


def make_snapshot(*, busquedas=(), entrevistas=(), publicaciones=(), fuentes: dict[str, str | None] | None = None, catalogos: dict[str, tuple[str, ...]] | None = None) -> Snapshot:
    return Snapshot(tuple(busquedas), tuple(entrevistas), tuple(publicaciones), dict(fuentes or {}), dict(catalogos if catalogos is not None else {}))


# ── dataset determinista (para el oráculo y los tests de paridad) ──────────────────────────────────────────

def build_raw_dataset(seed: int = 20261020) -> dict[str, Any]:
    rnd = random.Random(seed)
    base = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)
    pool = ["LinkedIn", "Flyer", "Referido", "Hunting", None]
    fuentes = {f"p{i:03d}": rnd.choice(pool) for i in range(40)}
    pids = list(fuentes)
    roles = ["Contador", "Analista Data", "Arquitecto SAP", "Desarrollador", "Consultor BW", "PM", "Analista Finanzas", "Tech Lead", "QA", "DevOps", "Líder Comercial", "RRHH Generalista", "Soporte", "Diseñador"]
    busquedas: list[dict] = []
    entrevistas: list[dict] = []
    for i, rol in enumerate(roles):
        bid = f"b{i:02d}"
        apertura = base + timedelta(days=rnd.randint(0, 520))
        cierre = apertura + timedelta(days=rnd.randint(1, 140)) if rnd.random() < 0.55 else None
        candidatos: list[dict] = []
        pares: set[str] = set()
        for j in range(rnd.randint(0, 9)):
            pid = rnd.choice(pids)
            alta = apertura + timedelta(days=rnd.randint(0, 30), hours=rnd.randint(0, 23))
            t = alta
            historial = [{"etapa": ETAPAS[0], "fecha": iso(t)}]
            idx = 0
            while idx < 4 and rnd.random() < 0.7:
                idx += 1
                t = t + timedelta(days=rnd.randint(0, 12), hours=rnd.randint(0, 20), minutes=rnd.randint(1, 59))
                historial.append({"etapa": ETAPAS[idx], "fecha": iso(t)})
            resultado = None
            if idx == 4:
                resultado = rnd.choice(["Ingreso", "Ingreso", "Rechazado", "Desistió"])
                if rnd.random() < 0.4:                      # resultado asignado después: segunda entrada «Finalizado»
                    t = t + timedelta(hours=rnd.randint(1, 50))
                    historial.append({"etapa": "Finalizado", "fecha": iso(t)})
            app = {"application_id": f"a{i:02d}{j:02d}", "profile_id": pid, "etapa": ETAPAS[idx], "resultado_final": resultado,
                   "fecha_cambio_etapa": iso(t), "historial": historial, "created_at": iso(alta)}
            if rnd.random() < 0.12:
                app.pop("historial")                        # candidatura legacy sin historial de etapas
            candidatos.append(app)
            if idx >= 1 and pid not in pares:               # entrevista automática: una por (persona, búsqueda)
                pares.add(pid)
                entro = historial[1]["fecha"] if "historial" in app else iso(alta + timedelta(days=1))
                entrevistas.append(make_entrevista(
                    f"e{i:02d}{j:02d}", pid, busqueda_id=bid, application_id=app["application_id"],
                    fecha=day(datetime.fromisoformat(entro) + timedelta(days=rnd.randint(0, 5))) if rnd.random() < 0.5 else None,
                    concretada=rnd.random() < 0.85, modalidad=rnd.choice(["Full time", "Part time", None, None]), created_at=entro))
        busquedas.append(make_busqueda(bid, f"Puesto {i:02d} {rol}", estado="cerrado" if cierre else "activo", apertura=day(apertura),
                                       cierre=day(cierre) if cierre else None, candidatos=candidatos))
    for n in range(6):                                      # entrevistas manuales (sin búsqueda ni candidatura)
        when = base + timedelta(days=rnd.randint(0, 600))
        entrevistas.append(make_entrevista(
            f"em{n:02d}", rnd.choice(pids), fecha=day(when) if rnd.random() < 0.6 else None, concretada=rnd.random() < 0.8,
            puesto=rnd.choice(["Contador", "Contador Sr", "  Analista Data ", "", None]), created_at=iso(when)))
    for n in range(3):                                      # candidatura removida: `application_id` colgado
        when = base + timedelta(days=rnd.randint(0, 600))
        entrevistas.append(make_entrevista(f"eg{n:02d}", f"ghostp{n}", busqueda_id=f"b{n:02d}", application_id=f"ghost{n}", concretada=True,
                                           modalidad=rnd.choice(["Full time", None]), created_at=iso(when)))
    periodos = [f"{y}-Q{q}" for y in (2025, 2026) for q in (1, 2, 3, 4)]
    publicaciones = [make_publicacion(f"pub{n:02d}", rnd.choice(["LinkedIn", "Flyer"]), avisos=rnd.randint(1, 4), periodo=rnd.choice(periodos)) for n in range(16)]
    return {"busquedas": busquedas, "entrevistas": entrevistas, "publicaciones": publicaciones, "fuentes": fuentes}


def snapshot_from_raw(raw: dict[str, Any], catalogos: dict[str, tuple[str, ...]] | None = None) -> Snapshot:
    return make_snapshot(busquedas=raw["busquedas"], entrevistas=raw["entrevistas"], publicaciones=raw["publicaciones"], fuentes=raw["fuentes"],
                         catalogos=CATALOGOS_PARIDAD if catalogos is None else catalogos)


def to_original_inputs(raw: dict[str, Any]) -> dict[str, Any]:
    """Traduce el dataset del Hub a las entradas de `lib/metricas.ts` (ver docstring del módulo)."""
    modalidad: dict[tuple[str, str], str | None] = {}
    for e in raw["entrevistas"]:
        if e.get("busqueda_id") and e.get("profile_id"):
            modalidad[(e["profile_id"], e["busqueda_id"])] = e.get("modalidad_contratacion")
    nombre = {b["busqueda_id"]: b["nombre_puesto"] for b in raw["busquedas"]}
    procesos, candidatos, por_application = [], [], {}
    for b in raw["busquedas"]:
        procesos.append({"id": b["busqueda_id"], "nombre_puesto": b["nombre_puesto"], "estado": b["estado"], "fecha_apertura": b["fecha_apertura"], "fecha_cierre": b["fecha_cierre"]})
        for a in b["candidatos"]:
            es_ingreso = a["etapa"] == "Finalizado" and a["resultado_final"] == "Ingreso"
            llegadas = sorted(h["fecha"] for h in a.get("historial", []) if h["etapa"] == "Finalizado")
            fuente = raw["fuentes"].get(a["profile_id"])
            candidato = {"id": a["application_id"], "proceso_id": b["busqueda_id"], "created_at": a["created_at"], "etapa_pipeline": a["etapa"], "resultado_final": a["resultado_final"],
                         "fecha_cambio_etapa": llegadas[0] if es_ingreso and llegadas else a["fecha_cambio_etapa"],
                         "banco_cv": {"fuente": fuente} if fuente else None,
                         "modalidad_contratacion": modalidad.get((a["profile_id"], b["busqueda_id"])), "fecha_ultimo_movimiento": a["fecha_cambio_etapa"]}
            candidatos.append(candidato)
            por_application[a["application_id"]] = {"fecha_cambio_etapa": a["fecha_cambio_etapa"], "created_at": a["created_at"]}
    entrevistas = []
    for e in raw["entrevistas"]:
        puesto = nombre.get(e.get("busqueda_id")) or e.get("puesto_evaluado")
        entrevistas.append({"fecha_entrevista": e.get("fecha_entrevista"), "concretada": e["concretada"], "created_at": e["created_at"], "puesto_evaluado": puesto,
                            "candidato": por_application.get(e.get("application_id"))})
    publicaciones = [{"canal": p["canal"], "cantidad_avisos": p["cantidad_avisos"], "periodo": p["periodo"]} for p in raw["publicaciones"]]
    return {"procesos": procesos, "candidatos": candidatos, "entrevistas": entrevistas, "publicaciones": publicaciones}
