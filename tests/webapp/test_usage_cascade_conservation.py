"""MÉTRICAS DE USO — CONSERVACIÓN DE LA CASCADA (corrective de confianza).

El defecto: el desglose por usuario del panel derecho se resolvía por NOMBRE DE
EVENTO solamente. Con el módulo "Asignaciones" seleccionado, la izquierda decía
175 y el desglose sumaba 978 — el universo GLOBAL de `module_viewed`, que existe
a propósito en Copiloto, Operaciones y Comercial. Además de no conservar,
insinuaba que usuarios sin autorización habían entrado a un módulo sensible.

EL ORÁCULO. `build_summary(..., module=M)` ya filtraba bien; el atajo
client-side lo puenteaba. Así que la equivalencia contra ese camino ya-correcto
es el guard central: no hay que confiar en que el cubo nuevo esté bien, se
compara contra la implementación que el owner ya validó.

LA FIXTURE. Con nombres de evento globalmente únicos, o con un solo módulo, el
bug es INDISTINGUIBLE: desglose global y module-scoped dan idéntico. Todas las
fixturas de acá tienen el MISMO evento en más de un módulo.
"""
from __future__ import annotations

import json
from collections import Counter

import pytest

from webapp.services import activity_analytics
from webapp.services import module_registry


def _escribir(tmp_path, dia: str, registros: list[dict]) -> None:
    destino = tmp_path / f"activity_{dia}.jsonl"
    with destino.open("a", encoding="utf-8") as fh:
        for r in registros:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _vista(ts, user, name, scope, module):
    return {"ts_utc": ts, "event": "module_viewed", "user": user, "name": name,
            "scope": scope, "area": "", "module": module, "payload": {}}


@pytest.fixture
def cascada(monkeypatch, tmp_path):
    """LA fixture que el owner pidió: mismo evento en dos módulos, usuarios que
    solapan sólo parcialmente."""
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    registros = []
    # Asignaciones (Operaciones): Ana 2, Juan 1.
    for i in range(2):
        registros.append(_vista(f"2026-03-01T10:0{i}:00+00:00", "ana@d.com", "Ana", "operations", "assignments"))
    registros.append(_vista("2026-03-01T10:05:00+00:00", "juan@d.com", "Juan", "operations", "assignments"))
    # Copiloto (CP01): Pedro 8 — NUNCA puede aparecer bajo Asignaciones.
    for i in range(8):
        registros.append(_vista(f"2026-03-01T11:{i:02d}:00+00:00", "pedro@d.com", "Pedro", "copilot", "CP01"))
    # Ana también estuvo en Copiloto: el solape parcial es lo que hace que el
    # test distinga "filtra por módulo" de "filtra por usuario conocido".
    registros.append(_vista("2026-03-01T11:30:00+00:00", "ana@d.com", "Ana", "copilot", "CP01"))
    _escribir(tmp_path, "20260301", registros)
    return activity_analytics.build_summary("", "")


def _canonico(raw: str) -> str:
    """`assignments` -> `OP01`. `top_modules` lleva el id CANONICO (lo resuelve
    `module_registry`), no el crudo del log."""
    resuelto = module_registry.resolve(raw)
    return resuelto.module_id if resuelto else raw


def _label_de(summary, raw: str) -> str:
    clave = _canonico(raw)
    return next(r["label"] for r in summary["top_modules"] if r["key"] == clave)


# ── EL CASO DEL OWNER ──────────────────────────────────────────────────────

def test_el_desglose_de_un_modulo_no_incluye_usuarios_de_otro(cascada):
    asign = _label_de(cascada, "assignments")
    bd = cascada["module_event_user_breakdowns"][asign]["module_viewed"]
    etiquetas = {r["label"] for r in bd["rows"]}
    assert etiquetas == {"Ana", "Juan"}
    assert "Pedro" not in etiquetas, "Pedro sólo estuvo en Copiloto"
    assert sum(r["count"] for r in bd["rows"]) == 3


