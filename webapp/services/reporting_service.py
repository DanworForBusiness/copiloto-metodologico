"""
reporting_service.py — Capa de servicio de Reporting Integral (Fase 1: SOLO
Cartera). Consume los hechos normalizados de `ReportingRepository` y arma el
contrato EXACTO que ya conoce el renderer Cartera de `webapp/static/reporting.js`
(mismas keys que los mocks `CARTERA_MANTENIMIENTO_ROWS` / `CARTERA_PROYECTO_ROWS`),
sin que la mecánica de fuente (Graph/SharePoint/XLSX) llegue al browser.

Deriva las medidas que NO son concern de fuente:
  - `diff = certificadas - trabajadas` (verificado 1:1 contra los mocks V0);
  - el split Mantenimiento vs Proyecto según `tipoProyecto` (ya clasificado en
    el repositorio desde `Proyectos.TipodeProyecto`);
  - la perspectiva Proyecto AGREGA sobre la vida del proyecto (suma de meses),
    con `facturadas` = total del XLSX por proyecto (source-faithful, NO se
    re-suma por mes). Mantenimiento conserva el grano mensual y su `facturadas`
    = `certificadas` (= HsFacturadas de la lista mensual, ver lineage).

NO mueve semántica de interacción del frontend (facets, jerarquía, colapsos,
defaults de filtro) — eso sigue 100% en reporting.js.
"""
from __future__ import annotations

from webapp.services.reporting_repository import ReportingRepository

# Nombres de mes → número (la columna `Mes` de "Horas por proyectos" es texto;
# puede venir como nombre español, número, o "MM"). Se usa SOLO para construir
# `fecha` (primer día del mes) del rango de fechas de Mantenimiento.
_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def _month_num(mes: str) -> int | None:
    text = (mes or "").strip().lower()
    if not text:
        return None
    if text in _MESES:
        return _MESES[text]
    try:
        n = int(float(text))
        return n if 1 <= n <= 12 else None
    except ValueError:
        return None


def _fecha_primer_dia(anio: str, mes: str) -> str:
    n = _month_num(mes)
    if not anio or n is None:
        return ""
    return f"{anio}-{n:02d}-01"


def _round2(x: float) -> float:
    return round(x * 100) / 100


# --- Batch A (Horas Vendidas / Participación / Calendario) -----------------
# Reusa `_MESES`/`_month_num` de arriba (Cartera) — MISMO parseo del texto
# de mes de "Horas por proyectos", nunca una segunda función.

# Orden real de negocio de Frecuencia (ver TMDL de Cliente[ID Frecuencia]:
# SWITCH 1 Mensual…6 Anual, YA verificado/vivo en el mock de Participación/
# Calendario de este mismo V0 — "1 Mensual"/"3 Trimestral"/"5 Semestral"/
# "6 Anual") — MISMA lista que `FRECUENCIA_ORDER` en reporting.js, nunca
# reordenada acá.
_FRECUENCIA_ORDER = ["Mensual", "Semanal", "Trimestral", "Cuatrimestral", "Semestral", "Anual"]


def _normalize_frecuencia(raw: str) -> str:
    """Valor CANÓNICO (misma capitalización que `_FRECUENCIA_ORDER`) del
    campo físico `Frecuencia Actualizacion` — necesario porque el motor de
    ordenamiento de Calendario en reporting.js compara por IGUALDAD exacta
    de string contra esa lista. Un valor no reconocido se preserva tal cual
    (nunca se fabrica una categoría nueva)."""
    text = (raw or "").strip()
    for label in _FRECUENCIA_ORDER:
        if text.lower() == label.lower():
            return label
    return text


def _id_frecuencia(raw: str) -> str:
    """`ID Frecuencia` es una columna DERIVADA en el PBIP (no física, ver
    real-data-lineage.md sección 8) — se reconstruye acá con la MISMA regla
    ya verificada por el mock ("1 Mensual".."6 Anual" == índice+1 en
    `_FRECUENCIA_ORDER`). Valor no reconocido ⇒ se devuelve tal cual, sin
    número — nunca se inventa un índice."""
    label = _normalize_frecuencia(raw)
    if label in _FRECUENCIA_ORDER:
        return f"{_FRECUENCIA_ORDER.index(label) + 1} {label}"
    return label


