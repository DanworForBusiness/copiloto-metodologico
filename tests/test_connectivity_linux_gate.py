"""Gate 7 (F2.1) — launcher directo y aislamiento del perfil del CLI en LINUX, con el CLI REAL y SIN tenant.

Una sola fuente: este test ejecuta `tools.catalog.connectivity_governance.linux_gate` (el mismo módulo que correría un job de
despliegue). En Linux con node y el CLI local instalados exige que TODOS los chequeos G1–G7 pasen. Fuera de Linux el gate no
produce evidencia: se verifica que devuelva NO_EVIDENCE (exit 2) y nunca PASS. Un SKIP no es evidencia de G7.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from tools.catalog.connectivity_governance import linux_gate

REPO = Path(__file__).resolve().parents[1]
TERMINAL_JS = REPO / "node_modules" / "@sap" / "datasphere-cli" / "terminal.js"
ON_LINUX = sys.platform.startswith("linux")


@pytest.mark.skipif(not ON_LINUX or shutil.which("node") is None or not TERMINAL_JS.is_file(),
                    reason="gate 7 requiere Linux con node y el CLI local de Datasphere instalados (un SKIP no es evidencia)")
def test_linux_gate_all_checks_pass(tmp_path, monkeypatch):
    home = tmp_path / "operator_home"                         # el "perfil real" del usuario se redirige: ni un fallo toca el real
    (home / ".@sap" / "datasphere-cli" / ".cache").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(REPO)
    checks = linux_gate.run_checks(REPO)
    failed = [f"{c.name}: {c.detail}" for c in checks if not c.ok]
    names = {c.name for c in checks}
    assert not failed, failed
    assert {"G1.node", "G2.launcher_direct", "G3.workspace_no_persistido", "G4.homedir_aislado", "G5.secrets_json_solo_efimero",
            "G5.secreto_fuera_de_argv", "G6.proceso_terminado", "G6.presupuesto_aborta", "G7.workspace_borrado",
            "G7.perfil_real_intacto"} <= names


@pytest.mark.skipif(ON_LINUX, reason="verifica el comportamiento FUERA de Linux")
def test_gate_never_passes_outside_linux(capsys):
    assert linux_gate.main([]) == linux_gate.EXIT_NO_EVIDENCE
    assert "NO_EVIDENCE" in capsys.readouterr().out


def test_gate_is_not_a_hub_tool():
    src = Path(linux_gate.__file__).read_text(encoding="utf-8")
    assert "import webapp" not in src and "from webapp" not in src            # no carga la app ni sus sesiones
    assert "APIRouter" not in src and "@app." not in src                      # sin rutas
    import tools.catalog.connectivity_governance as pkg
    assert "linux_gate" not in (pkg.__all__ or [])                            # no forma parte de la API del paquete
    assert os.path.basename(linux_gate.__file__) == "linux_gate.py"
