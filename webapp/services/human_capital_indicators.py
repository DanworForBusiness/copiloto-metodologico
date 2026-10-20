"""human_capital_indicators.py — motor de cálculo de 06 INDICADORES (Capital Humano).

Paridad funcional con `/metricas` de la app de referencia (app-seleccion: `lib/metricas.ts`, `components/MetricasView.tsx`, `EfectividadCanalCard.tsx`, `EntrevistasPorPerfilCard.tsx`,
`lib/metricasExport.ts`), adaptada al modelo de datos del Hub. Especificación aprobada: `_handoffs/CCHH_06_INDICADORES_SPEC.md`.

ARQUITECTURA (lectura ≠ cálculo):
  1. `load_snapshot()` es la ÚNICA función con I/O: lee UNA vez por request búsquedas, entrevistas, publicaciones, perfiles y los dos catálogos que el motor necesita. Es de solo lectura
     (ninguna escritura, ni a disco ni a Spaces) y no hace consultas por candidatura: el origen (`Profile.source`) se indexa por `profile_id` a partir de la única lectura de perfiles.
  2. `build_facts(snapshot)` deriva UNA vez los hechos normalizados (candidaturas, entrevistas con su fecha efectiva y el origen de esa fecha, búsquedas con su señal de estancamiento).
  3. Los indicadores son funciones PURAS sobre esos hechos: `build_page`, `build_perfiles` (filtro local C6), `build_canales` (filtro local C9) y `export_tables` (las 11 hojas del XLSX, que
     reutiliza los mismos resultados: ninguna fórmula está duplicada). Devuelven dicts/listas JSON-serializables aptos para plantillas y para la exportación.

Sin dependencias de HTTP, Jinja ni `openpyxl`. Sin entidades, campos ni configuraciones nuevas.

REGLAS (ver spec §1–§2):
  - Unidad: «candidato» del original = candidatura (elemento de `busqueda.candidatos`; N:M en el Hub). Entrevista = registro por persona y búsqueda. Publicación = fila con `cantidad_avisos`.
  - Ingreso: predicado canónico `human_capital_service.is_ingreso_application`. Estancamiento: `busqueda_attention`/`BUSQUEDA_UMBRAL_ESTANCADA_DIAS` canónicos (no se reimplementan).
  - Fechas en UTC; días = `Math.round` de JS (mitad hacia arriba, NO el redondeo al par de Python), mínimo 0; promedios y porcentajes enteros con la misma regla.
  - FECHAS RECONSTRUIDAS: cada dato fechado lleva su ORIGEN; nunca se presenta una fecha derivada como registro histórico exacto. Fecha de ingreso = primera llegada a «Finalizado» del `historial`
    (o, sin historial, `fecha_cambio_etapa`): SIEMPRE derivada, porque el historial no guarda `resultado_final`. Fecha efectiva de la entrevista = cadena del original (D6).
  - Gobierno: modalidades y canales salen del catálogo gobernado + valores históricos presentes en los datos. Nada hardcodeado.
  - Determinismo: orden estable y desempates por nombre (casefold) y luego por id; fechas ilegibles = dato faltante (nunca se inventa una fecha).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from webapp.services import busqueda_repository
from webapp.services import entrevista_repository
from webapp.services import human_capital_catalog_repository as catalog_repo
from webapp.services import human_capital_service as hc
from webapp.services import profile_repository
from webapp.services import publicacion_repository

SIN_ESPECIFICAR = "Sin especificar"
PERIODO_TODOS = "todos"
TOP_N = 10
VENTANA_MESES = 6
RESULTADOS_DESCARTE = ("Rechazado", "Desistió")
CATALOGO_MODALIDAD = "modalidad_contratacion"
CATALOGO_CANAL = "canal_publicacion"
_MESES = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")

# Origen de la fecha de ingreso (SIEMPRE derivada, ver docstring del módulo).
ORIGEN_INGRESO_HISTORIAL = "primera_llegada_finalizado"
ORIGEN_INGRESO_ULTIMO_MOVIMIENTO = "ultimo_movimiento"
# Origen de la fecha efectiva de la entrevista (cadena del original, D6). Solo la primera es un dato registrado.
ORIGEN_ENTREVISTA_REGISTRADA = "registrada"
ORIGEN_ENTREVISTA_ULTIMO_MOVIMIENTO = "ultimo_movimiento_candidatura"
ORIGEN_ENTREVISTA_ALTA_CANDIDATURA = "alta_candidatura"
ORIGEN_ENTREVISTA_ALTA_ENTREVISTA = "alta_entrevista"


# ── helpers puros (fechas, redondeo, texto) ──────────────────────────────────────────────────────────────

def round_half_up(value: float) -> int:
    """`Math.round` de JavaScript para valores no negativos (mitad hacia arriba). `round()` de Python redondea al par: 4,5 → 4, y acá 4,5 → 5."""
    return int(math.floor(value + 0.5))


def parse_utc(value: Any) -> datetime | None:
    """ISO-8601 (fecha sola o con hora/offset) → datetime UTC con zona. Vacío o ilegible → None (dato faltante, nunca una fecha inventada)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def dias_entre(desde: datetime, hasta: datetime) -> int:
    """Días entre dos instantes: `Math.max(0, Math.round(ms / 86_400_000))` del original."""
    return max(0, round_half_up((hasta - desde).total_seconds() / 86400.0))


