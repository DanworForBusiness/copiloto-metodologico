"""
admin_tools.py
==============
Capa fina para las "Herramientas de gestión" (Analítica interna) del panel
diagnóstico. Orquesta in-process — sin subprocess y sin BD — las utilidades que
en el Streamlit original vivían en ui/tools_panel.py.

Dos familias:

1. Export runtime — empaqueta los directorios efímeros (feedback_log, activity_log,
   case_library) en un ZIP descargable antes de un redeploy/reinicio en DO. Copia
   fiel de las funciones de legacy:ui/tools_panel.py.

2. Análisis / propuestas — reusa los scripts CLI de la raíz (analyze_cases.py,
   analyze_feedback.py, propose_tree_updates.py, propose_content_updates.py)
   ejecutando su main() in-process y capturando su salida (el mismo texto que el
   legacy mostraba al correrlos por subprocess). No se duplica lógica ni se tocan
   los scripts; los flags se pasan por argv.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from config import ACTIVITY_LOG_DIR, CASE_LIBRARY_DIR, FEEDBACK_LOG_DIR
from core import remote_store


# =========================================================
# 1. Export runtime  (copia fiel de legacy:ui/tools_panel.py)
# =========================================================

_RUNTIME_EXPORT_DIRS = (
    ("feedback_log", FEEDBACK_LOG_DIR),
    ("activity_log", ACTIVITY_LOG_DIR),
    ("case_library", CASE_LIBRARY_DIR),
)


def _format_bytes(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{num_bytes} B"


def _iter_runtime_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def runtime_inventory() -> tuple[list[dict], int, int, float | None]:
    """Inventario por carpeta: filas + totales + mtime más reciente."""
    rows: list[dict] = []
    total_files = 0
    total_size = 0
    latest_mtime: float | None = None

    for label, root in _RUNTIME_EXPORT_DIRS:
        files = _iter_runtime_files(root)
        size = sum(p.stat().st_size for p in files)
        mtimes = [p.stat().st_mtime for p in files]
        latest = max(mtimes) if mtimes else None
        rows.append({
            "carpeta": label,
            "path": str(root),
            "archivos": len(files),
            "tamano": _format_bytes(size),
            "ultimo_cambio": (
                datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M:%S")
                if latest else "-"
            ),
        })
        total_files += len(files)
        total_size += size
        if latest is not None:
            latest_mtime = latest if latest_mtime is None else max(latest_mtime, latest)

    return rows, total_files, total_size, latest_mtime


def runtime_export_zip() -> bytes:
    """ZIP en memoria con manifest.json + los archivos de cada carpeta runtime."""
    rows, total_files, total_size, latest_mtime = runtime_inventory()
    generated_at = datetime.now().isoformat(timespec="seconds")
    manifest = {
        "generated_at": generated_at,
        "total_files": total_files,
        "total_size_bytes": total_size,
        "latest_mtime": latest_mtime,
        "folders": rows,
        "note": "Export manual de archivos runtime locales/efimeros antes de redeploy o reinicio.",
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for label, root in _RUNTIME_EXPORT_DIRS:
            for path in _iter_runtime_files(root):
                zf.write(path, arcname=str(Path(label) / path.relative_to(root)))
    return buffer.getvalue()


def runtime_export_filename() -> str:
    return f"danwor-runtime-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"


# ── Borrado selectivo por rango de fechas ─────────────────────────────────
# Los tres directorios usan timestamps en el nombre (feedback_YYYYMMDD_HHMMSS_*,
# case_YYYYMMDD_*, activity_YYYYMMDD.jsonl); el rango se resuelve contra esa
# fecha (fallback: mtime del archivo). Flujo seguro: preview (dry-run) → delete.

_PURGE_DATE_RE = re.compile(r"(\d{8})")
_PURGE_LIST_LIMIT = 25


def _file_date(path: Path) -> str:
    match = _PURGE_DATE_RE.search(path.name)
    if match:
        return match.group(1)
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d")


def runtime_purge(folders: list[str], date_from: str, date_to: str, dry_run: bool = True) -> str:
    """Elimina (o previsualiza) archivos runtime cuyo timestamp cae en el rango.

    `date_from`/`date_to` en formato YYYY-MM-DD (inputs date), inclusivos;
    vacíos = sin límite en ese extremo. Devuelve un reporte en texto plano."""
    valid = {label for label, _ in _RUNTIME_EXPORT_DIRS}
    chosen = [folder for folder in folders if folder in valid]
    if not chosen:
        return "Elegí al menos una carpeta (feedback_log, activity_log o case_library)."

    low = (date_from or "").replace("-", "").strip() or "00000000"
    high = (date_to or "").replace("-", "").strip() or "99999999"
    if not (low.isdigit() and high.isdigit() and len(low) == 8 and len(high) == 8):
        return "Fechas inválidas: usá el formato YYYY-MM-DD."
    if low > high:
        return "El rango está invertido: 'Desde' es posterior a 'Hasta'."

    lines: list[str] = []
    total_files = 0
    total_size = 0
    remote_deleted = 0
    for label, root in _RUNTIME_EXPORT_DIRS:
        if label not in chosen:
            continue
        hits = [p for p in _iter_runtime_files(root) if low <= _file_date(p) <= high]
        size = sum(p.stat().st_size for p in hits)
        lines.append(f"\n{label}: {len(hits)} archivo(s) · {_format_bytes(size)}")
        for path in hits[:_PURGE_LIST_LIMIT]:
            lines.append(f"  - {path.name}")
        if len(hits) > _PURGE_LIST_LIMIT:
            lines.append(f"  … y {len(hits) - _PURGE_LIST_LIMIT} más")
        if not dry_run:
            for path in hits:
                try:
                    path.unlink()
                except OSError:
                    lines.append(f"  [ERROR] no se pudo eliminar {path.name}")
                    continue
                # Reflejar el borrado en el bucket para que la limpieza sea
                # consistente (si no, restore() los repondría en el próximo deploy).
                if remote_store.delete_path(path):
                    remote_deleted += 1
        total_files += len(hits)
        total_size += size

    span = f"{date_from or 'inicio'} → {date_to or 'hoy'}"
    if dry_run:
        header = (f"PREVISUALIZACIÓN — {span}\n"
                  f"Se eliminarían {total_files} archivo(s) ({_format_bytes(total_size)}). "
                  "Nada fue borrado todavía.")
        footer = "\n\nSi está OK, ejecutá 'Eliminar' (conviene bajar el Export ZIP antes)."
    else:
        remote_note = f" · {remote_deleted} en el bucket" if remote_store.enabled() else ""
        header = (f"BORRADO EJECUTADO — {span}\n"
                  f"Eliminados {total_files} archivo(s) ({_format_bytes(total_size)}){remote_note}.")
        footer = ""
    return header + "".join(f"\n{line}" if not line.startswith("\n") else line for line in lines) + footer


# =========================================================
# 2. Análisis / propuestas  (reusan los scripts CLI in-process)
# =========================================================

# Los scripts parsean sys.argv y escriben por stdout. Parcheamos argv y
# capturamos stdout para correr su main() exacta sin tocarlos ni duplicar lógica.
# Un lock evita pisar sys.argv entre requests concurrentes (uso admin, baja
# concurrencia, pero el lock lo deja determinístico).
_RUN_LOCK = threading.Lock()


def _capture_main(module, argv: list[str]) -> str:
    buffer = io.StringIO()
    with _RUN_LOCK:
        original_argv = sys.argv
        sys.argv = [getattr(module, "__name__", "script"), *argv]
        try:
            with contextlib.redirect_stdout(buffer):
                try:
                    module.main()
                except SystemExit:
                    # Los scripts hacen sys.exit(0) cuando no hay datos.
                    pass
                except Exception as exc:  # noqa: BLE001 — se reporta al panel
                    buffer.write(f"\n[ERROR] {type(exc).__name__}: {exc}\n")
        finally:
            sys.argv = original_argv
    return buffer.getvalue().strip() or "(sin salida)"


def run_cases_analysis(use_llm: bool = False) -> str:
    import analyze_cases
    return _capture_main(analyze_cases, ["--llm"] if use_llm else [])


def run_reconciliation_analysis() -> str:
    """Bucle diagnóstico ⇄ automatización (epic B 1.2): reconcilia el veredicto auto
    del catálogo con el criterio del árbol de extracción + lecciones de campo + el
    override del senior. Determinístico (sin LLM). Escribe reports/reconciliation*."""
    import analyze_reconciliation
    return _capture_main(analyze_reconciliation, [])


def run_feedback_analysis(kind: str = "all") -> str:
    import analyze_feedback
    if kind not in {"ia", "modulos", "secciones", "academy", "all"}:
        kind = "all"
    return _capture_main(analyze_feedback, ["--type", kind])


def run_tree_proposals(apply: bool = False) -> str:
    import propose_tree_updates
    return _capture_main(propose_tree_updates, ["--apply"] if apply else [])


def run_content_proposals(use_llm: bool = False) -> str:
    import propose_content_updates
    return _capture_main(propose_content_updates, ["--llm"] if use_llm else [])


# Regeneración self-service: un lock NO bloqueante evita que dos colaboradores
# regeneren a la vez (se pisarían sobre catalog_local). Si está tomado, avisamos en
# vez de encolar o correr en paralelo.
_CATALOG_REGEN_LOCK = threading.Lock()


def _reconciliation_line(out_dir: str) -> str:
    """Corre la reconciliación sobre el catálogo recién generado y devuelve una línea
    de estado para el output de la regeneración. Determinístico; nunca bloquea."""
    try:
        import os as _os
        import analyze_reconciliation as recon
        pack = _os.getenv("DANWOR_CONTENT_PACK", "").strip() or "datasphere_core"
        report, _ = recon.run(pack, Path(out_dir))
        if not report.get("flows"):
            return "[reconciliación] sin flujos para reconciliar."
        divs = report.get("senior_divergences") or []
        if divs:
            det = "; ".join(f"{d['flow']} (auto={d['auto']} vs senior={d['senior']})" for d in divs)
            return (f"[reconciliación] ATENCIÓN: {len(divs)} divergencia(s) auto↔senior a "
                    f"resolver → {det}")
        return (f"[reconciliación] OK: criterio espejado — 0 divergencias auto↔senior en "
                f"{report['flows']} flujo(s).")
    except Exception as exc:  # noqa: BLE001 — la reconciliación nunca debe romper la regeneración
        return f"[reconciliación] no ejecutada: {type(exc).__name__}: {exc}"


def _portability_line(out_dir: str) -> str:
    """Agrega la telemetría de Portabilidad BDC del catálogo recién generado y devuelve
    una línea de estado. Determinístico; nunca bloquea la regeneración."""
    try:
        import sys as _sys
        from config import BASE_DIR as _BD
        _tc = str(_BD / "tools" / "catalog")
        if _tc not in _sys.path:
            _sys.path.insert(0, _tc)
        import portability_telemetry as tele
        _report, line = tele.run(Path(out_dir))
        return line
    except Exception as exc:  # noqa: BLE001
        return f"[portabilidad BDC] no ejecutada: {type(exc).__name__}: {exc}"


def run_catalog_generation_locked(graph_token: str = "") -> tuple[bool, str]:
    """Como run_catalog_generation pero serializado. Devuelve (corrió, mensaje):
    corrió=False si ya había una regeneración en curso (no corre ni espera)."""
    if not _CATALOG_REGEN_LOCK.acquire(blocking=False):
        return False, "Ya hay una regeneración en curso. Esperá unos segundos y volvé a intentar."
    try:
        return True, run_catalog_generation(graph_token=graph_token)
    finally:
        _CATALOG_REGEN_LOCK.release()


def run_catalog_generation(graph_token: str = "") -> str:
    """PILOTO (no productivo): regenera el catálogo CDS desde los JSON crudos.
    Dispara el botón del panel diagnóstico. Fuente de los JSON crudos:
    - Si hay token de sesión + SHAREPOINT_CATALOG_CSN_URL → lee la carpeta CSN
      de SharePoint (Graph, token delegado) a un temp y genera desde ahí.
    - Si no → carpeta local catalog_inbound (stand-in de desarrollo).
    Escribe en catalog_local (DANWOR_CATALOG_DIR) y preserva el bloque `manual:`."""
    import os
    import shutil
    import tempfile
    from config import (
        BASE_DIR,
        SHAREPOINT_CATALOG_CSN_URL,
        SHAREPOINT_SECURITY_ROLE_MATRIX_CLIENT,
        SHAREPOINT_SECURITY_ROLE_MATRIX_URL,
    )
    out = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
    csn_url = (SHAREPOINT_CATALOG_CSN_URL or "").strip()
    svc_dir = str(BASE_DIR / "tools" / "catalog")
    if svc_dir not in sys.path:
        sys.path.insert(0, svc_dir)
    try:
        import importlib
        import generate_catalog as gen
        import generate_space_catalog as space_gen
        import generate_security_catalog as security_gen
        import generate_data_products_catalog as data_products_gen
        gen = importlib.reload(gen)
        space_gen = importlib.reload(space_gen)
        security_gen = importlib.reload(security_gen)
        data_products_gen = importlib.reload(data_products_gen)

        def _run_generators(src_dir: str) -> str:
            parts = [gen.generate_all(src_dir, out)]
            try:
                parts.append(space_gen.generate_all(src_dir, out))
            except Exception as space_exc:  # noqa: BLE001 — no debe bloquear los demás catálogos
                parts.append(f"[WARN] Catálogo de espacios/conexiones no generado: {type(space_exc).__name__}: {space_exc}")
            try:
                parts.append(security_gen.generate_all(src_dir, out))
            except Exception as security_exc:  # noqa: BLE001 — no debe bloquear los demás catálogos
                parts.append(f"[WARN] Catálogo de seguridad/DAC no generado: {type(security_exc).__name__}: {security_exc}")
            try:
                parts.append(data_products_gen.generate_all(src_dir, out))
            except Exception as dp_exc:  # noqa: BLE001 — no debe bloquear los demás catálogos
                parts.append(f"[WARN] Catálogo de Data Products no generado: {type(dp_exc).__name__}: {dp_exc}")
            # Reconciliación diagnóstico ⇄ automatización AUTOMÁTICA tras regenerar: el
            # criterio se re-espeja solo (ya no es un botón manual). Marca fuerte si hay
            # divergencia auto↔senior. Nunca bloquea la regeneración.
            parts.append(_reconciliation_line(out))
            parts.append(_portability_line(out))
            return "\n".join(parts)

        if graph_token and csn_url:
            # Caché PERSISTENTE de crudos (no un temp efímero): así la descarga es
            # incremental (solo baja lo que cambió por cTag/eTag). Vive dentro de
            # catalog_local (runtime, no se commitea).
            cache = os.getenv("DANWOR_CATALOG_CSN_CACHE", "").strip() or str(Path(out) / ".csn_cache")
            Path(cache).mkdir(parents=True, exist_ok=True)
            messages = [gen.fetch_csn_folder(csn_url, graph_token, cache)]
            role_matrix_url = (SHAREPOINT_SECURITY_ROLE_MATRIX_URL or "").strip()
            role_matrix_client = (SHAREPOINT_SECURITY_ROLE_MATRIX_CLIENT or "Danone").strip() or "Danone"
            if role_matrix_url:
                role_dest = os.path.join(cache, role_matrix_client, "Seguridad")
                messages.append(gen.fetch_shared_file(
                    role_matrix_url,
                    graph_token,
                    role_dest,
                    fallback_name="DSP_Matriz_Seguridad_Template.xlsx",
                    cache_root=cache,
                ))
            messages.append(_run_generators(cache))
            return "\n".join(messages)
        inbound = os.getenv("DANWOR_CATALOG_INBOUND_DIR", "").strip() or str(BASE_DIR / "catalog_inbound")
        return _run_generators(inbound)
    except Exception as exc:  # noqa: BLE001 — se reporta al panel
        return f"[ERROR] {type(exc).__name__}: {exc}\n" + traceback.format_exc(limit=8)


# =========================================================
# 3. SharePoint — seed semántico de casos SAP (Fase 2)
# =========================================================

# El seed generado se retiene en memoria por dueño para ofrecer la descarga
# como un GET separado (el JSON no viaja en el HTML del preview).
_LAST_SEED: dict[str, str] = {}


def sharepoint_refresh_cache(graph_token: str) -> str:
    """Limpia el cache local y vuelve a leer los casos desde Microsoft Graph."""
    if not graph_token:
        return ("No hay token de Microsoft Graph en la sesión. Cerrá sesión y "
                "volvé a ingresar para habilitar el acceso a SharePoint.")
    from core import sharepoint_cases as sp
    cases = sp.fetch_sap_cases(graph_token, force_refresh=True)
    if not cases:
        return ("No se obtuvieron casos: el token pudo expirar, falta el permiso "
                "Sites.Read.All, o la lista está vacía. Reingresá e intentá de nuevo.")
    return f"Cache refrescado. {len(cases)} caso(s) leído(s) desde SharePoint."


def run_sharepoint_seed(graph_token: str, owner_key: str) -> tuple[str, int]:
    """Genera el seed semántico. Devuelve (preview_o_mensaje, cantidad_de_casos)."""
    if not graph_token:
        return ("No hay token de Microsoft Graph en la sesión. Cerrá sesión y "
                "volvé a ingresar para habilitar el acceso a SharePoint.", 0)
    from core import sharepoint_cases as sp
    cases = sp.fetch_sap_cases(graph_token, force_refresh=False)
    if not cases:
        return ("No se obtuvieron casos: el token pudo expirar, falta el permiso "
                "Sites.Read.All, o la lista está vacía. Reingresá e intentá de nuevo.", 0)
    seed = sp.build_cases_semantic_seed(cases)
    payload = json.dumps(seed, ensure_ascii=False, indent=2)
    _LAST_SEED[owner_key or "anon"] = payload
    return payload, len(seed)


def last_seed(owner_key: str) -> str | None:
    return _LAST_SEED.get(owner_key or "anon")