_MESES_NOMBRES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _calendario_monthly(calendario_act: str) -> list[int]:
    """Presencia BOOLEANA por mes (1/0) — reproduce `CONTAINSSTRING(
    Calendario_Act, <nombre de mes>)` del DAX fuente (ver real-data-lineage.md
    sección 8): cada mes es 1 si su nombre en español aparece como substring
    del texto crudo, sin asumir un separador (coma/punto y coma/espacio) —
    agnóstico al formato exacto, igual que CONTAINSSTRING. NUNCA una cuenta."""
    text = (calendario_act or "").lower()
    return [1 if nombre in text else 0 for nombre in _MESES_NOMBRES]


def _fecha_min(facts_con_mes: list[dict]) -> str:
    """Fecha representativa (primer día del mes MÁS TEMPRANO presente) para
    el filtro Desde/Hasta de una fila agregada (Horas Vendidas/
    Participación NO tienen un único `fecha` fuente real — son agregados
    proyecto×mes/cliente×mes — se usa el primer mes con datos, mismo
    criterio de simplificación ya documentado que el mock V0). Sin meses
    válidos ⇒ "" (el facet de rango simplemente no filtra esa fila)."""
    fechas = sorted(
        f for f in (_fecha_primer_dia(x["anio"], x["mes"]) for x in facts_con_mes) if f
    )
    return fechas[0] if fechas else ""


