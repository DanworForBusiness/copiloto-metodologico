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

import unicodedata
from datetime import datetime

from webapp.services.reporting_repository import ReportingRepository, _is_valid_anio, _norm_key

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


def _round3(x: float) -> float:
    """SOLO Horas Productivas (owner request — "representacion de horas con
    3 decimales, tile incluido"): `_round2` en `_shape_horas_productivas`/
    `_shape_horas_productivas_denominador` estaba destruyendo el 3er
    decimal ANTES de que el valor llegara al frontend — el formatter
    `hours3` (reporting.js) nunca podía mostrar precisión que ya no existía
    en el dato (ej. 5.4219999... llegaba pre-redondeado a 5.42, el frontend
    solo podía mostrar "5.42" sin importar cuántos decimales permitiera).
    Nunca usado por otras experiencias — esas siguen en `_round2`."""
    return round(x * 1000) / 1000


# --- Batch A (Horas Vendidas / Participación / Calendario) -----------------
# Reusa `_MESES`/`_month_num` de arriba (Cartera) — MISMO parseo del texto
# de mes de "Horas por proyectos", nunca una segunda función.

# Orden real de negocio de Frecuencia — CORREGIDO contra evidencia PRIMARIA
# del PBIP (owner live comparison: "la tabla Frecuencia no ordena igual al
# PBIP, que ordena por ID donde además es cronológico"). `Cliente.tmdl`,
# columna calculada `'ID Frecuencia'`:
#   SWITCH(Cliente[Frecuencia Actualizacion],
#     "Mensual", 1, "Bimestral", 2, "Trimestral", 3, "Cuatrimestral", 4,
#     "Semestral", 5, "Anual", 6, "7  Sin especificar")
# La lista previa tenía "Semanal" (semanal) en el índice 2 — un valor que ni
# siquiera existe en el SWITCH real del PBIP (el mock V0 lo "verificaba"
# contra sí mismo, nunca contra el TMDL) y que además rompía la
# cronología (semanal < mensual, nunca podría ir segundo). MISMA lista que
# `FRECUENCIA_ORDER` en reporting.js, nunca reordenada acá.
_FRECUENCIA_ORDER = ["Mensual", "Bimestral", "Trimestral", "Cuatrimestral", "Semestral", "Anual"]


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


_MESES_NOMBRES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

# --- Batch B: Horas Productivas / Time Logs Reports (ver Correction 1 +
# real-data-lineage.md §8bis) -------------------------------------------
# Valores de NEGOCIO (nombres reales de Proyecto/Cliente que aparecen en el
# CSV), NO internal names de SharePoint — viven acá (shaping de negocio),
# no en el repositorio. Los 2 proyectos PLACEHOLDER internos que separan
# No Asignadas de Productivas (page.json "Hs No Asignadas" del PBIP:
# filtro `Project IN {...}`), y el marcador de Cliente placeholder que
# excluye Productivas (page.json "Horas Productivas consumidas": `NOT
# Cliente[Cliente] CONTAINS "Danwor"`).
_TIME_LOGS_NO_ASIGNADAS_PROJECTS = {
    "Danwor - Horas No Productivas (#1)",
    "Danwor - Horas No Productivas (#2)",
}
_TIME_LOGS_CLIENTE_INTERNO_MARCA = "danwor"

# Task fuente de "Horas No Asignadas" (FINAL PBIP SOURCE-PARITY CORRECTIVE —
# owner golden case 2026: PBI Task slicer = "01. No Asignación a Servicio en
# Cliente", Ene-Ago = 130/90/113/152/216/172/208/59, Total 1.139). El subtab
# Horas Productivas → No Asignadas se filtra por TASK (grano consultor-céntrico),
# NO por los 2 proyectos placeholder (con los que el Hub daba 0 filas).
# Rentabilidad/"No asignadas a servicios" (escalar de portfolio, LIVE RECONCILED)
# sigue con el filtro por proyecto placeholder — NO se toca.
#
# MATCH POR FRASE NÚCLEO (owner: el match al string COMPLETO seguía dejando No
# Asignadas en 0 filas en vivo — el valor real difiere del literal del slicer en
# algún detalle de representación). Se matchea por CONTENIDO del núcleo estable
# "no asignacion a servicio" (acento-insensible) — robusto al prefijo numérico
# ("01. "/"1. "/ninguno), singular/plural ("servicio"/"servicios") y variantes
# de cierre ("en cliente"/"en el cliente"), sin dejar de ser específico (no
# matchea tareas no relacionadas).
_NO_ASIGNADAS_TASK = "01. No Asignación a Servicio en Cliente"