def test_el_mismo_usuario_se_cuenta_en_cada_modulo_por_separado(cascada):
    """Ana estuvo en los dos. Su conteo bajo Asignaciones es 2, no 3: el
    desglose reporta el universo del MÓDULO, no la actividad total del usuario."""
    asign, copi = _label_de(cascada, "assignments"), _label_de(cascada, "CP01")
    por_modulo = {
        asign: cascada["module_event_user_breakdowns"][asign]["module_viewed"],
        copi: cascada["module_event_user_breakdowns"][copi]["module_viewed"],
    }
    conteo = {m: {r["label"]: r["count"] for r in bd["rows"]} for m, bd in por_modulo.items()}
    assert conteo[asign]["Ana"] == 2
    assert conteo[copi]["Ana"] == 1


def test_la_fixture_distingue_el_bug(cascada):
    """Sin esto el test podría pasar por casualidad: se comprueba que el
    desglose GLOBAL del mismo evento es DISTINTO del module-scoped. Si fueran
    iguales, el guard no probaría nada — que es exactamente lo que pasaba con
    las fixturas viejas, de un solo módulo."""
    asign = _label_de(cascada, "assignments")
    global_ = cascada["event_breakdowns"]["module_viewed"]
    scoped = cascada["module_event_user_breakdowns"][asign]["module_viewed"]
    assert global_["total"] == 12          # 3 + 9
    assert scoped["total"] == 3
    assert global_["total"] != scoped["total"]


# ── EL ORÁCULO SERVER-SIDE (owner §2) ──────────────────────────────────────

def test_equivale_al_filtro_server_side_para_todo_modulo_y_evento(cascada):
    """El guard central. Para TODO (M, E), el desglose module-scoped tiene que
    ser idéntico al que produce `build_summary(module=M)` — el camino que el
    owner ya validó como correcto."""
    for row in cascada["top_modules"]:
        oraculo = activity_analytics.build_summary("", "", module=row["key"])
        nuestro = cascada["module_event_user_breakdowns"].get(row["label"], {})
        for evento, bd_oraculo in oraculo["event_breakdowns"].items():
            if bd_oraculo["dim"] != "usuario":
                continue
            bd = nuestro.get(evento)
            assert bd is not None, f"falta el desglose de {row['label']}/{evento}"
            assert bd["rows"] == bd_oraculo["rows"], f"{row['label']}/{evento} diverge del oráculo"
            assert bd["total"] == bd_oraculo["total"]


# ── CONSERVACIÓN (owner §5) ────────────────────────────────────────────────

def test_la_suma_del_desglose_iguala_el_conteo_del_evento_en_ese_modulo(cascada):
    for label, eventos in cascada["module_event_user_breakdowns"].items():
        izquierda = {r["event"]: r["count"] for r in cascada["module_event_breakdowns"][label]}
        for evento, bd in eventos.items():
            assert sum(r["count"] for r in bd["rows"]) == izquierda[evento], \
                f"{label}/{evento} no conserva"
            assert bd["total"] == izquierda[evento]


def test_el_universo_hijo_esta_contenido_en_el_padre(cascada):
    for label, eventos in cascada["module_event_user_breakdowns"].items():
        for evento, bd in eventos.items():
            padre = cascada["event_breakdowns"][evento]
            assert bd["total"] <= padre["total"], f"{label}/{evento} excede su padre"
            hijos = Counter({r["label"]: r["count"] for r in bd["rows"]})
            padres = Counter({r["label"]: r["count"] for r in padre["rows"]})
            for usuario, n in hijos.items():
                assert padres[usuario] >= n, f"{usuario} cuenta más en {label} que en el total"


# ── TOP-N HONESTO (owner §6) ───────────────────────────────────────────────

