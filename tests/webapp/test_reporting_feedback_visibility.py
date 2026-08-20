"""
test_reporting_feedback_visibility.py — FEEDBACK VISIBILITY corrective
(owner decision 2026-08-19 — owner live smoke: "¿Qué te pareció este
reporte? 👍 👎" quedaba visible mientras el contenido del reporte todavía
estaba cargando, permitiendo evaluar algo que no había terminado de
resolver).

Root cause (audit, ver HANDOFF): la visibilidad del control de feedback
estaba gateada por `state.reportingTabHydrated[expId]` — el flag de UNA
SOLA VÍA que decide "¿este main tab se hidrató alguna vez?" (deliberado y
protegido para filtros/contenido — nunca colapsan al loader inicial al
cambiar de perspectiva). Feedback necesita una pregunta DISTINTA: "¿el
contenido que el usuario está viendo AHORA MISMO está listo?" — eso ya lo
representa `reportingTabRawStatus(expId)` (perspective-aware para Cartera/
Comercial), reusado tal cual vía el nuevo helper `isActiveReportReady`.

Este harness carga `reporting.js` real en un sandbox Node (mismo patrón
que reporting_cartera_lifecycle_active_perspective_only.node.js) y ejecuta
el ciclo de hidratación REAL vía `render()` con un `fetch` fake, probando
en runtime real:
  A. bootstrap Cartera/Mantenimiento loading -> feedback oculto;
  B. Mantenimiento ready -> feedback visible;
  C. switch a Proyecto (idle/loading) -> feedback oculto, aunque
     Mantenimiento siga ready en background/state;
  D. Proyecto ready -> feedback visible;
  E. switch a Rentabilidad (loading) -> feedback oculto, aunque Cartera
     siga ready en background;
  F. Rentabilidad ready -> feedback visible;
  G. revisitar un tab/perspectiva YA ready -> feedback visible de
     inmediato, sin flash;
  H. error de primera carga Y error incremental post-hidratación -> AMBOS
     ocultan feedback, mismo criterio (decisión de audit: nunca evaluar un
     reporte que falló);
  I. la acción 👍/👎 sigue invocando el MISMO endpoint/payload/estado
     interno que antes — esta corrective es SOLO de visibilidad.

No prueba wall-clock ni requiere sleeps.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_feedback_visibility.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_feedback_visibility_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Feedback visibility invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL FEEDBACK VISIBILITY INVARIANTS PASSED" in result.stdout