def _norm_task(task: str) -> str:
    """Normaliza un Task de Time Logs para el match: quita acentos (NFD +
    descarta diacríticos), colapsa whitespace y casefold — neutraliza toda
    diferencia de representación (case/acento/espacios)."""
    s = unicodedata.normalize("NFD", str(task or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.split()).casefold()


_NO_ASIGNADAS_TASK_CORE = _norm_task("No Asignación a Servicio")


def _is_no_asignadas_task(task: str) -> bool:
    """True si el Task es el de "No Asignación a Servicio" (match por frase
    núcleo, ver arriba)."""
    return _NO_ASIGNADAS_TASK_CORE in _norm_task(task)


def _time_logs_month_num(mes: str) -> int | None:
    """Índice 1-12 desde el `mes` YA normalizado a 'YYYY-MM' por el
    repositorio (`_time_logs_mes`) — NO reusa `_month_num` (ese parsea el
    formato de Cartera: nombre en español o número crudo, un formato
    DISTINTO)."""
    text = (mes or "").strip()
    if len(text) != 7 or text[4] != "-":
        return None
    try:
        n = int(text[5:7])
    except ValueError:
        return None
    return n if 1 <= n <= 12 else None


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
        """UN método de entrada para las experiencias FULL-HISTORY que reusan
        el backbone de Cartera — Calendario y Salud de Cliente (categóricas
        puras sobre `cliente_dim`, sin grano temporal). El router llama esto
        UNA vez (endpoint consolidado), nunca 2 POSTs separados que repitan
        el mismo fetch de Cliente.

        SCOPE DECOUPLING (owner directive — "batch-a como frontera de runtime:
        visitar una experiencia no debería cargar datos de otra sólo porque
        comparten fuente física"): Horas Vendidas Y Participación SALIERON de
        este grupo — AMBAS consumen su propia ventana caliente (`anios=...`,
        scope temporal) por un endpoint propio (ver `get_horas_vendidas`/
        `get_participacion`), sin pagar el volumen de la historia completa
        que Calendario/Salud (sin Año, no la necesitan) siguen leyendo acá.

        PERFORMANCE CORRECTIVE (owner live QA — Horas Vendidas >40s, ver
        HANDOFF): las experiencias comparten `get_batch_a_facts` en UN fetch,
        shaping puro de Python (barato) por separado."""
        # PERF: SOLO cliente_dim (Calendario/Salud son categóricas puras) — NO
        # se lee "Horas por proyectos" (full-history, la lectura más cara) ni
        # Proyectos: su `project_facts` ya no lo consume nadie acá (Participación
        # migró a su endpoint scopeado). Ver `get_cliente_dim`.
        cliente_dim = self._repository.get_cliente_dim(access_token)
        return {
            "calendario": self._shape_calendario(cliente_dim),
            "salud": self._shape_salud(cliente_dim),
        }

    def get_batch_b(self, access_token: str) -> dict:
        """UN método de entrada para Horas Productivas (AMBAS perspectivas,
        Productivas Y No Asignadas) — el router llama esto UNA vez, nunca 2
        POSTs separados que repitan la descarga/combine de TODOS los CSV de
        Time Logs (costo real, a diferencia de un GET de lista SharePoint —
        ver `ReportingRepository.get_time_logs_facts`)."""
        facts = self._repository.get_time_logs_facts(access_token)
        colaboradores = self._repository.get_colaboradores_email_map(access_token)
        return {
            "horasProductivas": self._shape_horas_productivas(facts),
            "horasProductivasNoAsignadas": self._shape_horas_productivas_no_asignadas(facts, colaboradores),
            # SOURCE-PARITY corrective (ver _shape_horas_productivas_denominador):
            # denominador REAL de "% Incidencia", grano {anio: [horas x 12]} —
            # UN solo grand-total compartido por AMBOS paneles (misma fórmula
            # DAX salvo cuál columna de identidad se remueve, lo que no cambia
            # la suma), nunca por-fila.
            "horasProductivasDenominador": self._shape_horas_productivas_denominador(facts),
        }

    def get_salud(self, access_token: str) -> list[dict]:
        """Wrapper delgado de `_shape_salud` con su PROPIO fetch (lean:
        SOLO cliente_dim, sin leer Horas por proyectos) — usado standalone/en
        tests; `get_batch_a` hace el fetch UNA vez para Calendario+Salud."""
        cliente_dim = self._repository.get_cliente_dim(access_token)
        return self._shape_salud(cliente_dim)

    def _shape_salud(self, cliente_dim: list[dict]) -> list[dict]:
        """Contrato EXACTO de `COMERCIAL_SALUD_ROWS`: una fila por Cliente
        con los 5 componentes categóricos + Salud Final, tal cual vienen de
        la lista Cliente (columnas de TEXTO puras, sin measure — NUNCA
        agregación numérica, ver HANDOFF). Filas sin Cliente se omiten."""
        rows = []
        for dim in cliente_dim:
            if not dim["cliente"]:
                continue
            rows.append({
                "cp": dim["clientPartner"],
                "cliente": dim["cliente"],
                "saludFinanciera": dim["saludFinanciera"],
                "saludRelacion": dim["saludRelacion"],
                "saludOperativo": dim["saludOperativo"],
                "saludContinuidad": dim["saludContinuidad"],
                "saludEstrategico": dim["saludEstrategico"],
                "saludFinal": dim["saludFinal"],
            })
        return rows

    def get_cartera(self, access_token: str) -> dict:
        """Contrato combinado RETROCOMPATIBLE (sin `perspectiva` — AMBAS
        perspectivas desde UN fetch, CON Facturadas porque Proyecto la
        necesita). Preferir `get_cartera_mantenimiento`/`get_cartera_
        proyecto` desde el router cuando se conoce la perspectiva activa
        (P5, owner live QA de performance — Cartera ~17s): Mantenimiento
        NUNCA necesita esperar la descarga/parseo de la XLSX."""
        facts = self._repository.get_cartera_facts(access_token, include_facturadas=True)
        return {
            "mantenimiento": self._mantenimiento_rows(facts),
            "proyecto": self._proyecto_rows(facts),
        }

    def get_cartera_mantenimiento(self, access_token: str, *, anios: list[str] | None = None) -> list[dict]:
        """Perspectiva Mantenimiento SOLA (P5) — `include_facturadas=False`
        salta por completo la descarga/parseo de la XLSX de Facturadas
        (`_mantenimiento_rows` nunca lee `facturadasProyecto`, ver abajo:
        `facturadas == certificadas` ahí, source-faithful).

        `anios` (HOT WINDOW / COLD HISTORY corrective, ver HANDOFF): `None`
        (default, primera hidratación del tab) resuelve a la VENTANA
        CALIENTE por defecto — año calendario actual + el anterior (`new
        Date()`/`datetime.now()`, nunca un literal hardcodeado) — NUNCA se
        hidratan 3-4+ años de historia solo porque el registro sigue "En
        curso". Una lista explícita (p. ej. `["2024"]`) es el lazy-load de
        un año FRÍO puntual, disparado por el usuario al elegirlo en el
        <select> de Año — ver `reporting_cartera_data` (router) para el
        contrato de `anios` como form param."""
        hot_years = anios if anios is not None else self._default_hot_years()
        facts = self._repository.get_cartera_facts(access_token, include_facturadas=False, hot_years=hot_years)
        return self._mantenimiento_rows(facts)

    def get_cartera_mantenimiento_anios(self, access_token: str) -> list[dict]:
        """Metadata de años/estado disponibles de Mantenimiento (HOT WINDOW
        / COLD HISTORY corrective) — separada de `get_cartera_mantenimiento`
        a propósito: las OPCIONES del <select> de Año NUNCA deben
        confundirse con los años ya HIDRATADOS (un año frío sigue siendo
        una opción seleccionable, ver HANDOFF: "DO NOT CONFUSE OPTIONS WITH
        HYDRATED FACTS"). Delegado 1:1 al repository — ver ahí el
        `$select` acotado."""
        return self._repository.get_cartera_mantenimiento_anio_estado_pairs(access_token)

    @staticmethod
    def _default_hot_years() -> list[str]:
        current = datetime.now().year
        return [str(current), str(current - 1)]

    def get_cartera_proyecto(self, access_token: str) -> list[dict]:
        """Perspectiva Proyecto SOLA — SÍ necesita la XLSX
        (`facturadasProyecto`, ver `_proyecto_rows`). NUNCA recibe/aplica
        `hot_years` — agrega la VIDA COMPLETA del proyecto (ver
        `_proyecto_rows`: `certificadas`/`trabajadas`/`vendidas` son sumas
        de TODOS los meses históricos), una ventana acá rompería el
        contrato de totales de por vida. El hot-window de este corrective
        es EXCLUSIVO de Mantenimiento (grano mensual, con default que
        aterriza en el mes/año calendario actual)."""
        facts = self._repository.get_cartera_facts(access_token, include_facturadas=True)
        return self._proyecto_rows(facts)

    # --- Mantenimiento: grano mensual, facturadas = certificadas -------------

    def _mantenimiento_rows(self, facts: list[dict]) -> list[dict]:
        rows = []
        for f in facts:
            if f.get("tipoProyecto") != "Mantenimiento":
                continue
            trabajadas = _round2(f["hsTrabajadas"])
            certificadas = _round2(f["hsCertificadas"])
            mes_num = _month_num(f["mes"])
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
                # UX1 (owner live QA): Año/Mes reemplazan Desde/Hasta como
                # filtro de Mantenimiento — grano MENSUAL real de la fuente
                # (Horas por proyectos es proyecto×mes). `mes` normalizado a
                # string "1".."12" (mismo índice+1 que el resto de la app,
                # ver `_month_num`) para matchear el <select> del frontend
                # (valores HTML siempre son string); fila con mes no
                # parseable ⇒ "" (nunca inventa un mes).
                "anio": f["anio"],
                "mes": str(mes_num) if mes_num is not None else "",
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

    # --- Batch B: Rentabilidad (Resumen / Certificadas vs Ejecutadas) -------
    # Ejecutadas/Trabajadas verificado con evidencia PRIMARIA del PBIP
    # (`Comparativo.tmdl`+`'Horas  Trabajadas por Mes'.tmdl`, ver
    # real-data-lineage.md §10): `Total Horas Trabajadas` = SUMMARIZE(Time
    # Logs Reports, Project, Mes, SUM(HORAS)) — la MISMA fuente/adapter que
    # Horas Productivas (Correction 1), NO `Horas por proyectos[HsTrabajadas]`
    # (ese es un campo DISTINTO, tracking manual de SharePoint que Cartera
    # usa para SU PROPIO "Trabajadas" — palabra igual, medida distinta,
    # confirmado por el propio comentario histórico de V0 en reporting.js:
    # "Certificadas viene de SharePoint, Ejecutadas/Trabajadas viene de
    # Time"). Certificadas = `Horas por proyectos[Hs certificadas]`
    # (SharePoint, MISMO backbone que Cartera — `get_cartera_facts`, sin
    # fetch nuevo para esa mitad).
    #
    # SOURCE-PARITY RECONCILIATION (owner live comparison vs PBIP, golden
    # caso 2026/Junio/Mantenimiento/En curso — PBIP Vendidas=4,883/
    # Certificadas=4,858/Trabajadas=4,624.5/Rentabilidad=233.5, Hub mostraba
    # 5,775/4,858/4.6/4,853.4). Certificadas YA reconciliaba exacto — prueba
    # de que esa mitad era correcta. Los otros dos SÍ eran defectos reales,
    # cada uno en un punto distinto:
    #
    # 1) TRABAJADAS casi desaparecía (4.6 en vez de 4,624.5). Causa raíz: el
    #    join Cartera↔Time Logs de este método usaba el TÍTULO RESUELTO de
    #    Proyecto como CLAVE EXACTA de diccionario. `get_time_logs_facts`
    #    resuelve ese título vía `_norm_key` (Upper+sin espacios) contra
    #    Proyectos — pero cuando el texto crudo del CSV de Time Logs no
    #    matchea ningún título de Proyectos (nombres históricos con drift),
    #    el join falla ahí y esa fila cae al texto CRUDO del CSV (fallback
    #    ya documentado en `get_time_logs_facts`), que después nunca
    #    matchea la clave prolija que `get_cartera_facts` sí resuelve — esas
    #    horas quedaban invisibles en `proyectos_rows`. El PBIP nunca exige
    #    ese match exacto: `Comparativo` (el partition calculado que
    #    unifica las 4 fuentes) agrupa por `Key_Proyect_desc`/`Proyecto_Key`
    #    — la MISMA normalización Upper+sin-espacios que `_norm_key` ya
    #    implementa acá (docstring de esa función, ya usada para el join de
    #    Facturadas XLSX). Fix: `cert_by_proyecto`/`trab_by_proyecto` ahora
    #    usan `_norm_key(proyecto)` como clave — estrictamente MÁS
    #    permisivo que el match exacto anterior (solo puede sumar joins que
    #    antes fallaban, nunca introduce un falso positivo que el PBIP no
    #    consideraría el mismo proyecto), reproduciendo el mecanismo real.
    #
    # 2) VENDIDAS sumaba de más (5,775 vs 4,883, +892). Causa raíz: Vendidas
    #    se trataba como un escalar de PORTFOLIO fijo (sumado sobre TODOS
    #    los `cartera_facts` sin importar Tipo Proyecto/Estado/Cliente/
    #    Proyecto/Client Partner) — un comentario previo afirmaba "Vendidas
    #    no existe como campo por-proyecto", pero `HsVendidas` vive en la
    #    MISMA fila de "Horas por proyectos" que `Hs certificadas` (ver
    #    `get_cartera_facts` — mismo `f["hsVendidas"]` por proyecto×mes),
    #    así que SÍ es atribuible por proyecto, e igual de filtrable. El
    #    PBIP confirma que debe filtrarse: la página "Analisis Rentabilidad"
    #    tiene slicers de "Tipo Proyecto Dash" Y "Status" (ver page.json),
    #    que aplican a Vendidas igual que a Certificadas. Fix: Vendidas
    #    ahora se acumula POR PROYECTO (`vendByYear`, mismo shape que
    #    `certByYear`) en vez de un escalar de portfolio, y fluye por el
    #    MISMO pipeline de facets que Certificadas/Trabajadas (Estado +
    #    CP/Cliente/Tipo/Proyecto, ver `rentabilidadSelectedRows` en
    #    reporting.js). "No asignadas a servicios" SIGUE siendo portfolio
    #    fijo — es Time Logs de los 2 proyectos placeholder, fuera del
    #    universo de Proyectos (sin Tipo/Estado propios que filtrar), el
    #    PBIP tampoco lo filtra.
    def get_rentabilidad(self, access_token: str, *, anios: list[str] | None = None) -> dict:
        """Contrato para AMBAS perspectivas de Rentabilidad: `proyectos`
        (una fila por proyecto — TODOS los tipos, a diferencia de Cartera
        que separa Mantenimiento/Proyecto — con `certificadas`/`trabajadas`/
        `vendidas` escalares agregados sobre la vida del proyecto para el
        detalle/ranking, MÁS `certMonthly`/`trabMonthly`/`vendMonthly[12]`
        reales para la matriz mensual de Concepto) + `noAsignadasMonthly[12]`
        (portfolio fijo, ver arriba). Join Cartera↔Time Logs por
        `_norm_key(proyecto)` (Upper+sin espacios — MISMA normalización que
        el PBIP usa para unir estas dos fuentes en `Comparativo`, ver
        comentario arriba; ambas fuentes derivan del mismo universo de
        Proyectos, pero el join YA NO exige que ambos lados hayan resuelto
        al string EXACTO). Un proyecto SharePoint sin ninguna hora en Time
        Logs (sin tracking aún) aporta trabajadas=0 en TODOS los meses —
        NUNCA se omite la fila.

        `anios` (HOT WINDOW / COLD HISTORY corrective, owner live QA —
        "hot window was applied to Horas Vendidas but not Rentabilidad"):
        `None` (primera hidratación del tab) resuelve a la ventana caliente
        por defecto (año actual + anterior, `_default_hot_years` — MISMO
        contrato que `get_horas_vendidas`). Una lista explícita es el
        lazy-load de un año FRÍO puntual. Acota SOLO la lectura de "Horas
        por proyectos" (vía `get_cartera_facts(hot_years=...)`, MISMO
        helper que Cartera/Horas Vendidas — cero lógica Graph duplicada).
        `include_facturadas=False`: Rentabilidad NUNCA lee
        `facturadasProyecto` (solo certificadas/vendidas) — la XLSX de
        Facturadas es un costo real que esta experiencia pagaba sin
        necesitarlo, mismo criterio que Cartera/Mantenimiento. Time Logs
        (CSV) queda SIN acotar — no hay un mecanismo de scope físico
        análogo a `$filter` para archivos ya descargados completos; ver
        HANDOFF para la medición pendiente de si esto importa en la
        práctica."""
        hot_years = anios if anios is not None else self._default_hot_years()
        cartera_facts = self._repository.get_cartera_facts(access_token, include_facturadas=False, hot_years=hot_years)
        time_logs = self._repository.get_time_logs_facts(access_token)

        # Grano POR-AÑO (migración Año/Mes/Estado, HANDOFF §-16 #8): Ene-2025 y
        # Ene-2026 quedan direccionables por separado — el filtro Año del browser
        # elige el `[12]` de ese año. `A_x00f1_o`/Mes (SharePoint) y
        # anio/mes (Time Logs, derivados del mismo Date) son las fuentes de año.
        # TEMPORAL DOMAIN CONTRACT: `_is_valid_anio` (nunca `if anio:` solo,
        # ver reporting_repository.py) en AMBOS loops — Cartera Y Time Logs
        # son fuentes de año independientes, cualquiera puede traer basura.
        #
        # Clave de agrupación = `_norm_key(proyecto)` (ver comentario del
        # SOURCE-PARITY RECONCILIATION arriba, defecto #1) — el nombre de
        # negocio para MOSTRAR se preserva por separado (`g["proyecto"]`,
        # primer valor visto bajo esa clave) porque `_norm_key` no es
        # presentable (Upper+sin espacios).
        cert_by_proyecto: dict[str, dict] = {}
        for f in cartera_facts:
            proyecto = f.get("proyecto")
            if not proyecto:
                continue
            key = _norm_key(proyecto)
            g = cert_by_proyecto.setdefault(key, {
                "proyecto": proyecto, "cliente": f["cliente"], "cp": f["clientPartner"],
                "tipo": f["tipoProyecto"], "estado": f.get("estado", ""),
                "certByYear": {}, "vendByYear": {}, "facts": [],
            })
            anio = f.get("anio")
            n = _month_num(f.get("mes"))
            if _is_valid_anio(anio) and n is not None:
                g["certByYear"].setdefault(anio, [0.0] * 12)[n - 1] += f["hsCertificadas"]
                # Defecto #2 (ver arriba): Vendidas ahora POR PROYECTO, ya no
                # un escalar de portfolio — mismo tratamiento que Certificadas.
                g["vendByYear"].setdefault(anio, [0.0] * 12)[n - 1] += f["hsVendidas"]
            g["facts"].append(f)

        trab_by_proyecto: dict[str, dict] = {}
        no_asignadas_by_year: dict[str, list[float]] = {}
        for tl in time_logs:
            anio = tl.get("anio")
            n = _time_logs_month_num(tl.get("mes"))
            if not _is_valid_anio(anio) or n is None:
                continue
            if tl.get("proyecto") in _TIME_LOGS_NO_ASIGNADAS_PROJECTS:
                no_asignadas_by_year.setdefault(anio, [0.0] * 12)[n - 1] += tl["horas"]
                continue
            # Defecto #1 (ver arriba): `_norm_key`, no el título exacto —
            # reproduce el join real del PBIP (`Key_Proyect_desc`).
            key = _norm_key(tl.get("proyecto"))
            trab_by_proyecto.setdefault(key, {}).setdefault(anio, [0.0] * 12)[n - 1] += tl["horas"]

        def _round_year_map(m: dict) -> dict:
            return {a: [_round2(v) for v in arr] for a, arr in m.items()}

        def _collapse(m: dict) -> list[float]:
            out = [0.0] * 12
            for arr in m.values():
                for i, v in enumerate(arr):
                    out[i] += v
            return [_round2(v) for v in out]

        proyectos_rows = []
        for key, g in cert_by_proyecto.items():
            trab_by_year = trab_by_proyecto.get(key, {})
            cert_collapsed = _collapse(g["certByYear"])
            trab_collapsed = _collapse(trab_by_year)
            vend_collapsed = _collapse(g["vendByYear"])
            proyectos_rows.append({
                "tipo": g["tipo"], "cliente": g["cliente"], "cp": g["cp"], "proyecto": g["proyecto"],
                "estado": g["estado"],
                # Per-año (grano preservado hasta el filtrado en el browser).
                "certByYear": _round_year_map(g["certByYear"]),
                "trabByYear": _round_year_map(trab_by_year),
                "vendByYear": _round_year_map(g["vendByYear"]),
                # Colapsado a través de años — retrocompat (contrato previo) para
                # tests/consumidores que no seleccionan año.
                "certificadas": _round2(sum(cert_collapsed)),
                "trabajadas": _round2(sum(trab_collapsed)),
                "vendidas": _round2(sum(vend_collapsed)),
                "certMonthly": cert_collapsed,
                "trabMonthly": trab_collapsed,
                "vendMonthly": vend_collapsed,
                "fecha": _fecha_min(g["facts"]),
            })

        return {
            "proyectos": proyectos_rows,
            "noAsignadasByYear": _round_year_map(no_asignadas_by_year),
            # Colapsado retrocompat.
            "noAsignadasMonthly": _collapse(no_asignadas_by_year),
        }

    # --- Batch A: Participación / Calendario / Salud (full-history) ----------
    # Comparten UN fetch (`get_batch_a_facts`). Horas Vendidas se sirve aparte
    # con scope temporal (ver `get_horas_vendidas` + `get_horas_vendidas_anios`).

    def get_horas_vendidas(self, access_token: str, *, anios: list[str] | None = None) -> list[dict]:
        """Horas Vendidas con SCOPE TEMPORAL (owner directive — HV pide al
        mismo source-family SÓLO su ventana caliente, sin arrastrar la
        historia completa de Participación). `anios=None` (primera hidratación
        del tab) resuelve a la ventana caliente por defecto (año actual +
        anterior, `_default_hot_years` — nunca un literal hardcodeado). Una
        lista explícita (`["2024"]`) es el lazy-load de un año FRÍO puntual,
        disparado por el usuario al elegirlo en el <select> de Año — MISMO
        contrato que `get_cartera_mantenimiento(anios=...)`. El scope acota
        SOLO la lectura de Horas por proyectos (ver `get_batch_a_facts`)."""
        hot_years = anios if anios is not None else self._default_hot_years()
        facts = self._repository.get_batch_a_facts(access_token, hot_years=hot_years)["project_facts"]
        return self._shape_horas_vendidas(facts)

    def get_horas_vendidas_anios(self, access_token: str) -> list[dict]:
        """Metadata de años/estado disponibles de Horas Vendidas (todos los
        tipos) — separada del fetch de datos a propósito (misma razón que
        Cartera: las OPCIONES del <select> de Año nunca deben confundirse con
        los años ya HIDRATADOS por la ventana caliente). Delegado 1:1 al
        repository (`get_horas_anio_estado_pairs`)."""
        return self._repository.get_horas_anio_estado_pairs(access_token)

    def get_rentabilidad_anios(self, access_token: str) -> list[dict]:
        """Metadata de años/estado disponibles de Rentabilidad — MISMO
        dominio que Horas Vendidas (todos los tipos de proyecto, misma
        lectura mínima de Proyectos/Horas por proyectos) — reusa el MISMO
        `get_horas_anio_estado_pairs` en vez de un segundo método idéntico
        (owner directive — "do not duplicate temporal runtime logic")."""
        return self._repository.get_horas_anio_estado_pairs(access_token)

    def _shape_horas_vendidas(self, facts: list[dict]) -> list[dict]:
        """Contrato EXACTO de `HORASVENDIDAS_ROWS` (reporting.js): una fila
        por (Client Partner, Tipo, Cliente, Proyecto) con `monthly[12]`
        (Ene..Dic, aditivo) de Hs Vendidas. `fecha` = primer mes con datos
        de ese proyecto (ver `_fecha_min`)."""
        groups: dict[tuple, dict] = {}
        for f in facts:
            key = (f["clientPartner"], f["tipoProyecto"], f["cliente"], f["proyecto"])
            g = groups.setdefault(key, {"byYear": {}, "collapsed": [0.0] * 12,
                                        "facts": [], "estado": f.get("estado", "")})
            n = _month_num(f["mes"])
            if n is not None:
                g["collapsed"][n - 1] += f["hsVendidas"]
                anio = f.get("anio")
                # TEMPORAL DOMAIN CONTRACT (owner live QA — "Septiembre" en
                # el <select> de Año): `_is_valid_anio`, nunca `if anio:`
                # solo — ver reporting_repository.py para el contrato
                # completo, reusado acá tal cual (nunca reimplementado).
                if _is_valid_anio(anio):
                    g["byYear"].setdefault(anio, [0.0] * 12)[n - 1] += f["hsVendidas"]
                g["facts"].append(f)
        rows = []
        for (cp, tipo, cliente, proyecto), g in groups.items():
            rows.append({
                "cp": cp, "tipo": tipo, "cliente": cliente, "proyecto": proyecto,
                "estado": g["estado"],
                # Grano POR-AÑO (migración Año/Mes/Estado, HANDOFF §-16 #8): Año
                # single-select elige el [12] de ese año; `monthly` colapsado queda
                # como retrocompat (contrato previo).
                "vendidasByYear": {a: [_round2(v) for v in arr] for a, arr in g["byYear"].items()},
                "monthly": [_round2(v) for v in g["collapsed"]],
                "fecha": _fecha_min(g["facts"]),
            })
        return rows

    def get_participacion(self, access_token: str, *, anios: list[str] | None = None) -> list[dict]:
        """Participación con SCOPE TEMPORAL (owner directive — MISMO
        principio que Horas Vendidas/Rentabilidad, "no mantener Participación
        en full-history solo porque el batch-a viejo lo hacía"): `anios=None`
        (primera hidratación del tab) resuelve a la ventana caliente por
        defecto (año actual + anterior, `_default_hot_years`). Una lista
        explícita (`["2024"]`) es el lazy-load de un año FRÍO puntual —
        MISMO contrato que `get_horas_vendidas(anios=...)`. El scope acota
        SOLO la lectura de Horas por proyectos (ver `get_batch_a_facts`);
        `cliente_dim` (la POBLACIÓN de clientes) siempre se lee completa —
        Año NUNCA filtra qué clientes existen, solo qué años de Vendidas/
        Certificadas se suman (ver `_shape_participacion`)."""
        hot_years = anios if anios is not None else self._default_hot_years()
        raw = self._repository.get_batch_a_facts(access_token, hot_years=hot_years)
        return self._shape_participacion(raw)

    def get_participacion_anios(self, access_token: str) -> list[dict]:
        """Metadata de años/estado disponibles de Participación — MISMO
        dominio que Horas Vendidas/Rentabilidad (todos los tipos de
        proyecto, misma lectura mínima de Proyectos/Horas por proyectos) —
        reusa el MISMO `get_horas_anio_estado_pairs` en vez de un método
        idéntico (owner directive — "do not duplicate temporal runtime
        logic")."""
        return self._repository.get_horas_anio_estado_pairs(access_token)

    def _shape_participacion(self, raw: dict) -> list[dict]:
        """Contrato EXACTO de `COMERCIAL_PARTICIPACION_ROWS`: una fila por
        Cliente con `vendidasByYear`/`certificadasByYear` (mapas año→horas,
        sumadas sobre TODOS sus proyectos/meses de ese año — Mantenimiento +
        Proyecto, sin distinguir tipo, `% Hs Vendidas 2`/`% Hs Certificadas 2`
        no filtran por tipo en el PBIP fuente) + las columnas categóricas de
        Cliente ya resueltas. El % contra el gran total fijo Y la suma sobre
        los años tildados se calculan en el FRONTEND (Año es MULTI-SELECT,
        owner directive — ver `comercialParticipacionSelectedRows`); este
        método solo entrega el numerador crudo POR AÑO, nunca pre-sumado ni
        el % pre-calculado.

        POR-AÑO (owner directive — "Año as MULTI-SELECT CHECKBOXES... reuse
        the hot/cold infrastructure"): MISMO patrón que `_shape_horas_
        vendidas` (`vendidasByYear`) — un año frío lazy-loaded se FUSIONA acá
        (`Object.assign` en el frontend, ver `mergeComercialParticipacionRows`)
        sin perder los años ya hidratados, y el usuario puede des-tildar un
        año sin re-fetchear (el numerador de ESE año simplemente se excluye
        de la suma client-side).

        POBLACIÓN (owner live QA — "suspiciously few clients"): la fuente
        de verdad de QUÉ clientes existen es `cliente_dim` (una fila por
        Cliente de SharePoint, YA completa — `get_batch_a_facts` no filtra
        esa lista, y Año NUNCA la acota — ver `get_participacion`). Un
        Cliente sin ningún hecho de Horas por proyectos (en los años
        actualmente hidratados) SIGUE siendo un Cliente válido — antes se
        omitía por completo (`g is None: continue`), perdiendo filas reales
        de la dimensión; ahora solo se omite si el nombre no resolvió (`not
        dim["cliente"]`, fila sin identidad). Atributos de negocio opcionales
        en blanco (idFrecuencia/permiteAdelanto/condicionPago) llegan como ""
        acá — `buildTree` en el frontend YA los convierte en "Sin dato",
        nunca se fabrica un valor acá.

        ROOT CAUSE CORRECTIVE (owner live QA — "127 clientes visibles, TODOS
        con % Participación = 0.00%"): esta función producía `vendidasByYear`/
        `certificadasByYear` como `{anio: number}` (escalar plano por año),
        pero el frontend (`comercialParticipacionSelectedRows`, ver
        reporting.js) SIEMPRE esperó `{anio: {estado: number}}` — itera
        `Object.keys(r.vendidasByYear[anio])` para sumar SOLO los estados
        tildados. `Object.keys()` sobre un NÚMERO devuelve `[]` (un primitivo
        no tiene claves enumerables propias) — el `.forEach` nunca ejecutaba
        su cuerpo, así que `vendidas`/`certificadas` quedaban en 0 SIEMPRE,
        sin importar cuántos hechos reales existieran. Fix: el numerador
        ahora se bucketiza por `(año, estado)` de verdad — necesario además
        porque Estado es un atributo de PROYECTO (`Proyectos.Status`, un
        Cliente puede tener proyectos En curso Y Finalizado a la vez) pero
        Participación agrega por CLIENTE; sin este grano, tildar/destildar
        Estado no podría incluir/excluir selectivamente horas de proyectos
        sin re-fetchear (ver `comercialParticipacionSelectedRows`)."""
        by_cliente: dict[str, dict] = {}
        for f in raw["project_facts"]:
            key = f["clienteId"] or f["cliente"]
            if not key:
                continue
            g = by_cliente.setdefault(key, {"vendidasByYear": {}, "certificadasByYear": {}, "facts": []})
            anio = f.get("anio")
            # TEMPORAL DOMAIN CONTRACT: `_is_valid_anio`, nunca `if anio:`
            # solo (ver reporting_repository.py) — reusado tal cual.
            if _is_valid_anio(anio):
                estado = f.get("estado") or ""
                vendidas_por_estado = g["vendidasByYear"].setdefault(anio, {})
                certificadas_por_estado = g["certificadasByYear"].setdefault(anio, {})
                vendidas_por_estado[estado] = vendidas_por_estado.get(estado, 0.0) + f["hsVendidas"]
                certificadas_por_estado[estado] = certificadas_por_estado.get(estado, 0.0) + f["hsCertificadas"]
            g["facts"].append(f)

        rows = []
        for dim in raw["cliente_dim"]:
            if not dim["cliente"]:
                # Fila de dimensión sin nombre resuelto ⇒ sin identidad,
                # nunca una fila "Sin dato" fabricada para el Cliente mismo
                # (a diferencia de sus atributos opcionales, que SÍ pueden
                # llegar en blanco).
                continue
            key = dim["clienteId"] or dim["cliente"]
            g = by_cliente.get(key) or {"vendidasByYear": {}, "certificadasByYear": {}, "facts": []}
            rows.append({
                # SOLO texto (owner request — "si en calendario decidimos no
                # mostrar los IDs de frecuencia... acá haría lo mismo"):
                # Calendario ya mostraba solo texto (`_normalize_frecuencia`,
                # ver `_shape_calendario`) — Participación mostraba "1
                # Mensual" (`_id_frecuencia`, con el número), inconsistente.
                # Clave del campo/columna sigue siendo `idFrecuencia`
                # (nombre interno, matchea el rótulo "ID Frecuencia" del
                # PBIP — un LABEL, no el valor) para no forzar un rename en
                # cascada de columnas/jerarquía/tests por algo cosmético.
                "idFrecuencia": _normalize_frecuencia(dim["frecuenciaActualizacion"]),
                "permiteAdelanto": dim["permiteAdelanto"],
                "condicionPago": dim["condicionPago"],
                "cliente": dim["cliente"],
                "cp": dim["clientPartner"],
                "vendidasByYear": {
                    a: {e: _round2(v) for e, v in por_estado.items()}
                    for a, por_estado in g["vendidasByYear"].items()
                },
                "certificadasByYear": {
                    a: {e: _round2(v) for e, v in por_estado.items()}
                    for a, por_estado in g["certificadasByYear"].items()
                },
                "fecha": _fecha_min(g["facts"]),
            })
        return rows

    def get_calendario(self, access_token: str) -> list[dict]:
        """Wrapper delgado de `_shape_calendario` con su PROPIO fetch (lean:
        SOLO cliente_dim, sin leer Horas por proyectos) — usado standalone/en
        tests; `get_batch_a` hace el fetch UNA vez para Calendario+Salud."""
        cliente_dim = self._repository.get_cliente_dim(access_token)
        return self._shape_calendario(cliente_dim)

    def _shape_calendario(self, cliente_dim: list[dict]) -> list[dict]:
        """Contrato EXACTO de `CALENDARIO_ROWS`: una fila por Cliente con
        `frecuencia` (normalizada) y `monthly[12]` booleano (ver
        `_calendario_monthly`). Filas sin Cliente/frecuencia reconocible se
        omiten — nunca una fila con frecuencia vacía fabricada."""
        rows = []
        for dim in cliente_dim:
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
        """Wrapper delgado de `_shape_horas_productivas` con su PROPIO
        fetch — usado standalone/en tests; `get_batch_b` hace el fetch UNA
        vez para ambas perspectivas y llama el shaping directo (ver ahí)."""
        facts = self._repository.get_time_logs_facts(access_token)
        return self._shape_horas_productivas(facts)

    def _shape_horas_productivas(self, facts: list[dict]) -> list[dict]:
        """Contrato EXACTO de `CAPACIDAD_PRODUCTIVAS_ROWS`: una fila por
        (Cliente, Proyecto) con `horas` aditivo (colapsado retrocompat,
        TODOS los meses) + `horasByYear` (grano POR-AÑO, `{anio: [12]}`,
        MISMO patrón que `_shape_horas_vendidas` — Año/Mes, ticket "CONTINUE
        REPORTING": el PBI de referencia muestra Año/Mes como slicers de
        página sobre ESTA tabla, ver real-data-lineage.md §8ter item 1) +
        `porcentajePart` (número YA multiplicado por 100, constante por
        Cliente — alimenta el panel "Cliente + Part", ver abajo) + `estado`
        (Proyectos.Status, constante por Proyecto — owner corrective:
        filtro de Estado faltante, MISMO campo/join que Cartera/Rentabilidad
        leen, nunca un segundo fetch).

        CORRECTION 1 CERRADA (ver HANDOFF/real-data-lineage.md §8bis):
        reimplementado sobre `get_time_logs_facts` (CSV, sitio Operaciones)
        — evidencia PRIMARIA del PBIP probó que la fuente real es
        `SUM('Time Logs Reports'[HORAS])`, NO `hsTrabajadas` de Horas por
        proyectos (la implementación previa, `5dcbb58`, se apoyaba SOLO en
        coincidencia de valores del mock, marcada insuficiente por el
        owner). Filtro `NOT Cliente CONTAINS "Danwor"` reproduce el filtro
        de página real ("Horas Productivas consumidas": excluye el/los
        cliente(s) placeholder interno(s) donde se cargan las 2 horas No
        Asignadas) — y se excluye por Proyecto placeholder también, belt-
        and-suspenders con el mismo criterio que No Asignadas usa para
        incluir. `% Incidencia` (denominador gran-total fijo) se calcula en
        el FRONTEND, sin cambios (mismo criterio que Participación).

        "CLIENTE + PART" (§8ter item 1 — RESUELTO): evidencia PRIMARIA
        (`Time Logs Reports.tmdl` línea 300) muestra que la columna
        calculada `'Cliente + Part'` NO es una dimensión de agrupamiento
        distinta — es `[Client] &" "& RELATED(Cliente[Porcentaje de
        Part.])*100`, el MISMO Cliente con un valor de referencia ESTÁTICO
        pegado al rótulo. El panel derecho del PBI reusa el MISMO
        RANKX/SUM/REMOVEFILTERS que el panel izquierdo (`Rank_T3` apunta
        `ALLSELECTED` a la columna calculada en vez de `[Client]`, patrón
        típico de PBI para que RANKX calce con el contexto de fila del
        visual — no una fórmula de negocio distinta). Por eso este método
        NO shapea un segundo dataset: `porcentajePart` viaja en la MISMA
        fila que `cliente`/`horas`, y el frontend arma el rótulo compuesto
        "{cliente} {porcentajePart}%" para el panel derecho reusando ESTAS
        MISMAS filas (ver `renderCapacidadProductivasClientePart` en
        reporting.js) — nunca un fetch/agregación paralela que pueda
        divergir del panel izquierdo."""
        groups: dict[tuple, dict] = {}
        for f in facts:
            cliente, proyecto = f["cliente"], f["proyecto"]
            if not cliente or not proyecto:
                continue
            if _TIME_LOGS_CLIENTE_INTERNO_MARCA in cliente.lower():
                continue
            if proyecto in _TIME_LOGS_NO_ASIGNADAS_PROJECTS:
                continue
            key = (cliente, proyecto)
            g = groups.setdefault(key, {
                "byYear": {}, "collapsed": 0.0, "porcentajePart": f.get("porcentajePart", 0.0),
                # Estado (Proyectos.Status, owner corrective — filtro
                # faltante en Horas Productivas): constante por
                # (Cliente, Proyecto), viaja igual que porcentajePart —
                # todas las filas de UN proyecto comparten el mismo Estado.
                "estado": f.get("estado", ""),
            })
            n = _time_logs_month_num(f["mes"])
            if n is not None:
                g["collapsed"] += f["horas"]
                anio = f.get("anio")
                # TEMPORAL DOMAIN CONTRACT: `_is_valid_anio`, nunca `if anio:`
                # solo — reusado tal cual (ver reporting_repository.py).
                if _is_valid_anio(anio):
                    g["byYear"].setdefault(anio, [0.0] * 12)[n - 1] += f["horas"]
        return [
            {
                "cliente": cliente, "proyecto": proyecto,
                "horas": _round3(g["collapsed"]),
                "horasByYear": {a: [_round3(v) for v in arr] for a, arr in g["byYear"].items()},
                "porcentajePart": _round2(g["porcentajePart"] * 100),
                "estado": g["estado"],
            }
            for (cliente, proyecto), g in groups.items()
        ]

    def _shape_horas_productivas_denominador(self, facts: list[dict]) -> dict[str, list[float]]:
        """Denominador REAL de "% Incidencia" (Horas Productivas — SOURCE-
        PARITY corrective, owner live comparison contra el PBI). Evidencia
        PRIMARIA (`Danwor - Tablero Integral.SemanticModel/definition/
        tables/Time Logs Reports.tmdl`): AMBOS paneles bindean a medidas
        DAX distintas pero de forma IDÉNTICA —

            '% Hs ejecutadas'  (panel izquierdo, Rank_T2):
            '% Hs ejecutadas2' (panel derecho, Rank_T3):
                DIVIDE(
                    SUM('Time Logs Reports'[HORAS]),
                    CALCULATE(
                        SUM('Time Logs Reports'[HORAS]),
                        REMOVEFILTERS(Cliente[Client Partner], Cliente[Frecuencia
                          Actualizacion], Cliente[Permite Adelanto],
                          Cliente[Condicion de Pago], Cliente[Cliente]),
                        REMOVEFILTERS('Time Logs Reports'[<propia columna de
                          identidad — Client para el panel izquierdo,
                          Cliente + Part para el derecho>])
                    )
                )

        REMOVEFILTERS NO toca Año/Mes/Proyecto — el denominador SIGUE
        escopeado por el filtro de fecha ambiental (Año/Mes activos), pero
        es un GRAN TOTAL respecto de TODO lo demás (Cliente/Client
        Partner/Proyecto — ninguno de estos tiene equivalente de slicer en
        la página real de Horas Productivas del PBIP, confirmado por
        `page.json`: solo hay slicers de Año/Mes ×2 —uno por panel— y
        Cliente/Client Partner compartidos, CERO slicer de Proyecto).

        HALLAZGO CRÍTICO (explica owner live evidence: LEFT ≈50.5 vs KPI
        visible 42.182 para el mismo Cliente/Año): `page.json` tiene DOS
        filtros de página que excluyen el cliente placeholder interno
        donde se cargan las 2 horas de "No Asignadas" — uno sobre
        `Cliente[Cliente]` ("NOT CONTAINS 'Danwor'"), otro sobre
        `'Time Logs Reports'[Client]` (mismo criterio). El denominador de
        AMBAS medidas hace `REMOVEFILTERS(Cliente[Cliente])` — Y la de
        LEFT además `REMOVEFILTERS('Time Logs Reports'[Client])` — ambos
        remueven EXACTAMENTE los campos donde viven esos 2 filtros de
        página, así que el denominador los DESHACE y reintroduce esas
        horas placeholder. Por eso es MÁS GRANDE que "Total Horas" (que sí
        las excluye, igual que cada fila mostrada) — un artefacto REAL del
        DAX original (REMOVEFILTERS es más amplio de lo que el autor
        probablemente intentaba), no un bug de Hub. "The denominator must
        reproduce the source measure semantics" (ticket) exige
        reproducirlo tal cual, no "corregirlo" a lo que Hub asumiría que
        es más prolijo.

        Por eso este método NO excluye el cliente placeholder interno NI
        proyectos "No Asignadas" (a diferencia de `_shape_horas_
        productivas`) — suma TODOS los hechos con mes/año reconocibles,
        SIN agrupar por Cliente/Proyecto. Tampoco filtra por Estado:
        Estado (`Proyectos.Status`) es una extensión Hub sin equivalente
        en el DAX original (no hay filtro/slicer de Estado en la página
        real) — el denominador queda Estado-AGNÓSTICO por diseño, MISMO
        criterio que Proyecto. Devuelve `{anio: [horas x 12]}` — grano
        idéntico a `horasByYear` de cada fila, para que el frontend
        reutilice el MISMO motor de proyección Año/Mes/mes-cap
        (`_sumMonthlyArrays`/`_maskMeses`/`_applyMonthCap`) sin duplicar
        lógica temporal."""
        totals: dict[str, list[float]] = {}
        for f in facts:
            n = _time_logs_month_num(f["mes"])
            if n is None:
                continue
            anio = f.get("anio")
            if not _is_valid_anio(anio):
                continue
            totals.setdefault(anio, [0.0] * 12)[n - 1] += f["horas"]
        # _round3 (owner request — mismo criterio que _shape_horas_productivas
        # de arriba): el denominador alimenta directo el tile "Total Horas"
        # (capacidadProductivasTotal) — redondear acá a 2 decimales
        # reproducía el mismo corte de precisión aunque el frontend ya
        # pidiera "hours3".
        return {a: [_round3(v) for v in arr] for a, arr in totals.items()}

    def get_horas_productivas_no_asignadas(self, access_token: str) -> list[dict]:
        """Wrapper delgado de `_shape_horas_productivas_no_asignadas` con
        su PROPIO fetch — usado standalone/en tests; `get_batch_b` hace el
        fetch UNA vez para ambas perspectivas y llama el shaping directo."""
        facts = self._repository.get_time_logs_facts(access_token)
        colaboradores = self._repository.get_colaboradores_email_map(access_token)
        return self._shape_horas_productivas_no_asignadas(facts, colaboradores)

    def _shape_horas_productivas_no_asignadas(self, facts: list[dict], colaboradores: dict[str, str]) -> list[dict]:
        """Contrato EXACTO de `CAPACIDAD_NOASIGNADAS_ROWS`: una fila por
        Consultor con `monthly[12]` (horas No Asignadas por mes, aditivo) +
        `hsAsig`. Reusa los MISMOS `facts`/`colaboradores` que Productivas
        (ver `get_batch_b` — UN fetch/combine de CSV para las 2
        perspectivas), filtrado por TASK = `_NO_ASIGNADAS_TASK` (slicer del
        PBI "01. No Asignación a Servicio en Cliente" — FINAL PBIP SOURCE-
        PARITY CORRECTIVE, antes filtraba por 2 proyectos placeholder y daba
        0 filas pese a haber data 2026), agrupado por Consultor vía
        `colaboradores` (join `Time Logs Reports.Email →
        Colaboradores.EMail_Colaborador`, MISMA relación confirmada en el
        TMDL del PBIP). Filas con Email sin Colaborador resuelto se omiten
        (nunca un Consultor "Desconocido" fabricado).

        GAP DOCUMENTADO (no adivinado): `hsAsig` (capacidad de referencia
        estática del consultor, mock `Colaboradores['Consultor + Hs asig']`)
        requiere `Colaboradores['HS asignadas a proy'] =
        RELATED('Resumen Asignaciones'[Hs Asignadas])` — una lista
        SharePoint adicional (`Resumen Asignaciones`) SIN ID confirmado en
        este repositorio (`config.py` no tiene una constante para ella).
        Queda en 0 explícitamente en vez de adivinar un list ID — a
        resolver cuando el owner confirme esa lista (mismo criterio que
        cualquier otro gap de este proyecto: nunca inventar un internal
        name/ID)."""
        groups: dict[str, list[float]] = {}
        for f in facts:
            # FINAL PBIP SOURCE-PARITY CORRECTIVE: se filtra por TASK (slicer del
            # PBI "No Asignación a Servicio"), NO por los 2 proyectos placeholder
            # (con los que el Hub daba "Sin filas" pese a haber data 2026). Match
            # por frase núcleo (ver `_is_no_asignadas_task`).
            if not _is_no_asignadas_task(f.get("task", "")):
                continue
            consultor = colaboradores.get(f["email"].lower())
            if not consultor:
                continue
            n = _time_logs_month_num(f["mes"])
            if n is None:
                continue
            monthly = groups.setdefault(consultor, [0.0] * 12)
            monthly[n - 1] += f["horas"]
        return [
            {"consultor": consultor, "hsAsig": 0, "monthly": [_round2(v) for v in monthly]}
            for consultor, monthly in groups.items()
        ]