def test_el_drilldown_es_completo_y_declara_su_universo(cascada):
    """`_top` recortaba a 10 sin decirlo: en vivo el desglose global mostraba
    978 de 984. Un drill-down que aparenta ser exhaustivo no puede omitir filas
    en silencio."""
    for eventos in cascada["module_event_user_breakdowns"].values():
        for bd in eventos.values():
            assert bd["truncated"] is False
            assert bd["shown"] == bd["usuarios"] == len(bd["rows"])
            assert sum(r["count"] for r in bd["rows"]) == bd["total"]
    for bd in cascada["event_breakdowns"].values():
        assert sum(r["count"] for r in bd["rows"]) == bd["total"], \
            "el desglose global tampoco puede omitir filas en silencio"


def test_los_leaderboards_de_la_vista_general_siguen_acotados(cascada):
    """Top usuarios / Top módulos SÍ son un "top" declarado — ahí el recorte es
    el contrato, no una omisión. No se los toca."""
    assert len(cascada["top_users"]) <= activity_analytics._TOP_N
    assert len(cascada["top_modules"]) <= activity_analytics._TOP_N


# ── SISTÉMICO (owner §9) ───────────────────────────────────────────────────

def test_conserva_en_operaciones_copiloto_comercial_y_un_modulo_de_bajo_volumen(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    registros = []
    plan = [
        ("operations", "assignments", [("Ana", 4), ("Juan", 2)]),
        ("copilot", "CP01", [("Pedro", 5), ("Ana", 1)]),
        ("commercial", "CM01", [("Sol", 7), ("Juan", 3)]),
        ("copilot", "CP05", [("Ana", 1)]),                      # bajo volumen
    ]
    t = 0
    for scope, module, usuarios in plan:
        for nombre, veces in usuarios:
            for _ in range(veces):
                t += 1
                registros.append(_vista(f"2026-03-02T{t // 60:02d}:{t % 60:02d}:00+00:00",
                                        f"{nombre.lower()}@d.com", nombre, scope, module))
    _escribir(tmp_path, "20260302", registros)
    s = activity_analytics.build_summary("", "")

    for scope, module, usuarios in plan:
        label = _label_de(s, module)
        esperado = {n: v for n, v in usuarios}
        bd = s["module_event_user_breakdowns"][label]["module_viewed"]
        assert {r["label"]: r["count"] for r in bd["rows"]} == esperado, label
        # module total -> evento -> desglose, conservando en cada paso.
        total_modulo = next(r["count"] for r in s["top_modules"] if r["key"] == _canonico(module))
        evento = {r["event"]: r["count"] for r in s["module_event_breakdowns"][label]}["module_viewed"]
        assert total_modulo == evento == bd["total"] == sum(esperado.values()), label


# ── IDENTIDAD DE USUARIO — SÓLO AUDITORÍA (owner §8) ───────────────────────

def test_un_mismo_user_con_dos_nombres_es_una_sola_fila(monkeypatch, tmp_path):
    """Este test FIJABA el defecto (agrupar por `name`) y lo registraba como
    corrective aparte. Ese corrective ya se hizo: ahora se agrupa por la
    identidad estable, así que el mismo escenario debe dar UNA fila."""
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    _escribir(tmp_path, "20260303", [
        _vista("2026-03-03T10:00:00+00:00", "ana@d.com", "Ana Perez", "operations", "assignments"),
        _vista("2026-03-03T10:01:00+00:00", "ana@d.com", "Ana P.", "operations", "assignments"),
    ])
    s = activity_analytics.build_summary("", "")
    label = _label_de(s, "assignments")
    bd = s["module_event_user_breakdowns"][label]["module_viewed"]
    assert [r["user"] for r in bd["rows"]] == ["ana@d.com"]
    assert bd["rows"][0]["label"] == "Ana P.", "la etiqueta es el último nombre"
    assert s["unique_users"] == 1
    # Y sigue conservando.
    assert bd["total"] == 2


# ── El harness de front, dentro de la suite ────────────────────────────────

def test_frontend_cascade_invariants():
    """La otra mitad del contrato: el backend puede emitir el cubo correcto y
    el front seguir mostrando el panel equivocado. Ejecuta `app.js` REAL."""
    import shutil
    import subprocess
    from pathlib import Path

    if shutil.which("node") is None:
        pytest.skip("node no disponible en este entorno")
    harness = Path(__file__).resolve().parent / "usage_cascade_integrity.node.js"
    r = subprocess.run(["node", str(harness)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"Usage cascade invariants failed:\n{r.stdout}\n{r.stderr}"
    assert "ALL USAGE CASCADE INVARIANTS PASSED" in r.stdout


# ══ IDENTIDAD ESTABLE (owner corrective, trust) ═══════════════════════════
# El log guarda `user` (sub/email, ESTABLE) y `name` (display, MUTABLE). Agrupar
# por `name or user` rompia en las DOS direcciones, y en los datos reales pasaban
# las dos: una persona aparecia como 3 filas ("Jose O'Connor" / "Jose OConnor" /
# "Jose") y un mismo nombre de display aparecia en 4 cuentas distintas ("Smoke").
# Inflaba unas filas y fusionaba otras.

@pytest.fixture
def identidades(monkeypatch, tmp_path):
    """LA fixture que el owner pidio, y es la unica forma de ver el bug: un
    `user` con tres nombres, y otro `user` DISTINTO con un nombre que repite."""
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    registros, t = [], 0

    def agregar(user, name, veces):
        nonlocal t
        for _ in range(veces):
            t += 1
            registros.append(_vista(f"2026-04-01T{t // 60:02d}:{t % 60:02d}:00+00:00",
                                    user, name, "operations", "assignments"))

    agregar("user_id_1", "Jose O'Connor", 2)
    agregar("user_id_1", "Jose OConnor", 3)
    agregar("user_id_1", "Jose", 1)          # el ULTIMO en el tiempo -> la etiqueta
    agregar("user_id_2", "Jose O'Connor", 4)  # OTRA cuenta, mismo nombre visible
    _escribir(tmp_path, "20260401", registros)
    return activity_analytics.build_summary("", "")


def test_un_usuario_con_tres_nombres_es_UN_usuario(identidades):
    bd = identidades["module_event_user_breakdowns"][_label_de(identidades, "assignments")]["module_viewed"]
    por_user = {r["user"]: r["count"] for r in bd["rows"]}
    assert por_user == {"user_id_1": 6, "user_id_2": 4}
    assert len(bd["rows"]) == 2, "cuatro etiquetas, DOS personas"


def test_dos_cuentas_con_el_mismo_nombre_NO_se_fusionan(identidades):
    """La otra direccion del defecto, y la que un 'normalizador de texto'
    empeoraria: el nombre visible coincide, la identidad no."""
    bd = identidades["module_event_user_breakdowns"][_label_de(identidades, "assignments")]["module_viewed"]
    users = [r["user"] for r in bd["rows"]]
    assert len(users) == len(set(users)) == 2
    etiquetas = [r["label"] for r in bd["rows"]]
    assert "Jose O'Connor" in etiquetas, "user_id_2 conserva su nombre visible"


def test_la_etiqueta_es_el_ultimo_nombre_de_esa_identidad(identidades):
    bd = identidades["module_event_user_breakdowns"][_label_de(identidades, "assignments")]["module_viewed"]
    etiqueta = {r["user"]: r["label"] for r in bd["rows"]}
    assert etiqueta["user_id_1"] == "Jose", "el ultimo `name` no vacio de esa identidad"
    assert etiqueta["user_id_2"] == "Jose O'Connor"


def test_el_conteo_de_usuarios_distintos_cuenta_personas(identidades):
    assert identidades["unique_users"] == 2, "cuatro etiquetas, dos cuentas"
    bd = identidades["module_event_user_breakdowns"][_label_de(identidades, "assignments")]["module_viewed"]
    assert bd["usuarios"] == 2


def test_la_cascada_sigue_conservando_con_identidad_estable(identidades):
    """El corrective anterior no se puede haber roto: SUM(usuarios) tiene que
    seguir siendo el conteo del evento en ese modulo."""
    label = _label_de(identidades, "assignments")
    izquierda = {r["event"]: r["count"] for r in identidades["module_event_breakdowns"][label]}
    bd = identidades["module_event_user_breakdowns"][label]["module_viewed"]
    assert sum(r["count"] for r in bd["rows"]) == izquierda["module_viewed"] == 10
    assert bd["total"] == 10


def test_la_identidad_estable_viaja_en_el_payload(identidades):
    """Owner: para poder auditar despues hay que poder distinguir dos cuentas
    con el mismo nombre, aunque la UI muestre la etiqueta amable."""
    for bd in [identidades["module_event_user_breakdowns"][_label_de(identidades, "assignments")]["module_viewed"],
               identidades["event_breakdowns"]["module_viewed"]]:
        for r in bd["rows"]:
            assert r.get("user"), "cada fila de usuario declara su identidad estable"
    for r in identidades["top_users"]:
        assert r.get("user")


def test_el_selector_de_usuario_usa_la_misma_etiqueta(identidades):
    """El `<select>` se quedaba con el PRIMER nombre visto, asi que podia llamar
    a alguien distinto que el desglose. Una sola regla de display."""
    facetas = {f["value"]: f["label"] for f in identidades["available_users"]}
    assert facetas == {"user_id_1": "Jose", "user_id_2": "Jose O'Connor"}


def test_no_se_normaliza_texto_para_decidir_identidad(monkeypatch, tmp_path):
    """Dos cuentas cuyos nombres solo difieren en puntuacion/mayusculas siguen
    siendo DOS. La identidad viene de `user`, no de parecerse."""
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    _escribir(tmp_path, "20260402", [
        _vista("2026-04-02T10:00:00+00:00", "a@d.com", "Jose O'Connor", "operations", "assignments"),
        _vista("2026-04-02T10:01:00+00:00", "b@d.com", "jose oconnor", "operations", "assignments"),
    ])
    s = activity_analytics.build_summary("", "")
    bd = s["module_event_user_breakdowns"][_label_de(s, "assignments")]["module_viewed"]
    assert {r["user"] for r in bd["rows"]} == {"a@d.com", "b@d.com"}
    assert s["unique_users"] == 2


def test_sin_ningun_name_se_muestra_la_identidad(monkeypatch, tmp_path):
    """Nunca se inventa un nombre: sin `name`, la etiqueta es el `user`."""
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    _escribir(tmp_path, "20260403", [
        _vista("2026-04-03T10:00:00+00:00", "anon@d.com", "", "operations", "assignments"),
    ])
    s = activity_analytics.build_summary("", "")
    bd = s["module_event_user_breakdowns"][_label_de(s, "assignments")]["module_viewed"]
    assert bd["rows"][0]["label"] == "anon@d.com"
    assert bd["rows"][0]["user"] == "anon@d.com"


def test_top_usuarios_tambien_agrupa_por_identidad_estable(identidades):
    """Hueco que encontro el mutation test: los guards cubrian el cubo de la
    cascada y el desglose global, pero NO el leaderboard. Agrupar ahi por
    nombre volveria a partir a una persona en varias filas — el mismo defecto,
    en el panel mas visible del dashboard."""
    filas = {r["user"]: r["count"] for r in identidades["top_users"]}
    assert filas == {"user_id_1": 6, "user_id_2": 4}
    etiquetas = {r["user"]: r["label"] for r in identidades["top_users"]}
    assert etiquetas["user_id_1"] == "Jose"
    assert etiquetas["user_id_2"] == "Jose O'Connor"
    # Y la suma del leaderboard sigue siendo el total de eventos de uso.
    assert sum(r["count"] for r in identidades["top_users"]) == identidades["total_events"]