class ReportingService:
    """Repositorio inyectable para tests (mock sin tocar `core.microsoft365`)."""

    def __init__(self, repository: ReportingRepository | None = None):
        self._repository = repository if repository is not None else ReportingRepository()

    def get_batch_a(self, access_token: str) -> dict:
        """UN método de entrada para las 4 experiencias de Batch A — el
        router llama esto UNA vez (endpoint consolidado), nunca 4 POSTs
        separados que repitan el mismo fetch de Cliente/Proyectos/Horas.
        Salud de Cliente queda FUERA (sin lineage físico documentado para
        sus 6 columnas categóricas, ver HANDOFF) — sigue en mock."""
        return {
            "horasVendidas": self.get_horas_vendidas(access_token),
            "participacion": self.get_participacion(access_token),
            "calendario": self.get_calendario(access_token),
            "horasProductivas": self.get_horas_productivas(access_token),
        }

    def get_cartera(self, access_token: str) -> dict:
        facts = self._repository.get_cartera_facts(access_token)
        return {
            "mantenimiento": self._mantenimiento_rows(facts),
            "proyecto": self._proyecto_rows(facts),
        }

    # --- Mantenimiento: grano mensual, facturadas = certificadas -------------

    def _mantenimiento_rows(self, facts: list[dict]) -> list[dict]:
        rows = []
        for f in facts:
            if f.get("tipoProyecto") != "Mantenimiento":
                continue
            trabajadas = _round2(f["hsTrabajadas"])
            certificadas = _round2(f["hsCertificadas"])
            rows.append({
                "cliente": f["cliente"],
                "cp": f["clientPartner"],
                "proyecto": f["proyecto"],
                "diff": _round2(certificadas - trabajadas),
                "trabajadas": trabajadas,
                "certificadas": certificadas,
                "vendidasMensuales": _round2(f["hsVendidas"]),
                # Source-faithful: Mantenimiento no usa el XLSX (facturadas == certificadas).
                "facturadas": certificadas,
                "status": f["estado"],
                "fecha": _fecha_primer_dia(f["anio"], f["mes"]),
            })
        return rows

    # --- Proyecto: agrega sobre la vida del proyecto, facturadas = XLSX -------

    def _proyecto_rows(self, facts: list[dict]) -> list[dict]:
        groups: dict[str, dict] = {}
        for f in facts:
            if f.get("tipoProyecto") == "Mantenimiento":
                continue
            key = f.get("idProyecto") or f.get("proyecto")
            if not key:
                continue
            g = groups.get(key)
            if g is None:
                g = {
                    "cliente": f["cliente"], "cp": f["clientPartner"],
                    "idProyecto": f["idProyecto"], "proyecto": f["proyecto"],
                    "status": f["estado"], "anioInicio": f["anioInicio"],
                    "trabajadas": 0.0, "certificadas": 0.0, "vendidas": 0.0,
                    # facturadas es un TOTAL por proyecto (XLSX); NO se re-suma
                    # por mes — se toma una vez (idéntico en cada fact del proyecto).
                    "facturadas": _round2(f.get("facturadasProyecto", 0.0)),
                }
                groups[key] = g
            g["trabajadas"] += f["hsTrabajadas"]
            g["certificadas"] += f["hsCertificadas"]
            g["vendidas"] += f["hsVendidas"]

        rows = []
        for g in groups.values():
            trabajadas = _round2(g["trabajadas"])
            certificadas = _round2(g["certificadas"])
            rows.append({
                "cliente": g["cliente"], "cp": g["cp"],
                "idProyecto": g["idProyecto"], "proyecto": g["proyecto"],
                "diff": _round2(certificadas - trabajadas),
                "trabajadas": trabajadas,
                "certificadas": certificadas,
                "vendidas": _round2(g["vendidas"]),
                "facturadas": g["facturadas"],
                "status": g["status"],
                "anioInicio": g["anioInicio"],
            })
        return rows

    # --- Batch A: Horas Vendidas / Participación / Calendario ---------------
    # Las 3 comparten UN fetch (`get_batch_a_facts`) — nunca 3 llamadas
    # redundantes a las mismas listas.

    def get_horas_vendidas(self, access_token: str) -> list[dict]:
        """Contrato EXACTO de `HORASVENDIDAS_ROWS` (reporting.js): una fila
        por (Client Partner, Tipo, Cliente, Proyecto) con `monthly[12]`
        (Ene..Dic, aditivo) de Hs Vendidas. `fecha` = primer mes con datos
        de ese proyecto (ver `_fecha_min`)."""
        facts = self._repository.get_batch_a_facts(access_token)["project_facts"]
        groups: dict[tuple, dict] = {}
        for f in facts:
            key = (f["clientPartner"], f["tipoProyecto"], f["cliente"], f["proyecto"])
            g = groups.setdefault(key, {"monthly": [0.0] * 12, "facts": []})
            n = _month_num(f["mes"])
            if n is not None:
                g["monthly"][n - 1] += f["hsVendidas"]
                g["facts"].append(f)
        rows = []
        for (cp, tipo, cliente, proyecto), g in groups.items():
            rows.append({
                "cp": cp, "tipo": tipo, "cliente": cliente, "proyecto": proyecto,
                "monthly": [_round2(v) for v in g["monthly"]],
                "fecha": _fecha_min(g["facts"]),
            })
        return rows

    def get_participacion(self, access_token: str) -> list[dict]:
        """Contrato EXACTO de `COMERCIAL_PARTICIPACION_ROWS`: una fila por
        Cliente con `horasVendidas`/`horasCertificadas` sumadas sobre TODOS
        sus proyectos/meses (Mantenimiento + Proyecto, sin distinguir tipo —
        `% Hs Vendidas 2`/`% Hs Certificadas 2` no filtran por tipo en el
        PBIP fuente) + las columnas categóricas de Cliente ya resueltas.
        El % contra el gran total fijo se calcula en el FRONTEND
        (`comercialPctVendidas`/`comercialPctCertificadas`, ya existente,
        sin cambios) — este método solo entrega el numerador crudo por
        Cliente, nunca el % pre-calculado."""
        raw = self._repository.get_batch_a_facts(access_token)
        by_cliente: dict[str, dict] = {}
        for f in raw["project_facts"]:
            key = f["clienteId"] or f["cliente"]
            if not key:
                continue
            g = by_cliente.setdefault(key, {"vendidas": 0.0, "certificadas": 0.0, "facts": []})
            g["vendidas"] += f["hsVendidas"]
            g["certificadas"] += f["hsCertificadas"]
            g["facts"].append(f)

        rows = []
        for dim in raw["cliente_dim"]:
            key = dim["clienteId"] or dim["cliente"]
            g = by_cliente.get(key)
            if g is None or not dim["cliente"]:
                # Cliente sin ningún hecho de Horas por proyectos (o sin
                # nombre resuelto) ⇒ no aporta participación medible, se
                # omite (nunca una fila con 0/0 fabricada).
                continue
            rows.append({
                "idFrecuencia": _id_frecuencia(dim["frecuenciaActualizacion"]),
                "permiteAdelanto": dim["permiteAdelanto"],
                "condicionPago": dim["condicionPago"],
                "cliente": dim["cliente"],
                "cp": dim["clientPartner"],
                "horasVendidas": _round2(g["vendidas"]),
                "horasCertificadas": _round2(g["certificadas"]),
                "fecha": _fecha_min(g["facts"]),
            })
        return rows

    def get_calendario(self, access_token: str) -> list[dict]:
        """Contrato EXACTO de `CALENDARIO_ROWS`: una fila por Cliente con
        `frecuencia` (normalizada) y `monthly[12]` booleano (ver
        `_calendario_monthly`). Filas sin Cliente/frecuencia reconocible se
        omiten — nunca una fila con frecuencia vacía fabricada."""
        raw = self._repository.get_batch_a_facts(access_token)
        rows = []
        for dim in raw["cliente_dim"]:
            frecuencia = _normalize_frecuencia(dim["frecuenciaActualizacion"])
            if not dim["cliente"] or not frecuencia:
                continue
            rows.append({
                "frecuencia": frecuencia,
                "cliente": dim["cliente"],
                "cp": dim["clientPartner"],
                "monthly": _calendario_monthly(dim["calendarioAct"]),
            })
        return rows

    def get_horas_productivas(self, access_token: str) -> list[dict]:
        """Contrato EXACTO de `CAPACIDAD_PRODUCTIVAS_ROWS`: una fila por
        (Cliente, Proyecto) con `horas` aditivo. Reusa `project_facts` — el
        MISMO fetch ya hecho para Horas Vendidas/Participación, sin fuente
        nueva.

        Resolución de la contradicción de lineage registrada en HANDOFF
        (comentario de V0 en `reporting.js` decía `Sum(Time Logs
        Reports.HORAS)` vs. el resumen de Batch A que clasificaba esto como
        reuso del backbone de Horas por proyectos): los VALORES del mock
        `CAPACIDAD_PRODUCTIVAS_ROWS.horas` coinciden EXACTAMENTE, cliente×
        proyecto por cliente×proyecto, con `trabajadas` de
        `CARTERA_MANTENIMIENTO_ROWS`/`CARTERA_PROYECTO_ROWS` (Andesmar=134/
        40, Danone=92, YPF=340, AySA=210, Ternium=175 — las 6 filas, sin
        excepción). Evidencia directa de que "Horas Productivas" = SUM de
        `hsTrabajadas` de Horas por proyectos, NO una fuente separada. `%
        Incidencia` (denominador gran-total fijo) se calcula en el
        FRONTEND, sin cambios (mismo criterio que Participación)."""
        facts = self._repository.get_batch_a_facts(access_token)["project_facts"]
        groups: dict[tuple, float] = {}
        for f in facts:
            key = (f["cliente"], f["proyecto"])
            if not f["cliente"] or not f["proyecto"]:
                continue
            groups[key] = groups.get(key, 0.0) + f["hsTrabajadas"]
        return [
            {"cliente": cliente, "proyecto": proyecto, "horas": _round2(horas)}
            for (cliente, proyecto), horas in groups.items()
        ]