def promedio_entero(values: list[int]) -> int | None:
    return round_half_up(sum(values) / len(values)) if values else None


def mes_clave(moment: datetime) -> str:
    return f"{moment.year:04d}-{moment.month:02d}"


def etiqueta_mes(clave: str) -> str:
    year, month = clave.split("-")
    return f"{_MESES[int(month) - 1]} {year}"


def trimestre(moment: datetime) -> str:
    return hc.periodo_desde_fecha(moment.date())


def _norm(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _ancho(valor: float, maximo: float) -> float:
    """Ancho porcentual de una barra respecto del máximo de su lista (el original normaliza contra `Math.max(..., 1)`)."""
    return round(valor / max(maximo, 1) * 100, 2)


def _as_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


# ── snapshot (única lectura) ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Snapshot:
    """Lectura consistente y única de todo lo que el motor necesita. `fuentes` indexa `Profile.source` por `profile_id` e INCLUYE los perfiles dados de baja (D9)."""
    busquedas: tuple[dict, ...]
    entrevistas: tuple[dict, ...]
    publicaciones: tuple[dict, ...]
    fuentes: dict[str, str | None]
    catalogos: dict[str, tuple[str, ...]]


def load_snapshot() -> Snapshot:
    """ÚNICA función con I/O del motor. Solo lecturas (una por colección); sin escrituras ni consultas por candidatura."""
    busquedas = busqueda_repository.list()
    entrevistas = entrevista_repository.list()
    publicaciones = publicacion_repository.list()
    fuentes: dict[str, str | None] = {}
    for profile in profile_repository.list():
        profile_id = profile.get("profile_id")
        if profile_id:
            fuentes[profile_id] = _norm(profile.get("source")) or None
    catalogos = {name: tuple(catalog_repo.catalog_values(name)) for name in (CATALOGO_MODALIDAD, CATALOGO_CANAL)}
    return Snapshot(tuple(busquedas), tuple(entrevistas), tuple(publicaciones), fuentes, catalogos)


# ── hechos normalizados ──────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BusquedaFact:
    busqueda_id: str
    nombre: str
    estado: str
    apertura: datetime | None
    cierre: datetime | None
    n_candidaturas: int
    dias_sin_movimiento: int | None = None   # solo búsquedas activas (función canónica)
    estancada: bool = False


@dataclass(frozen=True)
class Candidatura:
    busqueda_id: str
    application_id: str | None
    profile_id: str | None
    fuente: str | None
    alta: datetime | None
    ultimo_movimiento: datetime | None
    es_ingreso: bool
    ingreso_fecha: datetime | None
    ingreso_origen: str | None
    resultado_final: str | None = None


@dataclass(frozen=True)
class EntrevistaFact:
    entrevista_id: str
    profile_id: str | None
    busqueda_id: str | None
    concretada: bool
    puesto: str
    fecha: datetime | None
    fecha_origen: str | None


@dataclass(frozen=True)
class PublicacionFact:
    canal: str
    avisos: int
    periodo: str


@dataclass(frozen=True)
class IndicatorFacts:
    busquedas: tuple[BusquedaFact, ...]
    candidaturas: tuple[Candidatura, ...]
    entrevistas: tuple[EntrevistaFact, ...]
    publicaciones: tuple[PublicacionFact, ...]
    modalidad_por_par: dict[tuple[str, str], str | None] = field(default_factory=dict)
    catalogos: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _ingreso_fecha(app: dict) -> tuple[datetime | None, str | None]:
    """Fecha DERIVADA del ingreso: primera llegada a «Finalizado» del historial; sin historial, `fecha_cambio_etapa`. Ninguna es «la fecha en que se marcó Ingreso»."""
    llegadas = [parse_utc(h.get("fecha")) for h in (app.get("historial") or [])
                if isinstance(h, dict) and h.get("etapa") == busqueda_repository.ETAPA_FINAL]
    llegadas = [d for d in llegadas if d is not None]
    if llegadas:
        return min(llegadas), ORIGEN_INGRESO_HISTORIAL
    fallback = parse_utc(app.get("fecha_cambio_etapa"))
    return (fallback, ORIGEN_INGRESO_ULTIMO_MOVIMIENTO) if fallback is not None else (None, None)


def _fecha_entrevista(entrevista: dict, app: Candidatura | None) -> tuple[datetime | None, str | None]:
    """Cadena de fecha efectiva del original (D6): fecha cargada → último movimiento de la candidatura → alta de la candidatura → alta de la entrevista."""
    registrada = parse_utc(entrevista.get("fecha_entrevista"))
    if registrada is not None:
        return registrada, ORIGEN_ENTREVISTA_REGISTRADA
    if app is not None and app.ultimo_movimiento is not None:
        return app.ultimo_movimiento, ORIGEN_ENTREVISTA_ULTIMO_MOVIMIENTO
    if app is not None and app.alta is not None:
        return app.alta, ORIGEN_ENTREVISTA_ALTA_CANDIDATURA
    alta = parse_utc(entrevista.get("created_at"))
    return (alta, ORIGEN_ENTREVISTA_ALTA_ENTREVISTA) if alta is not None else (None, None)


def build_facts(snapshot: Snapshot) -> IndicatorFacts:
    """Deriva UNA vez los hechos normalizados. Pura salvo por el reloj de `busqueda_attention` (función canónica de estancamiento, solo búsquedas activas)."""
    busquedas: list[BusquedaFact] = []
    candidaturas: list[Candidatura] = []
    for doc in sorted(snapshot.busquedas, key=lambda b: str(b.get("busqueda_id"))):
        busqueda_id = str(doc.get("busqueda_id"))
        apps = [a for a in (doc.get("candidatos") or []) if isinstance(a, dict)]
        activo = doc.get("estado") == "activo"
        attention = hc.busqueda_attention(doc) if activo else {}
        busquedas.append(BusquedaFact(
            busqueda_id=busqueda_id, nombre=_norm(doc.get("nombre_puesto")), estado=str(doc.get("estado") or ""),
            apertura=parse_utc(doc.get("fecha_apertura")), cierre=parse_utc(doc.get("fecha_cierre")), n_candidaturas=len(apps),
            dias_sin_movimiento=attention.get("dias_sin_movimiento") if activo else None, estancada=bool(attention.get("estancada")) if activo else False))
        for app in apps:
            es_ingreso = hc.is_ingreso_application(app)
            fecha, origen = _ingreso_fecha(app) if es_ingreso else (None, None)
            profile_id = app.get("profile_id")
            candidaturas.append(Candidatura(
                busqueda_id=busqueda_id, application_id=app.get("application_id"), profile_id=profile_id,
                fuente=snapshot.fuentes.get(profile_id), alta=parse_utc(app.get("created_at")),
                ultimo_movimiento=parse_utc(app.get("fecha_cambio_etapa")), es_ingreso=es_ingreso, ingreso_fecha=fecha, ingreso_origen=origen,
                resultado_final=app.get("resultado_final")))
    # Candidatura por `application_id` (la entrevista automática la referencia); una candidatura removida deja la referencia colgada → cae a la alta de la entrevista.
    por_application = {c.application_id: c for c in candidaturas if c.application_id}
    nombre_busqueda = {b.busqueda_id: b.nombre for b in busquedas}
    entrevistas: list[EntrevistaFact] = []
    por_par: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    for doc in sorted(snapshot.entrevistas, key=lambda e: str(e.get("entrevista_id"))):
        busqueda_id = doc.get("busqueda_id")
        fecha, origen = _fecha_entrevista(doc, por_application.get(doc.get("application_id")))
        puesto = nombre_busqueda.get(busqueda_id) or _norm(doc.get("puesto_evaluado")) or SIN_ESPECIFICAR
        entrevistas.append(EntrevistaFact(
            entrevista_id=str(doc.get("entrevista_id")), profile_id=doc.get("profile_id"), busqueda_id=busqueda_id,
            concretada=bool(doc.get("concretada", True)), puesto=puesto, fecha=fecha, fecha_origen=origen))
        if busqueda_id and doc.get("profile_id"):
            clave = (doc["profile_id"], busqueda_id)
            orden = (str(doc.get("created_at") or ""), str(doc.get("entrevista_id")), _norm(doc.get("modalidad_contratacion")) or None)
            if clave not in por_par or orden[:2] >= por_par[clave][:2]:
                por_par[clave] = orden                          # (profile, búsqueda) es único en el Hub; ante un duplicado gana el más reciente (determinista)
    publicaciones = tuple(
        PublicacionFact(canal=_norm(p.get("canal")), avisos=_as_int(p.get("cantidad_avisos")), periodo=_norm(p.get("periodo")))
        for p in sorted(snapshot.publicaciones, key=lambda p: str(p.get("publicacion_id"))))
    return IndicatorFacts(
        busquedas=tuple(busquedas), candidaturas=tuple(candidaturas), entrevistas=tuple(entrevistas), publicaciones=publicaciones,
        modalidad_por_par={k: v[2] for k, v in por_par.items()}, catalogos=dict(snapshot.catalogos))


# ── metadatos de procedencia de fechas (spec §2) ─────────────────────────────────────────────────────────

def _meta_fechas_ingreso(facts: IndicatorFacts) -> dict[str, int]:
    ingresos = [c for c in facts.candidaturas if c.es_ingreso]
    return {
        "ingresos": len(ingresos),
        "con_fecha": sum(1 for c in ingresos if c.ingreso_fecha is not None),
        "desde_historial": sum(1 for c in ingresos if c.ingreso_origen == ORIGEN_INGRESO_HISTORIAL),
        "desde_ultimo_movimiento": sum(1 for c in ingresos if c.ingreso_origen == ORIGEN_INGRESO_ULTIMO_MOVIMIENTO),
        "sin_fecha": sum(1 for c in ingresos if c.ingreso_fecha is None),
    }


def _meta_fechas_entrevistas(entrevistas: list[EntrevistaFact] | tuple[EntrevistaFact, ...]) -> dict[str, int]:
    con_fecha = [e for e in entrevistas if e.fecha is not None]
    return {
        "total": len(entrevistas),
        "registradas": sum(1 for e in con_fecha if e.fecha_origen == ORIGEN_ENTREVISTA_REGISTRADA),
        "estimadas": sum(1 for e in con_fecha if e.fecha_origen != ORIGEN_ENTREVISTA_REGISTRADA),
        "sin_fecha": len(entrevistas) - len(con_fecha),
    }


# ── KPI (K1–K6) ──────────────────────────────────────────────────────────────────────────────────────────

def kpis(facts: IndicatorFacts) -> dict[str, Any]:
    cobertura = [dias_entre(b.apertura, b.cierre) for b in facts.busquedas if b.apertura is not None and b.cierre is not None]
    contratacion = [dias_entre(c.alta, c.ingreso_fecha) for c in facts.candidaturas if c.es_ingreso and c.alta is not None and c.ingreso_fecha is not None]
    total = len(facts.candidaturas)
    ingresos = sum(1 for c in facts.candidaturas if c.es_ingreso)
    descartadas = sum(1 for c in facts.candidaturas if c.resultado_final in RESULTADOS_DESCARTE)
    return {
        "tiempo_cobertura": {"valor": promedio_entero(cobertura), "n": len(cobertura)},
        "tiempo_contratacion": {"valor": promedio_entero(contratacion), "n": len(contratacion), "fechas": _meta_fechas_ingreso(facts)},
        "tasa_conversion": {"valor": 0 if total == 0 else round_half_up(ingresos / total * 100), "ingresos": ingresos, "candidaturas": total},
        "total_contratados": {"valor": ingresos},
        "total_descartados": {"valor": descartadas},
        "entrevistas_realizadas": {"valor": sum(1 for e in facts.entrevistas if e.concretada)},
    }


# ── tarjetas (C1–C10) ────────────────────────────────────────────────────────────────────────────────────

def _barras(filas: list[dict], clave: str) -> list[dict]:
    """Agrega `ancho_pct` a cada fila de un ranking de barras (normalizado contra el máximo de la lista, como el original)."""
    maximo = max((f[clave] for f in filas), default=1)
    return [{**f, "ancho_pct": _ancho(f[clave], maximo)} for f in filas]


def postulantes_por_busqueda(facts: IndicatorFacts) -> dict[str, Any]:
    """C1 — candidaturas por búsqueda (activas y cerradas), sin las de 0, descendente, top 10."""
    filas = [{"busqueda_id": b.busqueda_id, "nombre_puesto": b.nombre, "cantidad": b.n_candidaturas} for b in facts.busquedas if b.n_candidaturas > 0]
    filas.sort(key=lambda f: (-f["cantidad"], f["nombre_puesto"].casefold(), f["busqueda_id"]))
    return {"filas": _barras(filas[:TOP_N], "cantidad"), "total_busquedas": len(filas)}


def busquedas_por_mes(facts: IndicatorFacts, ventana_meses: int | None = VENTANA_MESES) -> dict[str, Any]:
    """C2 — abiertas (mes de `fecha_apertura`) y cerradas (mes de `fecha_cierre`) por mes; últimos N meses CON movimiento, cronológico."""
    mapa: dict[str, dict[str, int]] = {}
    for b in facts.busquedas:
        if b.apertura is not None:
            mapa.setdefault(mes_clave(b.apertura), {"abiertas": 0, "cerradas": 0})["abiertas"] += 1
        if b.cierre is not None:
            mapa.setdefault(mes_clave(b.cierre), {"abiertas": 0, "cerradas": 0})["cerradas"] += 1
    meses = sorted(mapa)
    if ventana_meses is not None:
        meses = meses[-ventana_meses:]
    maximo = max((mapa[m]["abiertas"] + mapa[m]["cerradas"] for m in meses), default=1)
    filas = [{"mes": m, "etiqueta": etiqueta_mes(m), "abiertas": mapa[m]["abiertas"], "cerradas": mapa[m]["cerradas"],
              "total": mapa[m]["abiertas"] + mapa[m]["cerradas"],
              "ancho_abiertas_pct": _ancho(mapa[m]["abiertas"], maximo), "ancho_cerradas_pct": _ancho(mapa[m]["cerradas"], maximo)} for m in meses]
    return {"filas": filas, "ventana_meses": ventana_meses}


def _series_unicas(valores: list[str]) -> list[str]:
    visto: set[str] = set()
    out: list[str] = []
    for valor in valores:
        if valor and valor not in visto:
            visto.add(valor)
            out.append(valor)
    return out


def ingresos_por_mes(facts: IndicatorFacts, ventana_meses: int | None = VENTANA_MESES) -> dict[str, Any]:
    """C3 — ingresos por mes (de la fecha DERIVADA de ingreso) abiertos por modalidad. Series = catálogo gobernado en su orden + valores históricos presentes en los datos; «Sin especificar»
    agrupa los ingresos sin entrevista/modalidad. Últimos N meses con al menos un ingreso."""
    ingresos = [c for c in facts.candidaturas if c.es_ingreso and c.ingreso_fecha is not None]

    def modalidad(c: Candidatura) -> str | None:
        return facts.modalidad_por_par.get((c.profile_id, c.busqueda_id))

    catalogo = _series_unicas(list(facts.catalogos.get(CATALOGO_MODALIDAD, ())))
    historicas = sorted({m for m in (modalidad(c) for c in ingresos) if m and m not in catalogo}, key=lambda s: (s.casefold(), s))
    series = catalogo + historicas
    mapa: dict[str, dict[str | None, int]] = {}
    for c in ingresos:
        valor = modalidad(c)
        clave = valor if valor in series else None
        fila = mapa.setdefault(mes_clave(c.ingreso_fecha), {})
        fila[clave] = fila.get(clave, 0) + 1
    meses = sorted(mapa)
    if ventana_meses is not None:
        meses = meses[-ventana_meses:]
    totales = {m: sum(mapa[m].values()) for m in meses}
    maximo = max(totales.values(), default=1)
    filas = []
    for m in meses:
        segmentos = [{"clave": s, "sin_especificar": False, "valor": mapa[m].get(s, 0), "ancho_pct": _ancho(mapa[m].get(s, 0), maximo)} for s in series]
        segmentos.append({"clave": SIN_ESPECIFICAR, "sin_especificar": True, "valor": mapa[m].get(None, 0), "ancho_pct": _ancho(mapa[m].get(None, 0), maximo)})
        filas.append({"mes": m, "etiqueta": etiqueta_mes(m), "segmentos": segmentos, "total": totales[m]})
    return {"series": series, "filas": filas, "ventana_meses": ventana_meses, "fechas": _meta_fechas_ingreso(facts)}


def _entrevistas_por(facts: IndicatorFacts, agrupar) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    mapa: dict[str, dict[str, int]] = {}
    for e in (e for e in facts.entrevistas if e.fecha is not None):
        fila = mapa.setdefault(agrupar(e.fecha), {"realizadas": 0, "no_realizadas": 0})
        fila["realizadas" if e.concretada else "no_realizadas"] += 1
    return mapa, _meta_fechas_entrevistas(facts.entrevistas)


def entrevistas_por_anio(facts: IndicatorFacts) -> dict[str, Any]:
    """C4 — entrevistas por año de la fecha efectiva (concretadas / no concretadas), ascendente."""
    mapa, meta = _entrevistas_por(facts, lambda d: str(d.year))
    filas = [{"anio": a, "realizadas": v["realizadas"], "no_realizadas": v["no_realizadas"], "total": v["realizadas"] + v["no_realizadas"]} for a, v in sorted(mapa.items())]
    return {"filas": filas, "fechas": meta}


def entrevistas_por_mes(facts: IndicatorFacts) -> dict[str, Any]:
    """C5 — igual que C4 por mes, HISTORIAL COMPLETO (sin ventana), cronológico."""
    mapa, meta = _entrevistas_por(facts, mes_clave)
    filas = [{"mes": m, "etiqueta": etiqueta_mes(m), "realizadas": v["realizadas"], "no_realizadas": v["no_realizadas"],
              "total": v["realizadas"] + v["no_realizadas"]} for m, v in sorted(mapa.items())]
    return {"filas": filas, "fechas": meta}


def build_perfiles(facts: IndicatorFacts, periodo: str | None = PERIODO_TODOS) -> dict[str, Any]:
    """C6 — entrevistas CONCRETADAS por puesto evaluado (filtro local de período, independiente de C9). Opciones: trimestres con al menos una entrevista concretada, recientes primero.
    Un período que no está entre las opciones se trata como «todos»."""
    concretadas = [e for e in facts.entrevistas if e.concretada]
    opciones = sorted({trimestre(e.fecha) for e in concretadas if e.fecha is not None}, reverse=True)
    pedido = _norm(periodo) or PERIODO_TODOS
    efectivo = pedido if pedido in opciones else PERIODO_TODOS
    incluidas = concretadas if efectivo == PERIODO_TODOS else [e for e in concretadas if e.fecha is not None and trimestre(e.fecha) == efectivo]
    conteo: dict[str, int] = {}
    for e in incluidas:
        conteo[e.puesto] = conteo.get(e.puesto, 0) + 1
    filas = sorted(({"perfil": p, "cantidad": n} for p, n in conteo.items()), key=lambda f: (-f["cantidad"], f["perfil"].casefold(), f["perfil"]))
    return {"periodo": efectivo, "periodo_solicitado": pedido, "periodo_valido": pedido == efectivo, "periodos": opciones,
            "filas": filas, "top": _barras(filas[:TOP_N], "cantidad"), "fechas": _meta_fechas_entrevistas(incluidas)}


def efectividad_por_fuente(facts: IndicatorFacts) -> dict[str, Any]:
    """C7 — postulantes (candidaturas), contratados y conversión por fuente de la persona; sin fuente → «Sin especificar». Descendente por postulantes."""
    mapa: dict[str, dict[str, int]] = {}
    for c in facts.candidaturas:
        fila = mapa.setdefault(c.fuente or SIN_ESPECIFICAR, {"postulantes": 0, "contratados": 0})
        fila["postulantes"] += 1
        fila["contratados"] += 1 if c.es_ingreso else 0
    filas = [{"fuente": f, "postulantes": v["postulantes"], "contratados": v["contratados"],
              "conversion": 0 if v["postulantes"] == 0 else round_half_up(v["contratados"] / v["postulantes"] * 100)} for f, v in mapa.items()]
    filas.sort(key=lambda f: (-f["postulantes"], f["fuente"].casefold(), f["fuente"]))
    return {"filas": filas}


def tiempo_cobertura_por_busqueda(facts: IndicatorFacts) -> dict[str, Any]:
    """C8 — días apertura→cierre por búsqueda cerrada. El original no ordenaba: acá orden determinista (apertura, nombre, id)."""
    cerradas = [b for b in facts.busquedas if b.apertura is not None and b.cierre is not None]
    cerradas.sort(key=lambda b: (b.apertura, b.nombre.casefold(), b.busqueda_id))
    filas = [{"busqueda_id": b.busqueda_id, "nombre_puesto": b.nombre, "dias": dias_entre(b.apertura, b.cierre)} for b in cerradas]
    return {"filas": _barras(filas, "dias")}


def build_canales(facts: IndicatorFacts, periodo: str | None = PERIODO_TODOS) -> dict[str, Any]:
    """C9 — efectividad por canal de aviso (filtro local de período, independiente de C6). Filas = catálogo `canal_publicacion` + canales presentes en publicaciones.
    La coincidencia canal↔fuente es SOLO de nombre exacto: una aproximación por canal/período, nunca una atribución aviso por aviso ni una causa comprobada."""
    catalogo = _series_unicas(list(facts.catalogos.get(CATALOGO_CANAL, ())))
    presentes = sorted({p.canal for p in facts.publicaciones if p.canal and p.canal not in catalogo}, key=lambda s: (s.casefold(), s))
    canales = catalogo + presentes
    opciones_set = {p.periodo for p in facts.publicaciones if p.periodo}
    opciones_set |= {trimestre(c.alta) for c in facts.candidaturas if c.fuente and c.alta is not None}   # igual que el original: toda candidatura con fuente aporta su trimestre
    opciones = sorted(opciones_set, reverse=True)
    pedido = _norm(periodo) or PERIODO_TODOS
    efectivo = pedido if pedido in opciones else PERIODO_TODOS
    filas = []
    for canal in canales:
        avisos = sum(p.avisos for p in facts.publicaciones if p.canal == canal and (efectivo == PERIODO_TODOS or p.periodo == efectivo))
        propias = [c for c in facts.candidaturas if c.fuente == canal and (efectivo == PERIODO_TODOS or (c.alta is not None and trimestre(c.alta) == efectivo))]
        contratados = sum(1 for c in propias if c.es_ingreso)
        filas.append({"canal": canal, "avisos": avisos, "candidatos": len(propias), "contratados": contratados,
                      "conversion": 0 if not propias else round_half_up(contratados / len(propias) * 100),
                      "contrataciones_por_aviso": None if avisos == 0 else math.floor(contratados / avisos * 1000 + 0.5) / 1000})
    return {"periodo": efectivo, "periodo_solicitado": pedido, "periodo_valido": pedido == efectivo, "periodos": opciones, "filas": filas,
            "sin_datos": all(f["avisos"] == 0 and f["candidatos"] == 0 for f in filas), "coincidencia_por_nombre": True}


def busquedas_estancadas(facts: IndicatorFacts) -> dict[str, Any]:
    """C10 — búsquedas ACTIVAS con ≥ umbral días sin movimiento, según la función canónica del Hub (`busqueda_attention`); descendente."""
    filas = [{"busqueda_id": b.busqueda_id, "nombre_puesto": b.nombre, "dias": b.dias_sin_movimiento} for b in facts.busquedas if b.estancada and b.dias_sin_movimiento is not None]
    filas.sort(key=lambda f: (-f["dias"], f["nombre_puesto"].casefold(), f["busqueda_id"]))
    return {"filas": filas, "umbral_dias": hc.BUSQUEDA_UMBRAL_ESTANCADA_DIAS}


# ── página y exportación ─────────────────────────────────────────────────────────────────────────────────

def build_page(facts: IndicatorFacts, *, ventana_meses: int | None = VENTANA_MESES) -> dict[str, Any]:
    """Todos los indicadores de la pantalla (K1–K6, C1–C10) en una sola estructura. C6 y C9 con su filtro en «todos»."""
    return {
        "kpis": kpis(facts),
        "postulantes_por_busqueda": postulantes_por_busqueda(facts),
        "busquedas_por_mes": busquedas_por_mes(facts, ventana_meses),
        "ingresos_por_mes": ingresos_por_mes(facts, ventana_meses),
        "entrevistas_por_anio": entrevistas_por_anio(facts),
        "entrevistas_por_mes": entrevistas_por_mes(facts),
        "perfiles_entrevistados": build_perfiles(facts, PERIODO_TODOS),
        "efectividad_por_fuente": efectividad_por_fuente(facts),
        "tiempo_cobertura_por_busqueda": tiempo_cobertura_por_busqueda(facts),
        "canales_aviso": build_canales(facts, PERIODO_TODOS),
        "busquedas_estancadas": busquedas_estancadas(facts),
    }


def export_tables(facts: IndicatorFacts, *, ventana_meses: int | None = VENTANA_MESES) -> list[dict[str, Any]]:
    """Las 11 hojas del XLSX original (mismos nombres, encabezados y orden), desde los MISMOS resultados de la pantalla: sin fórmulas duplicadas. Siempre histórico completo: C6 y C9 en «todos».
    Los meses de C2/C3 conservan la ventana del original (`ventana_meses`, por defecto 6; `None` = todos los meses). Las celdas son valores crudos: la neutralización de texto peligroso
    para Excel es responsabilidad de la capa de exportación (no de este motor)."""
    page = build_page(facts, ventana_meses=ventana_meses)
    k = page["kpis"]
    ingresos = page["ingresos_por_mes"]
    cob, hire = k["tiempo_cobertura"]["valor"], k["tiempo_contratacion"]["valor"]
    return [
        {"hoja": "Resumen", "encabezados": ["Indicador", "Valor"], "filas": [
            ["Tiempo de cobertura prom.", "—" if cob is None else f"{cob} días"],
            ["Tiempo de contratación prom.", "—" if hire is None else f"{hire} días"],
            ["Tasa de conversión", f"{k['tasa_conversion']['valor']}%"],
            ["Total contratados", k["total_contratados"]["valor"]],
            ["Total descartados", k["total_descartados"]["valor"]],
            ["Total entrevistas realizadas", k["entrevistas_realizadas"]["valor"]]]},
        {"hoja": "Postulantes por búsqueda", "encabezados": ["Posición / búsqueda", "Postulantes"],
         "filas": [[f["nombre_puesto"], f["cantidad"]] for f in page["postulantes_por_busqueda"]["filas"]]},
        {"hoja": "Búsquedas por mes", "encabezados": ["Mes", "Abiertas", "Cerradas"],
         "filas": [[f["mes"], f["abiertas"], f["cerradas"]] for f in page["busquedas_por_mes"]["filas"]]},
        {"hoja": "Ingresos por mes", "encabezados": ["Mes", *ingresos["series"], SIN_ESPECIFICAR, "Total"],
         "filas": [[f["mes"], *[s["valor"] for s in f["segmentos"]], f["total"]] for f in ingresos["filas"]]},
        {"hoja": "Efectividad por fuente", "encabezados": ["Fuente", "Postulantes", "Contratados", "Conversión %"],
         "filas": [[f["fuente"], f["postulantes"], f["contratados"], f["conversion"]] for f in page["efectividad_por_fuente"]["filas"]]},
        {"hoja": "Tiempo de cobertura", "encabezados": ["Posición / búsqueda", "Días (apertura a cierre)"],
         "filas": [[f["nombre_puesto"], f["dias"]] for f in page["tiempo_cobertura_por_busqueda"]["filas"]]},
        {"hoja": "Búsquedas estancadas", "encabezados": ["Posición / búsqueda", "Días sin movimiento"],
         "filas": [[f["nombre_puesto"], f["dias"]] for f in page["busquedas_estancadas"]["filas"]]},
        {"hoja": "Entrevistas por año", "encabezados": ["Año", "Realizadas", "No concretadas", "Total"],
         "filas": [[f["anio"], f["realizadas"], f["no_realizadas"], f["total"]] for f in page["entrevistas_por_anio"]["filas"]]},
        {"hoja": "Entrevistas por período", "encabezados": ["Período", "Realizadas", "No concretadas", "Total"],
         "filas": [[f["mes"], f["realizadas"], f["no_realizadas"], f["total"]] for f in page["entrevistas_por_mes"]["filas"]]},
        {"hoja": "Perfiles más entrevistados", "encabezados": ["Perfil", "Cantidad"],
         "filas": [[f["perfil"], f["cantidad"]] for f in page["perfiles_entrevistados"]["filas"]]},
        {"hoja": "Efectividad por canal", "encabezados": ["Canal", "Avisos", "Candidatos", "Contratados", "Conversión %", "Contrataciones por aviso"],
         "filas": [[f["canal"], f["avisos"], f["candidatos"], f["contratados"], f["conversion"],
                    "—" if f["contrataciones_por_aviso"] is None else f["contrataciones_por_aviso"]] for f in page["canales_aviso"]["filas"]]},
    ]
