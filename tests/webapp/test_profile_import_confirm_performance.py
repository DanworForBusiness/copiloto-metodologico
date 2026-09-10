"""
test_profile_import_confirm_performance.py — Capital Humano Phase 3.8.1
§7/§8: mide el costo REAL de la orquestación síncrona de
`profile_import_service.confirm_rows` para separar:

  - nuestro costo Python/import (verificar JWT + rechequeo de duplicados
    + escritura atómica local por fila), de
  - el costo INEVITABLE de N × latencia remota (mockeada acá — NUNCA se
    llama Spaces real).

NO son aserciones de regresión frágiles (§7: "Do NOT turn timings into
brittle regression assertions"). El único `assert` de tiempo es un
umbral GENEROSÍSIMO para atrapar una regresión catastrófica (un bucle
O(n²), un deadlock) — nunca un SLA. Los números concretos observados en
una corrida local se reportan en el OUTPUT de la fase.

`DigitalOcean Spaces production PUT latency = UNKNOWN` (§9) — la latencia
sintética de acá NO se extrapola a un SLA de producción."""
from __future__ import annotations

import io
import time

import openpyxl
import pytest

import config
from webapp.services import profile_repository, profile_import_service as pis

_ALL_HEADERS = ["nombre", "apellido", "email", "telefono", "linkedin", "ubicacion",
                "perfil_skills", "seniority", "fuente", "tags", "notas", "potencial", "marca_potencial"]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    # El harness MIDE hasta 500 filas para caracterizar la curva (§7) —
    # eso está por encima del límite operacional del primer slice (200),
    # así que se sube el tope acá; nunca es una aserción sobre el valor
    # de producción.
    monkeypatch.setattr(pis, "MAX_IMPORT_ROWS", 10_000)


def _tokens_for(n: int) -> list[str]:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_ALL_HEADERS)
    for i in range(n):
        ws.append([f"Persona{i}", "Bench", f"p{i}@x.com", "", "", "", "", "", "", "", "", "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    preview = pis.build_preview(pis.parse_workbook(buf.getvalue()))
    return [row.token for row in preview]


@pytest.mark.parametrize("n", [10, 50, 100, 250, 500])
@pytest.mark.parametrize("latency_ms", [0.0, 2.0, 10.0])
def test_confirm_orchestration_cost_matrix(n, latency_ms, monkeypatch, capsys):
    tokens = _tokens_for(n)

    def _push_with_latency(path):
        if latency_ms:
            time.sleep(latency_ms / 1000.0)
        return True

    monkeypatch.setattr(profile_repository.remote_store, "push_path", _push_with_latency)

    start = time.perf_counter()
    results = pis.confirm_rows(tokens, tokens)
    total_s = time.perf_counter() - start

    assert all(r.status == "CREATED" for r in results)
    simulated_remote_s = n * (latency_ms / 1000.0)  # 1 push por fila en el camino feliz
    orchestration_s = max(0.0, total_s - simulated_remote_s)

    with capsys.disabled():
        print(
            f"\n[3.8.1 confirm perf] N={n:>3}  push_latency={latency_ms:>4.0f}ms  "
            f"total={total_s * 1000:>8.1f}ms  simulated_remote={simulated_remote_s * 1000:>8.1f}ms  "
            f"orchestration={orchestration_s * 1000:>8.1f}ms  ({orchestration_s / n * 1000:>5.2f}ms/row)"
        )

    # Umbral catastrófico, NO un SLA: incluso 500 filas con 10ms de
    # latencia sintética por push son ~5s de latencia inevitable + el
    # overhead local -- 60s deja margen enorme para cualquier máquina de
    # CI y solo dispara ante un bug estructural.
    assert total_s < 60.0
