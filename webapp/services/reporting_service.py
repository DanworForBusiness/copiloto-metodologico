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


class ReportingService:
    """Repositorio inyectable para tests (mock sin tocar `core.microsoft365`)."""

    def __init__(self, repository: ReportingRepository | None = None):
        self._repository = repository if repository is not None else ReportingRepository()
        self.last_diagnostics: dict = {}

    def get_cartera(self, access_token: str) -> dict:
        facts = self._repository.get_cartera_facts(access_token)
        mantenimiento = self._mantenimiento_rows(facts)
        proyecto = self._proyecto_rows(facts)
        # Diagnóstico SEGURO (conteos + nombres de clave de campos, nunca PII) —
        # passthrough del repo + los conteos de salida del service.
        self.last_diagnostics = dict(getattr(self._repository, "last_diagnostics", {}))
        self.last_diagnostics["mantenimiento_rows"] = len(mantenimiento)
        self.last_diagnostics["proyecto_rows"] = len(proyecto)
        return {"mantenimiento": mantenimiento, "proyecto": proyecto}

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
