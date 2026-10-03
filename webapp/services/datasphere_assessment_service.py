"""
datasphere_assessment_service.py — adaptador WEB de la exportación directa desde SAP Datasphere ("Assessment de tu modelo").

NO reimplementa nada: la extracción (preflight OAuth `client_credentials`, login del CLI, preflight de autorización sobre el Space,
inventario/linaje, escritura del pool CSN, persistencia en DigitalOcean Spaces vía `core.remote_store`) vive en
``tools/catalog/DSP_CLI_Export_Lineage.py::run_export``, la MISMA función que usa el CLI. Este módulo solo agrega lo que una
superficie web necesita y el CLI no:

  * validación de entradas (formato + allowlist de hosts: el servidor hará un POST con credenciales al token URL, así que no
    puede apuntar a un destino arbitrario);
  * single-flight por (cliente, space, modelo);
  * normalización de errores a estados estables en español (sin texto crudo del CLI en la pantalla);
  * saneamiento de la traza (el secreto, tokens y rutas internas nunca salen de acá);
  * un view-model no sensible del resultado.

Reglas de secretos: el ``client_secret`` entra por argumento, vive solo durante la llamada, jamás se guarda (sesión, caché,
JSON, activity log) ni se devuelve en ninguna respuesta. Las credenciales de DigitalOcean NO se piden: ``mirror_up`` usa la
configuración de almacenamiento que el Hub ya tiene.

Alcance de la V1 (lo que realmente automatiza): extracción → JSON en el pool CSN local → (opcional) sincronización a Spaces.
NO regenera el catálogo ni corre el motor de assessment: eso sigue siendo "Actualizar catálogo" + el informe de assessment.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import config

log = logging.getLogger("danwor.datasphere_assessment")

# Módulos donde la herramienta está disponible (se introduce primero en 04 · Modelado & Desarrollo).
ASSESSMENT_MODULES = frozenset({"04_Modelado_y_Desarrollo"})

# Hosts permitidos para el tenant y el token URL (sufijos de dominio). Extensible por entorno (hosts EXACTOS) solo para dev/tests.
ALLOWED_HOST_SUFFIXES = (".cloud.sap", ".hana.ondemand.com")
ALLOWED_HOSTS_ENV = "DANWOR_DATASPHERE_ALLOWED_HOSTS"

_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
_CLIENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,59}$")

# Mensajes estables (español) por código de error. El texto crudo del CLI NO se muestra: va saneado al detalle técnico.
ERROR_MESSAGES = {
    "INVALID_PARAMETER": "Revisá los datos ingresados.",
    "AUTHENTICATION_FAILED": "No fue posible autenticar el usuario técnico de Datasphere. Revisá las credenciales OAuth (Client ID, Client Secret y Token URL) y que el OAuth Client siga vigente.",
    "OAUTH_ENDPOINT_UNREACHABLE": "No se pudo contactar el endpoint OAuth de Datasphere. Revisá el Token URL y la conectividad con el tenant.",
    "INSUFFICIENT_DATASPHERE_PRIVILEGES": "Las credenciales son válidas, pero el usuario técnico no tiene permisos suficientes sobre el Space. Revisá los scoped roles asignados.",
    "SPACE_ACCESS_FAILED": "No se pudo validar el acceso al Space indicado. Revisá el nombre del Space y los permisos del usuario técnico.",
    "DATASPHERE_CLI_UNAVAILABLE": "El Datasphere CLI no está disponible en este servidor.",
    "EXTRACTION_FAILED": "La extracción desde Datasphere falló. Revisá el nombre del Analytic Model y el detalle técnico.",
    "CATALOG_GENERATION_FAILED": "La extracción terminó, pero falló la regeneración del catálogo.",
    "STORAGE_UPLOAD_FAILED": "La extracción terminó, pero falló la sincronización con el almacenamiento (Spaces).",
    "OPERATION_ALREADY_RUNNING": "Ya hay una extracción en curso para este cliente, Space y modelo. Esperá a que termine.",
    "NOT_AUTHORIZED": "No tenés permiso para ejecutar esta operación.",
}

FIELD_LABELS = {
    "host": "URL del tenant de Datasphere", "token_url": "Token URL (OAuth)", "client_id": "Client ID", "client_secret": "Client Secret",
    "space": "Space", "analytic_model": "Analytic Model", "client": "Cliente del catálogo",
}
NON_SECRET_FIELDS = ("host", "token_url", "client_id", "space", "analytic_model", "client")


@dataclass
class FormValues:
    """Entradas NO sensibles del formulario (las únicas que pueden volver a renderizarse). El secreto jamás vive acá."""
    host: str = ""
    token_url: str = ""
    client_id: str = ""
    space: str = ""
    analytic_model: str = ""
    client: str = ""

    @classmethod
    def from_mapping(cls, data) -> "FormValues":
        return cls(**{k: str(data.get(k) or "").strip() for k in NON_SECRET_FIELDS})


@dataclass
class Outcome:
    state: str                                  # SUCCESS | FAILED | INVALID
    error_code: str | None = None
    message: str = ""
    errors: dict[str, str] = field(default_factory=dict)      # errores de validación por campo
    summary: dict | None = None                 # resultado no sensible (solo SUCCESS)
    trace: list[str] = field(default_factory=list)            # traza saneada (detalle técnico)
    operation_id: str = ""
    duration_s: float = 0.0


# ───────────────────────────── disponibilidad / autorización ─────────────────────────────
def tool_enabled(stem: str) -> bool:
    return stem in ASSESSMENT_MODULES


def can_execute(request) -> bool:
    """Ejecutar una extracción externa es más poderoso que ver un diagnóstico: solo el dueño de la app (APP_OWNER), el MISMO
    predicado que ya gobierna las herramientas operativas (`access.is_owner`). No se crea un RBAC nuevo."""
    from webapp import access
    return access.is_owner(request)


def tool_context(request, stem: str) -> dict:
    """Contexto mínimo para la plantilla del panel. Sin secretos; los defaults del formulario son vacíos (no hay nada derivable)."""
    return {"enabled": tool_enabled(stem), "can_execute": bool(tool_enabled(stem) and can_execute(request)), "stem": stem}


def cli_available() -> bool:
    """El CLI se invoca vía ``npx --no-install datasphere`` desde la raíz del repo: hace falta npx y la dependencia instalada."""
    root = Path(__file__).resolve().parents[2]
    return shutil.which("npx") is not None and (root / "node_modules" / "@sap" / "datasphere-cli").is_dir()


# ───────────────────────────── validación ─────────────────────────────
def _allowed_host(hostname: str) -> bool:
    host = (hostname or "").lower().strip(".")
    if not host:
        return False
    extra = {h.strip().lower() for h in os.getenv(ALLOWED_HOSTS_ENV, "").split(",") if h.strip()}
    return host in extra or any(host.endswith(sfx) for sfx in ALLOWED_HOST_SUFFIXES)


def _check_url(value: str, errors: dict, key: str) -> None:
    if not value:
        errors[key] = "Completá este campo."
        return
    parsed = urlparse(value)
    allowed = (parsed.hostname or "").lower() in {h.strip().lower() for h in os.getenv(ALLOWED_HOSTS_ENV, "").split(",") if h.strip()}
    if parsed.scheme != "https" and not (allowed and parsed.scheme == "http"):
        errors[key] = "Ingresá una URL https:// válida."
    elif parsed.username or parsed.password or not _allowed_host(parsed.hostname or ""):
        errors[key] = "El host no corresponde a un tenant de SAP Datasphere permitido."


def validate(values: FormValues, client_secret: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    _check_url(values.host, errors, "host")
    _check_url(values.token_url, errors, "token_url")
    if not values.client_id or len(values.client_id) > 300 or re.search(r"\s", values.client_id):
        errors["client_id"] = "Ingresá el Client ID (sin espacios)."
    if not client_secret or len(client_secret) > 1000:
        errors["client_secret"] = "Ingresá el Client Secret."
    for key, label in (("space", "Space"), ("analytic_model", "Analytic Model")):
        if not _NAME_RE.match(getattr(values, key)):
            errors[key] = f"Ingresá el nombre técnico del {label} (letras, números, _ . -)."
    if not _CLIENT_RE.match(values.client):
        errors["client"] = "Ingresá el cliente del catálogo (letras, números, espacios, _ . -)."
    return errors


# ───────────────────────────── single-flight ─────────────────────────────
_LOCKS_GUARD = threading.Lock()
_RUNNING: dict[tuple[str, str, str], threading.Lock] = {}


def _lock_for(values: FormValues) -> threading.Lock:
    key = (values.client.lower(), values.space.lower(), values.analytic_model.lower())
    with _LOCKS_GUARD:
        return _RUNNING.setdefault(key, threading.Lock())


# ───────────────────────────── saneamiento ─────────────────────────────
_TOKEN_RE = re.compile(r"(?i)(bearer\s+|access_token[\"'=:\s]+|client_secret[\"'=:\s]+|authorization[\"'=:\s]+basic\s+)[A-Za-z0-9._~+/=\-]{6,}")


def sanitize(text: str, secrets: tuple[str, ...] = (), roots: dict[str, str] | None = None) -> str:
    """Quita secretos conocidos, tokens/cabeceras tipo OAuth y rutas internas de un texto antes de mostrarlo o registrarlo."""
    out = str(text or "")
    for secret in secrets:
        if secret and len(secret) >= 4:
            import base64
            out = out.replace(secret, "[REDACTED]")
            out = out.replace(base64.b64encode(secret.encode()).decode(), "[REDACTED]")
    out = _TOKEN_RE.sub(lambda m: m.group(1) + "[REDACTED]", out)
    for real, alias in (roots or {}).items():
        if real:
            out = out.replace(real, alias).replace(real.replace("\\", "/"), alias)
    return out


def _trace(lines: list[str], secrets: tuple[str, ...], roots: dict[str, str], limit: int = 150) -> list[str]:
    clean = [sanitize(line, secrets, roots)[:300] for line in lines if str(line).strip()]
    return clean[-limit:]


# ───────────────────────────── ejecución ─────────────────────────────
def _summary(result, values: FormValues, op_id: str) -> dict:
    client_dir = re.sub(r'[\\/:*?"<>|]', "_", values.client.strip()).replace(" ", "_")
    prefix = (config.SPACES_PREFIX + "/") if getattr(config, "SPACES_PREFIX", "") else ""
    return {
        "client": values.client, "space": values.space, "analytic_model": values.analytic_model,
        "tenant": urlparse(values.host).hostname or "",
        "objects": result.objects, "edges": result.edges, "external_sources": result.external_sources,
        "exported_files": result.exported_files, "export_errors": result.export_errors,
        "files_by_type": dict(result.files_by_type),
        "deleted_stale": result.deleted_stale, "retained_shared": result.retained_shared,
        "pool_prefix": f"catalog_local/.csn_cache/{client_dir}/",
        "storage_enabled": result.storage_enabled,
        "storage_prefix": f"{prefix}catalog_local/.csn_cache/{client_dir}/" if result.storage_enabled else None,
        "mirrored_files": result.mirrored_files,
        "catalog_generated": result.catalog_generated,
        "duration_s": result.duration_s, "operation_id": op_id,
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def run(values: FormValues, client_secret: str, *, user_email: str = "", on_line=None) -> Outcome:
    """Valida y ejecuta la exportación con la MISMA función que el CLI. El secreto no se almacena ni se devuelve.
    ``on_line`` (opcional) recibe, EN TIEMPO REAL y en orden, cada línea que emite el extractor (ya saneada)."""
    op_id = uuid.uuid4().hex[:10]
    errors = validate(values, client_secret)
    if errors:
        return Outcome("INVALID", "INVALID_PARAMETER", ERROR_MESSAGES["INVALID_PARAMETER"], errors=errors, operation_id=op_id)
    if not cli_available():
        log.warning("datasphere_assessment op=%s status=FAILED code=DATASPHERE_CLI_UNAVAILABLE", op_id)
        return Outcome("FAILED", "DATASPHERE_CLI_UNAVAILABLE", ERROR_MESSAGES["DATASPHERE_CLI_UNAVAILABLE"], operation_id=op_id)
    lock = _lock_for(values)
    if not lock.acquire(blocking=False):
        return Outcome("FAILED", "OPERATION_ALREADY_RUNNING", ERROR_MESSAGES["OPERATION_ALREADY_RUNNING"], operation_id=op_id)
    secrets = (client_secret,)
    try:
        from tools.catalog import DSP_CLI_Export_Lineage as dsp
        from core import remote_store
        catalog_root = Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(config.BASE_DIR / "catalog_local"))
        roots = {str(catalog_root.resolve()): "<catalog_local>", str(config.BASE_DIR): "<app>"}
        request = dsp.ExportRequest(
            host=values.host, client_id=values.client_id, client_secret=client_secret, token_url=values.token_url,
            space=values.space, analytic_model=values.analytic_model, client=values.client,
            generate_catalog=False,                     # no se dispara una regeneración larga: "Actualizar catálogo" es un paso explícito
            mirror_up=bool(remote_store.enabled()),     # almacenamiento ya configurado en el Hub (sin credenciales en la UI)
        )
        def live(line: str) -> None:
            if on_line is not None:
                on_line(sanitize(line, secrets, roots)[:2000])

        with dsp.capture_output(on_line=live) as cap:
            result = dsp.run_export(request)
        trace = _trace(cap.lines, secrets, roots)
        if result.ok:
            # el índice del pool de assessment se invalida: la próxima vigencia se calcula contra el pool recién escrito
            try:
                from webapp.services import assessment_service
                assessment_service.invalidate_pool_index()
            except Exception:                                                   # noqa: BLE001 — nunca rompe una exportación exitosa
                log.warning("datasphere_assessment op=%s no se pudo invalidar el índice del pool", op_id)
            summary = _summary(result, values, op_id)
            log.info("datasphere_assessment op=%s user=%s client=%s space=%s model=%s status=SUCCESS duration=%.1fs files=%s",
                     op_id, user_email, values.client, values.space, values.analytic_model, result.duration_s, result.exported_files)
            return Outcome("SUCCESS", summary=summary, trace=trace, operation_id=op_id, duration_s=result.duration_s)
        code = result.error_code or "EXTRACTION_FAILED"
        log.warning("datasphere_assessment op=%s user=%s client=%s space=%s model=%s status=FAILED code=%s duration=%.1fs",
                    op_id, user_email, values.client, values.space, values.analytic_model, code, result.duration_s)
        # El mensaje humano de los prechecks (PRECHECK_OAUTH_401, PRECHECK_AUTH_403…) lo redacta el extractor: se muestra TAL CUAL (saneado),
        # sin re-diagnosticar. Para el resto de las fallas, el texto estable por código (el detalle crudo queda en el detalle técnico).
        backend = sanitize(result.message, secrets, roots)[:500]
        message = backend if str(result.message).startswith("PRECHECK_") else ERROR_MESSAGES.get(code, ERROR_MESSAGES["EXTRACTION_FAILED"])
        return Outcome("FAILED", code, message, trace=trace, operation_id=op_id, duration_s=result.duration_s)
    except Exception as exc:                                                    # noqa: BLE001 — jamás se muestra el texto crudo de la excepción
        log.warning("datasphere_assessment op=%s status=FAILED code=EXTRACTION_FAILED exc=%s", op_id, type(exc).__name__)
        return Outcome("FAILED", "EXTRACTION_FAILED", ERROR_MESSAGES["EXTRACTION_FAILED"],
                       trace=[f"{type(exc).__name__}"], operation_id=op_id)
    finally:
        lock.release()


# ───────────────────────────── actualizar catálogo (paso explícito) ─────────────────────────────
_REFRESH_LOCK = threading.Lock()


def refresh_catalog(*, user_email: str = "") -> Outcome:
    """Regenera el catálogo desde el pool CSN con el MISMO paso que usa el CLI (``regenerate_catalog_from_pool``). Es explícito:
    la exportación nunca lo dispara sola. Sirve para que el flujo exportado exista como entrada del Catálogo (y por tanto del
    informe de assessment)."""
    op_id = uuid.uuid4().hex[:10]
    if not _REFRESH_LOCK.acquire(blocking=False):
        return Outcome("FAILED", "OPERATION_ALREADY_RUNNING", "Ya hay una actualización del catálogo en curso.", operation_id=op_id)
    try:
        from tools.catalog import DSP_CLI_Export_Lineage as dsp
        catalog_root = Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(config.BASE_DIR / "catalog_local")).resolve()
        csn_cache = Path(os.getenv("DANWOR_CATALOG_CSN_CACHE", "").strip() or str(catalog_root / ".csn_cache")).resolve()
        roots = {str(catalog_root): "<catalog_local>", str(config.BASE_DIR): "<app>"}
        started = datetime.now(timezone.utc)
        summary = dsp.regenerate_catalog_from_pool(csn_cache, catalog_root)
        try:
            from webapp import main as hub_main
            hub_main._invalidate_catalog_cache()
        except Exception:                                                       # noqa: BLE001
            log.warning("datasphere_assessment op=%s no se pudo invalidar el caché del catálogo", op_id)
        duration = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        log.info("datasphere_assessment op=%s user=%s action=refresh_catalog status=SUCCESS duration=%.1fs", op_id, user_email, duration)
        return Outcome("SUCCESS", message=sanitize(summary, (), roots)[:600], operation_id=op_id, duration_s=duration)
    except Exception as exc:                                                    # noqa: BLE001
        log.warning("datasphere_assessment op=%s action=refresh_catalog status=FAILED exc=%s", op_id, type(exc).__name__)
        return Outcome("FAILED", "CATALOG_GENERATION_FAILED", ERROR_MESSAGES["CATALOG_GENERATION_FAILED"], operation_id=op_id)
    finally:
        _REFRESH_LOCK.release()


def summary_rows(r: dict) -> list[list[str]]:
    """Filas [etiqueta, valor] (texto plano) del resultado exitoso. La UI las pinta con textContent: no hay lógica de negocio en el navegador."""
    rows = [
        ["Cliente", r["client"]], ["Tenant", r["tenant"]], ["Space", r["space"]], ["Analytic Model", r["analytic_model"]],
        ["Objetos incluidos", str(r["objects"])], ["Relaciones", str(r["edges"])], ["Fuentes externas", str(r["external_sources"])],
        ["Archivos exportados", f"{r['exported_files']}" + (f" · {r['export_errors']} con error" if r["export_errors"] else "")],
        ["Destino (pool del catálogo)", r["pool_prefix"]],
    ]
    if r["storage_enabled"]:
        n = r["mirrored_files"] or 0
        rows.append(["Persistencia en Spaces", f"{n} archivo{'s' if n != 1 else ''} sincronizado{'s' if n != 1 else ''} · {r['storage_prefix']}"])
    else:
        rows.append(["Persistencia en Spaces", "No configurada en este entorno (solo copia local)"])
    rows += [["Duración", f"{r['duration_s']} s"], ["Finalizó", r["finished_at"]], ["Operación", r["operation_id"]]]
    if r["files_by_type"]:
        rows.append(["Archivos por tipo", ", ".join(f"{k}: {v}" for k, v in r["files_by_type"].items())])
    return rows
