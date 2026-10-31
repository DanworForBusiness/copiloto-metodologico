"""Gate LINUX offline de la extracción de conexiones (F2.1 · G7). SOLO stdlib + el código F2.1; sin pytest.

Valida, con el CLI REAL instalado en la imagen y un "tenant" FALSO en 127.0.0.1 (nunca SAP, sin credenciales reales, sin red
externa), las garantías técnicas del adaptador en Linux:

  G1 versiones   node v22 y @sap/datasphere-cli / @sap/cli-core en node_modules == package-lock.json
  G2 launcher    resolve_datasphere_cli_launcher ⇒ 'direct' (no npx)
  G3 workspace   el temporal del gate cae FUERA de los directorios persistidos y del Catálogo
  G4 HOME        isolated_cli_env + Node real ⇒ os.homedir() dentro del perfil efímero
  G5 secretos    CliConnectionSource.preflight_report(): login por --secrets-file; secrets.json SOLO en el perfil efímero; el secreto
                 sintético no aparece en argv, salida ni resultado; el archivo de secretos efímero se borra
  G6 timeout     un subprocess colgado se corta y el proceso node muere de verdad (/proc); el corte por presupuesto es BudgetError
  G7 limpieza    workspace borrado y verificado; perfil real del usuario con huella idéntica; sin procesos node del gate

No es una herramienta del Hub: no expone rutas, no importa `webapp`, no escribe fuera de su temporal y solo imprime PASS/FAIL.
Uso (desde la raíz de la app):  python -m tools.catalog.connectivity_governance.linux_gate
Salida: 0 = PASS (todo, en Linux) · 1 = FAIL (cualquier chequeo o error) · 2 = NO_EVIDENCE (fuera de Linux: nunca PASS).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SECRET = "G7-SYNTHETIC-NOT-A-REAL-SECRET-4711"
DEADLINE_S = 90
EXIT_PASS, EXIT_FAIL, EXIT_NO_EVIDENCE = 0, 1, 2
_PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


class _FakeTenant(BaseHTTPRequestHandler):
    token_posts = 0

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # token OAuth (preflight HTTP y login del CLI)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).token_posts += 1
        self._json(200, {"access_token": "FAKE-AT", "token_type": "bearer", "expires_in": 3600})

    def do_GET(self):
        self._json(404, {})

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _fingerprint(p: Path) -> tuple:
    if not p.exists():
        return ()
    return tuple(sorted((str(f.relative_to(p)), f.stat().st_size, f.stat().st_mtime_ns) for f in p.rglob("*") if f.is_file()))


def _pids_with(marker: str) -> list[int] | None:
    """PIDs cuyo cmdline contiene `marker` (Linux /proc). None si no se puede inspeccionar (fuera de Linux)."""
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    found = []
    for d in proc.iterdir():
        if d.name.isdigit():
            try:
                if marker in (d / "cmdline").read_bytes().decode(errors="replace"):
                    found.append(int(d.name))
            except OSError:
                pass
    return found


def run_checks(root: Path) -> list[Check]:
    """Ejecuta G1–G7. No lanza: cualquier error inesperado se reporta como un chequeo en FAIL."""
    from tools.catalog import DSP_CLI_Export_Lineage as D                       # perezoso: el paquete no carga el CLI al importarse
    from tools.catalog.connectivity_governance import (
        Budget, BudgetError, CheckStatus, CliConnectionSource, ephemeral_workdir, real_cli_profile_dir,
    )

    checks: list[Check] = []
    add = lambda name, ok, detail="": checks.append(Check(name, bool(ok), detail))  # noqa: E731
    for var in _PROXY_VARS:                                                   # solo loopback: ningún proxy del entorno interviene
        os.environ.pop(var, None)
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))["packages"]

    # G1 — versiones
    node = shutil.which("node")
    node_v = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30).stdout.strip() if node else ""
    add("G1.node", bool(node) and node_v.startswith("v22."), f"node {node_v or 'ausente'}")
    for pkg in ("@sap/datasphere-cli", "@sap/cli-core"):
        pj = root / "node_modules" / pkg / "package.json"
        got = json.loads(pj.read_text(encoding="utf-8"))["version"] if pj.is_file() else "ausente"
        want = lock.get(f"node_modules/{pkg}", {}).get("version", "?")
        add(f"G1.{pkg}", got == want, f"instalado {got} / lock {want}")

    # G2 — launcher
    try:
        mode = D.resolve_datasphere_cli_launcher(root).mode
    except Exception as exc:                                                  # noqa: BLE001
        mode = f"no resoluble ({type(exc).__name__})"
    add("G2.launcher_direct", mode == "direct", f"modo {mode}")
    if not node or mode != "direct":
        return checks                                                         # sin CLI directo no hay nada más que validar con seguridad

    real_profile = real_cli_profile_dir()
    before = _fingerprint(real_profile)
    workdir_seen: Path | None = None
    try:
        with ephemeral_workdir("g7-linux-gate-") as wd:
            workdir_seen = wd
            # G3 — workspace fuera de directorios persistidos (misma fuente de verdad que el servicio web)
            try:
                from core import remote_store
                forbidden = [Path(p) for _n, p in remote_store._persist_dirs()]
                forbidden.append(Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or (root / "catalog_local")))
                safe = not any(_within(wd, d) for d in forbidden)
                add("G3.workspace_no_persistido", safe, f"{len(forbidden)} directorios verificados")
            except Exception as exc:                                          # noqa: BLE001
                add("G3.workspace_no_persistido", False, f"no verificable ({type(exc).__name__})")
                return checks

            # G4 — HOME aislado con el código F2.1 y Node real
            profile = wd / "home-probe"
            out = subprocess.run([node, "-e", "console.log(require('os').homedir())"], env=D.isolated_cli_env(profile),
                                 capture_output=True, text=True, timeout=60)
            add("G4.homedir_aislado", Path(out.stdout.strip()) == profile, "os.homedir() dentro del perfil efímero"
                if Path(out.stdout.strip()) == profile else "homedir FUERA del perfil efímero")

            # G5 — login real por --secrets-file contra el tenant falso
            argvs: list[list[str]] = []
            real_run = D.subprocess.run

            def spy(cmd, **kw):
                argvs.append([str(c) for c in cmd])
                return real_run(cmd, **kw)
            srv = HTTPServer(("127.0.0.1", 0), _FakeTenant)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{srv.server_address[1]}"
            D.subprocess.run = spy
            try:
                budget = Budget(max_requests=20, max_duration_s=60, max_total_retries=2)
                budget.start()
                src = CliConnectionSource(host=url + "/", client_id="sb-g7-synthetic", client_secret=SECRET,
                                          token_url=url + "/oauth/token", probe_space="G7", timeout=45,
                                          work_dir=str(wd / "src"), budget=budget)
                report = src.preflight_report()
                written = list((wd / "src").rglob("secrets.json"))
                leftovers = list((wd / "src").rglob("login-secrets-*.json"))
                src.close()
            finally:
                D.subprocess.run = real_run
                srv.shutdown()
            add("G5.oauth_login_ok", report.status_of("oauth_token") == CheckStatus.OK, "login del CLI real contra el tenant falso")
            add("G5.secrets_json_solo_efimero", len(written) == 1, f"secrets.json en el perfil efímero: {len(written)}")
            add("G5.secreto_fuera_de_argv", bool(argvs) and not any(SECRET in a for cmd in argvs for a in cmd),
                f"{len(argvs)} comandos inspeccionados")
            add("G5.secreto_fuera_del_resultado", SECRET not in json.dumps(report.to_dict()))
            add("G5.archivo_de_secretos_borrado", not leftovers)
            add("G5.tenant_falso_contactado", _FakeTenant.token_posts >= 1, f"POST de token en loopback: {_FakeTenant.token_posts}")

            # G6 — timeout real + corte por presupuesto
            marker = "g7-gate-timeout-" + wd.name
            hang = D.DatasphereCliLauncher(command=(node, "-e", f"/*{marker}*/ setTimeout(()=>{{}}, 30000)"), mode="direct")
            cli = D.DatasphereCli(host="https://h.invalid", client_id="i", client_secret=SECRET, token_url="https://t.invalid",
                                  work_dir=wd / "hang", launcher=hang, isolated_profile=True)
            t0 = time.monotonic()
            try:
                cli._run(["spaces", "list"], timeout=1.0)
                add("G6.timeout_corta", False, "no expiró")
            except subprocess.TimeoutExpired as exc:
                add("G6.timeout_corta", time.monotonic() - t0 < 10 and SECRET not in str(exc), f"{time.monotonic() - t0:.1f}s")
            time.sleep(0.5)
            alive = _pids_with(marker)
            add("G6.proceso_terminado", alive == [], "sin procesos vivos" if alive == [] else
                ("no verificable fuera de Linux" if alive is None else f"vivos: {len(alive)}"))
            capped = CliConnectionSource(host="https://h.invalid", client_id="i", client_secret=SECRET, token_url="https://t.invalid",
                                         timeout=60, budget=Budget(max_requests=5, max_duration_s=1.0, max_total_retries=0),
                                         work_dir=str(wd / "capped"))
            capped._cli = cli
            capped.budget.start()
            try:
                capped.get_connection("G7", "X")
                add("G6.presupuesto_aborta", False, "no abortó")
            except BudgetError as exc:
                add("G6.presupuesto_aborta", exc.code == "DURATION_EXCEEDED", exc.code)
            capped.close()
    except Exception as exc:                                                  # noqa: BLE001 — nunca PASS ante un error
        add("G.error_inesperado", False, type(exc).__name__)
    # G7 — limpieza
    add("G7.workspace_borrado", workdir_seen is not None and not workdir_seen.exists())
    add("G7.perfil_real_intacto", _fingerprint(real_profile) == before, "huella de metadatos sin cambios")
    leftover = _pids_with("g7-gate-timeout-")
    add("G7.sin_procesos_del_gate", leftover == [], "ok" if leftover == [] else "no verificable fuera de Linux" if leftover is None
        else f"vivos: {len(leftover)}")
    return checks


def main(argv: list[str] | None = None) -> int:
    if not sys.platform.startswith("linux"):
        print("G7 NO_EVIDENCE: este gate solo produce evidencia en Linux (exit 2).", flush=True)
        return EXIT_NO_EVIDENCE
    import signal
    signal.signal(signal.SIGALRM, lambda *_: (print("G7 FAIL: superó el límite de tiempo del gate", flush=True), os._exit(EXIT_FAIL)))
    signal.alarm(DEADLINE_S)
    try:
        checks = run_checks(Path.cwd())
    except Exception as exc:                                                  # noqa: BLE001
        checks = [Check("G.error_inesperado", False, type(exc).__name__)]
    finally:
        signal.alarm(0)
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name}{(' — ' + c.detail) if c.detail else ''}", flush=True)
    failed = [c.name for c in checks if not c.ok]
    print(f"G7 RESULTADO: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)})", flush=True)
    return EXIT_PASS if not failed and checks else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
