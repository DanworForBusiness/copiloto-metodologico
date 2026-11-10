"""
datasphere_assessment_service.py — adaptador WEB de la exportación directa desde SAP Datasphere ("Assessment de tu modelo").

NO reimplementa nada: la extracción (preflight OAuth `client_credentials`, login del CLI, preflight de autorización sobre el Space,
inventario/linaje, escritura de la evidencia) vive en ``tools/catalog/DSP_CLI_Export_Lineage.py::run_export`` (la MISMA función que
usa el CLI) y la evaluación en el MISMO motor de assessment (vía ``assessment_service.assess_evidence_root``).

Contrato de producto: EVALUAR != PUBLICAR. Una ejecución es

    adquirir evidencia (workspace por operación, fuera de todo directorio persistido)
      → correr el Modeling Assessment sobre ESE workspace, solo para el AM pedido
      → guardar el perfil canónico bajo un ``result_id`` opaco (24 h; fuera de Spaces)
      → borrar la evidencia cruda → el navegador abre el informe

y NO toca el pool canónico del Catálogo, ``.lineage_runs``, Spaces ni la caché de assessment del Catálogo. ``generate_all``
("Actualizar catálogo") queda como capacidad separada (futuro "Catálogo → Agregar"), no se invoca desde esta herramienta.

Lo que este módulo agrega a ``run_export``:
  * validación de entradas (formato + allowlist de hosts: el servidor hará un POST con credenciales al token URL);
  * single-flight por (cliente, space, modelo);
  * normalización de errores a estados estables por etapa (extracción vs. assessment);
  * saneamiento de la traza (el secreto, tokens y rutas internas nunca salen de acá).

Secretos: el ``client_secret`` entra por argumento, vive solo durante la extracción, jamás se guarda (sesión, almacén de resultados,
JSON, activity log) ni se devuelve; la etapa de assessment recibe SOLO rutas de evidencia.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import config
from webapp.services import assessment_service as asvc
from webapp.services import operation_registry as _registry

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
    "EXTRACTION_FAILED": "La extracción desde Datasphere falló. Revisá el objeto indicado y el detalle técnico.",
    "CATALOG_GENERATION_FAILED": "La extracción terminó, pero falló la regeneración del catálogo.",
    "STORAGE_UPLOAD_FAILED": "La extracción terminó, pero falló la sincronización con el almacenamiento (Spaces).",
    "OPERATION_ALREADY_RUNNING": "Ya hay una extracción en curso para este cliente, Space y objeto. Esperá a que termine.",
    "NOT_AUTHORIZED": "No tenés permiso para ejecutar esta operación.",
    "ADHOC_STORAGE_UNSAFE": "El almacenamiento temporal del assessment no es seguro en este entorno (cae bajo un directorio persistido). No se ejecutó nada.",
    "AM_NOT_TERMINAL": "La evidencia se adquirió correctamente, pero el artefacto indicado no puede evaluarse con el alcance actual. Revisá el objeto seleccionado.",
    "ROOT_NOT_IN_EVIDENCE": "La evidencia se adquirió correctamente, pero el objeto indicado no figura en ella. Revisá el objeto seleccionado.",
    "ASSESSMENT_FAILED": "La evidencia se adquirió correctamente, pero ocurrió un error al generar el informe.",
    "POOL_LOAD_ERROR": "La evidencia se adquirió correctamente, pero ocurrió un error al generar el informe.",
    "POOL_UNAVAILABLE": "La evidencia se adquirió correctamente, pero ocurrió un error al generar el informe.",
    "INVALID_PROFILE": "La evidencia se adquirió correctamente, pero ocurrió un error al generar el informe.",
    "GENERATION_ERROR": "La evidencia se adquirió correctamente, pero ocurrió un error al generar el informe.",
}

# El extractor antepone un identificador técnico ("PRECHECK_OAUTH_401 - ..."): el texto humano se muestra, el identificador queda solo
# en "Detalle técnico" (el stdout ya trae la línea completa "ERROR: <código> - <mensaje>").
_CODE_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9_]{3,} - ")

# Códigos del extractor cuyo mensaje ya está redactado en español para el usuario (objeto no encontrado, tipo ambiguo): se muestra tal cual.
BACKEND_WORDED_CODES = frozenset({"ROOT_OBJECT_NOT_FOUND", "AMBIGUOUS_ROOT_OBJECT_TYPE", "INVALID_FEATURE_SCOPE"})

TITLE_EXTRACTION = "No se pudo completar la extracción"
TITLE_ASSESSMENT = "No se pudo generar el assessment"
# Ciclo de vida SEMÁNTICO de la operación (el backend es autoritativo sobre QUÉ fase ocurre; cómo se dibuja es del frontend).
# "extracting" es la fase inicial: arranca con el POST y no emite evento propio.
PHASE_ACQUIRED = "evidence_acquired"
PHASE_ASSESSING = "assessing"
PHASE_DONE = "assessment_ready"
PHASE_COPY = {PHASE_ACQUIRED: "Evidencia adquirida", PHASE_ASSESSING: "Generando assessment...", PHASE_DONE: "Assessment generado"}

FIELD_LABELS = {
    "host": "URL del tenant de Datasphere", "token_url": "Token URL (OAuth)", "client_id": "Client ID", "client_secret": "Client Secret",
    "space": "Space", "root_object": "Artefacto / Objeto", "client": "Cliente del catálogo",
}
NON_SECRET_FIELDS = ("host", "token_url", "client_id", "space", "root_object", "client")

# Alcance de evidencia. Las familias ESTRUCTURALES (modelos analíticos, vistas, tablas) son requeridas por este assessment (el cierre
# semántico depende de ellas) y NO son elegibles: el servidor siempre las pide. El usuario elige solo las familias opcionales, con los
# identificadores canónicos del extractor (sin alias). El servidor valida contra esta lista blanca; las dependencias las normaliza el extractor.
STRUCTURAL_FEATURES = ("analytic-models", "views", "tables")
OPTIONAL_FEATURES = ("replication-flows", "data-flows", "transformation-flows", "task-chains")
FEATURE_LABELS = {"replication-flows": "Replication Flows", "data-flows": "Data Flows",
                  "transformation-flows": "Transformation Flows", "task-chains": "Task Chains"}



@dataclass
class FormValues:
    """Entradas NO sensibles del formulario (las únicas que pueden volver a renderizarse). El secreto jamás vive acá."""
    host: str = ""
    token_url: str = ""
    client_id: str = ""
    space: str = ""
    root_object: str = ""
    client: str = ""
    features: tuple[str, ...] | None = None      # familias OPCIONALES elegidas; None = no enviado => todas (comportamiento actual)

    @classmethod
    def from_mapping(cls, data) -> "FormValues":
        raw = data.get("features")
        return cls(**{k: str(data.get(k) or "").strip() for k in NON_SECRET_FIELDS},
                   features=None if raw is None else parse_features(raw))


def parse_features(raw) -> tuple[str, ...]:
    """Familias opcionales válidas (lista blanca canónica, orden canónico, sin duplicados). Valores desconocidos se ignoran sin error."""
    wanted = {str(v).strip() for v in (raw if isinstance(raw, (list, tuple, set, frozenset)) else str(raw).split(","))}
    return tuple(f for f in OPTIONAL_FEATURES if f in wanted)


def requested_features(values: "FormValues") -> list[str] | None:
    """``ExportRequest.features_requested``: estructurales (siempre) + opcionales elegidas. ``None`` (no enviado) = el default del extractor
    (todas), idéntico a la extracción de hoy."""
    if values.features is None:
        return None
    return [*STRUCTURAL_FEATURES, *values.features]


@dataclass
class Outcome:
    state: str                                  # SUCCESS | FAILED | INVALID
    error_code: str | None = None
    message: str = ""
    errors: dict[str, str] = field(default_factory=dict)      # errores de validación por campo
    trace: list[str] = field(default_factory=list)            # traza saneada (detalle técnico)
    operation_id: str = ""
    duration_s: float = 0.0
    stage: str = ""                             # extraction | assessment (en qué etapa terminó)
    title: str = ""                             # título humano de la falla (según la etapa)
    result_id: str = ""                         # solo SUCCESS: resultado ad-hoc
    terminal_am: str = ""


# ───────────────────────────── disponibilidad / autorización ─────────────────────────────
def tool_enabled(stem: str) -> bool:
    return stem in ASSESSMENT_MODULES


def can_execute(request) -> bool:
    """Ejecutar el assessment ad-hoc (y ver/exportar/eliminar SUS resultados): cualquier consultor con sesión válida y perfil COMPLETO del
    Hub (``auth_service.user_tier == "full"``, el mismo que ve el panel "Herramientas de diagnóstico"; el perfil de solo lectura queda
    afuera, y un usuario retirado de la allowlist pierde el acceso en el acto porque se deriva del email en cada request) o el dueño de
    la app. La herramienta NO es pública: sin sesión no hay acceso. Esta política es SOLO de esta herramienta: ``access.is_owner`` no se
    toca y las capacidades administrativas (``can_administer``, ``can_use_client_profiles``) siguen siendo del dueño. La autorización
    sobre los DATOS del resultado sigue siendo por dueño del resultado (``_record_or_404``), no por esta compuerta."""
    from webapp import access
    from webapp.services import auth_service
    user = auth_service.session_user(request)
    if not user:
        return False
    return access.is_owner_user(user) or auth_service.user_tier(str(user.get("sub") or "")) == "full"


def can_administer(request) -> bool:
    """Capacidades ADMINISTRATIVAS ligadas a esta herramienta (regenerar el Catálogo desde el pool): solo el dueño (APP_OWNER)."""
    from webapp import access
    return access.is_owner(request)


def can_use_client_profiles(request) -> bool:
    """Perfiles de clientes (libro de SharePoint con las credenciales OAuth de TODOS los clientes): solo el dueño. Un consultor usa sus
    propias credenciales en el formulario; abrir el libro compartido a todos exige un modelo de autorización por cliente que no existe."""
    from webapp import access
    return access.is_owner(request)


_PREFILL_RE = re.compile(r"^[A-Za-z0-9_.\- ]{1,120}$")

# ───────────────────── continuidad Diagnóstico → Catálogo (SOLO LECTURA) ─────────────────────
# El resultado ad hoc NO se convierte en un Assessment gobernado: su perfil temporal no reemplaza nada. La ÚNICA operación que actualiza el Catálogo con la versión
# actual de un modelo es la PUBLICACIÓN (re-extrae el CSN al pool canónico como unidad transaccional, regenera el Catálogo y registra el Assessment canónico desde esa
# MISMA evidencia), con la guarda de identidad del pool intacta. ``catalog_continuity`` solo arma la revisión y decide qué operación está realmente disponible.
OP_UPDATE_SAME_SPACE = "UPDATE_FROM_SAME_SPACE"            # el modelo está en el Catálogo y su última publicación registrada es de ESTE Space
OP_INCORPORATE = "INCORPORATE"                            # el modelo no está en el Catálogo
OP_UPDATE_UNVERIFIED_SPACE = "UPDATE_SPACE_NOT_RECORDED"  # está en el Catálogo pero sin Space de publicación registrado: la guarda del pool decide al publicar
OP_REPLACE_OTHER_SPACE = "REPLACE_PUBLISHED_FROM_OTHER_SPACE"  # publicado desde OTRO Space: la publicación choca con POOL_IDENTITY_CONFLICT y muestra su alcance; el reemplazo
                                                              # explícito solo se ofrece si todos los objetos que difieren son exclusivos del modelo (publication_replacement)
OP_BLOCKED_OTHER_SPACE = OP_REPLACE_OTHER_SPACE               # nombre histórico (compatibilidad de importaciones)
OP_BLOCKED_AMBIGUOUS = "BLOCKED_AMBIGUOUS_CATALOG_IDENTITY"     # más de una entrada del Catálogo para el mismo cliente y modelo: no se elige una
OP_BLOCKED_NO_SPACE = "BLOCKED_RESULT_WITHOUT_SPACE"            # resultado previo a la procedencia por Space: hay que volver a diagnosticar
OP_BLOCKED_CATALOG_UNVERIFIED = "BLOCKED_CATALOG_NOT_VERIFIABLE"  # no se pudo leer el Catálogo o resolver la identidad: NO se afirma que el modelo no exista
# Resoluciones que NO establecen una identidad cierta (no permiten afirmar que el modelo no está en el Catálogo). NO_TERMINAL_AM sí es una resolución válida.
_UNVERIFIABLE_RESOLUTIONS = frozenset({"POOL_UNAVAILABLE", "POOL_AMBIGUOUS_DOMAIN", "POOL_LOAD_ERROR", "MULTIPLE_TERMINAL_AMS"})
PUBLISHABLE_OPERATIONS = frozenset({OP_UPDATE_SAME_SPACE, OP_INCORPORATE, OP_UPDATE_UNVERIFIED_SPACE, OP_REPLACE_OTHER_SPACE})


def _replacement_preview(client_dir: str, am: str, space: str) -> dict | None:
    """Vista previa SIN extracción de la publicación anterior del modelo (otro Space): cuántos objetos tiene y cuáles comparte con otros modelos. No decide nada."""
    try:
        from tools.catalog import publication_replacement as rp
        from webapp.services import datasphere_publication_service as pub
        catalog_root, csn_cache = pub._canonical_roots()
        return rp.preview(catalog_root=catalog_root, csn_cache_root=csn_cache, client_dir=client_dir, root_type="analytic-model", root_object=am, target_space=space)
    except Exception:                                    # noqa: BLE001 — la vista previa es informativa: sin ella, la publicación igual calcula el alcance real
        log.warning("catalog_continuity: no se pudo armar la vista previa del reemplazo", exc_info=True)
        return None


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _query_params(request) -> Any:
    """Parámetros de la URL, o vacío si el request no los trae (p. ej. un Request mínimo sin ``query_string``): la precarga es opcional y nunca rompe el panel."""
    try:
        return request.query_params
    except Exception:                                    # noqa: BLE001
        return {}


def publication_prefill_url(stem: str, pack: str, client: str, root_object: str, space: str) -> str:
    """Formulario de PUBLICACIÓN (pestaña Catálogo) con el modelo y el Space DEL RESULTADO precargados; las credenciales las ingresa el usuario."""
    return f"/modules/{stem}?" + urllib.parse.urlencode({"pack": pack, "tab": "catalogo", "catalog_action": "publish", "client": client, "root": root_object, "space": space})


def catalog_continuity(record: dict) -> dict:
    """Revisión Diagnóstico → Catálogo de UN resultado ad hoc: identidad (cliente, modelo, Space de origen), Assessment del Catálogo si existe (estado, nivel, Space de su
    última publicación registrada) y la operación gobernada realmente disponible. Solo lee; no ejecuta nada ni usa el perfil temporal como Assessment gobernado."""
    client, am, space = str(record.get("client_display") or ""), str(record.get("terminal_am") or ""), str(record.get("space") or "")
    judgment = asvc.provisional_judgment(record.get("profile"))
    out: dict[str, Any] = {"client": client, "model": am, "space": space or None, "result_id": record.get("result_id"),
                           "diagnostic": {"status": (judgment or {}).get("status"), "level": (judgment or {}).get("level"), "rule": (judgment or {}).get("level_rule")},
                           "catalog": None, "differences": [], "operation": None}
    matches: list = []
    unverifiable = False
    try:
        from webapp.services import catalog_reads
        mod, entries = catalog_reads._catalog_entries()
        if mod is None:                                  # el Catálogo no se pudo cargar: (None, []) NO significa «sin modelos»
            raise RuntimeError("catalog not loaded")
        candidates = [e for e in entries if _norm(getattr(e, "cliente", "")) == _norm(client)]
        resolved = asvc.resolve_entries(candidates) if candidates else {}
        if any(getattr(resolved.get(e.entry_id), "kind", None) in _UNVERIFIABLE_RESOLUTIONS for e in candidates):
            raise RuntimeError("catalog identity not resolvable")          # una entrada sin identidad cierta podría ser este modelo
        matches = [e for e in candidates if getattr(resolved.get(e.entry_id), "terminal_am", None) == am]
        if len(matches) == 1:
            entry = matches[0]
            res = resolved[entry.entry_id]
            status = asvc.status_for_entry(entry)
            published = asvc.published_space(res.client_dir, am)
            out["catalog"] = {"entry": entry, "client_dir": res.client_dir, "state": status.state, "level": (status.judgment or {}).get("level"), "status": (status.judgment or {}).get("status"),
                              "rule": (status.judgment or {}).get("level_rule"), "published_space": published, "generated_at": status.generated_at}
            if published and space and _norm(published) != _norm(space):
                out["differences"].append(f"El modelo del Catálogo se publicó desde el Space {published}; este diagnóstico corresponde al Space {space}.")
            elif not published:
                out["differences"].append("El Catálogo no tiene registrado desde qué Space se publicó este modelo.")
    except Exception:                                    # noqa: BLE001 — sin Catálogo legible o sin identidad resuelta no se afirma nada sobre él
        log.warning("catalog_continuity: no se pudo leer el Catálogo o resolver la identidad", exc_info=True)
        unverifiable = True
        matches, out["catalog"], out["differences"] = [], None, []
        out["catalog_unavailable"] = True
    if not space:
        out["operation"] = OP_BLOCKED_NO_SPACE
    elif unverifiable:
        out["operation"] = OP_BLOCKED_CATALOG_UNVERIFIED
    elif len(matches) > 1:
        out["operation"] = OP_BLOCKED_AMBIGUOUS
    elif not matches:
        out["operation"] = OP_INCORPORATE
    elif not out["catalog"]["published_space"]:
        out["operation"] = OP_UPDATE_UNVERIFIED_SPACE
    elif _norm(out["catalog"]["published_space"]) == _norm(space):
        out["operation"] = OP_UPDATE_SAME_SPACE
    else:
        out["operation"] = OP_REPLACE_OTHER_SPACE
        out["replacement_preview"] = _replacement_preview(out["catalog"].get("client_dir") or client, am, space)
    return out


def catalog_prefill_url(stem: str, pack: str, client: str, root_object: str) -> str:
    """Enlace del Catálogo a ESTA herramienta con el modelo precargado (cliente + objeto raíz; nunca Space ni credenciales)."""
    return f"/modules/{stem}?" + urllib.parse.urlencode({"pack": pack, "tool": "assessment", "client": client, "root": root_object})


def _catalog_prefill(request, values: dict[str, str]) -> dict[str, str]:
    """Precarga desde el Catálogo (``?client=…&root=…``): SOLO campos no sensibles y validados. El Space se VACÍA a propósito: el consultor elige desde qué Space
    evaluar (la versión publicada en el Catálogo puede venir de otro); nunca se infiere. Sin parámetros, el contexto de la sesión queda como estaba."""
    qp = _query_params(request)
    client, root = str(qp.get("client") or "").strip(), str(qp.get("root") or "").strip()
    if not (client and root and _PREFILL_RE.match(client) and _PREFILL_RE.match(root)):
        return values
    return {**values, "client": client, "root_object": root, "space": ""}


def publication_prefill(request, values: dict[str, str]) -> dict[str, str]:
    """Precarga del formulario de PUBLICACIÓN desde la revisión Diagnóstico → Catálogo (``?client=…&root=…&space=…``): cliente, modelo y el Space DEL RESULTADO
    diagnosticado (procedencia registrada, no inferida del contexto del usuario). Validados; nunca credenciales. Desde el Catálogo («Regenerar Catálogo» del flujo) el Space es el de
    la última publicación registrada; si no hay registro llega vacío y se deja VACÍO a propósito (lo elige el usuario; nunca el de la sesión)."""
    qp = _query_params(request)
    client, root, space = (str(qp.get(k) or "").strip() for k in ("client", "root", "space"))
    if not (client and root and all(_PREFILL_RE.match(v) for v in (client, root)) and (not space or _PREFILL_RE.match(space))):
        return values
    return {**values, "client": client, "root_object": root, "space": space}


def tool_context(request, stem: str) -> dict:
    """Contexto para la plantilla del panel. Sin secretos: ``form`` son SOLO los campos no sensibles de la última ejecución de ESTA
    sesión (el Client Secret no existe en ningún almacén) y ``recent`` los assessments ad-hoc vivos del propio usuario."""
    enabled = tool_enabled(stem)
    can = bool(enabled and can_execute(request))
    can_profiles = bool(can and can_use_client_profiles(request))
    values: dict[str, str] = {}
    recent: list[dict] = []
    if can:
        values = load_form_context(session_key(request))
        recent = list_results(owner_email(request))
        values = _catalog_prefill(request, values)
    selected = values.get("features")
    checked = list(OPTIONAL_FEATURES) if selected is None else [f for f in OPTIONAL_FEATURES if f in selected.split(",")]
    options = [{"value": f, "label": FEATURE_LABELS[f], "checked": f in checked} for f in OPTIONAL_FEATURES]
    from webapp.services import datasphere_profiles
    return {"enabled": enabled, "can_execute": can, "stem": stem, "form": values, "recent": recent, "feature_options": options,
            "owner_profiles_url": datasphere_profiles.selector_url(stem, can_profiles)}


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
    if not _NAME_RE.match(values.space):
        errors["space"] = "Ingresá el nombre técnico del Space (letras, números, _ . -)."
    if not _NAME_RE.match(values.root_object):
        errors["root_object"] = "Ingresá un artefacto u objeto válido (letras, números, _ . -)."
    if not _CLIENT_RE.match(values.client):
        errors["client"] = "Ingresá el cliente del catálogo (letras, números, espacios, _ . -)."
    return errors


# ───────────────────────────── single-flight ─────────────────────────────
_LOCKS_GUARD = threading.Lock()
_RUNNING: dict[tuple[str, str, str], threading.Lock] = {}


def _lock_for(values: FormValues) -> threading.Lock:
    key = (values.client.lower(), values.space.lower(), values.root_object.lower())
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


def _trace(lines: list[str], secrets: tuple[str, ...], roots: dict[str, str], limit: int = 2000) -> list[str]:
    clean = [sanitize(line, secrets, roots)[:300] for line in lines if str(line).strip()]
    return clean[-limit:]


# ───────────────────────────── workspace y resultados AD-HOC ─────────────────────────────
# Evaluar != publicar: la evidencia de una ejecución vive en un workspace por operación y el RESULTADO (perfil canónico) en un almacén
# propio, ambos bajo ``ASSESSMENT_CACHE_DIR/adhoc`` (no persistido, jamás espejado a Spaces). No comparten espacio de claves con la
# caché del Catálogo ((cliente, AM)): cada resultado tiene un ``result_id`` opaco.
RESULT_TTL_S = 24 * 3600
_RESULT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_boot_lock = threading.Lock()
_boot_done = False


class AdhocStorageError(RuntimeError):
    """La raíz ad-hoc caería bajo un directorio persistido/gobernado: FAIL-CLOSED (no se ejecuta nada)."""


def adhoc_base() -> Path:
    """``<ASSESSMENT_CACHE_DIR>/adhoc``. Reusa la guarda fail-closed del servicio de assessment (``cache_dir``: None si la ruta cae bajo
    cualquier directorio persistido, ``catalog_local`` o el pool CSN canónico)."""
    d = asvc.cache_dir()
    if d is None:
        raise AdhocStorageError("ASSESSMENT_CACHE_DIR cae bajo un directorio persistido o gobernado")
    return d / "adhoc"


def _dirs() -> tuple[Path, Path]:
    """(workspaces, resultados). La primera vez que este PROCESO los usa se borra todo resto anterior: un resultado ad-hoc no
    sobrevive a un reemplazo de proceso/runtime (ni quedan workspaces huérfanos de una caída)."""
    global _boot_done
    base = adhoc_base()
    with _boot_lock:
        if not _boot_done:
            shutil.rmtree(base / "work", ignore_errors=True)
            shutil.rmtree(base / "results", ignore_errors=True)
            shutil.rmtree(base / "context", ignore_errors=True)
            _boot_done = True
    work, results = base / "work", base / "results"
    work.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    (base / "context").mkdir(parents=True, exist_ok=True)
    return work, results


def reset_boot_state() -> None:
    """Solo tests: simula el reemplazo de proceso."""
    global _boot_done
    _boot_done = False


def purge_expired_results(*, now: float | None = None) -> int:
    """Borra los resultados vencidos (> 24 h). Oportunista: se llama al crear y al leer un resultado (sin scheduler ni worker)."""
    _work, results = _dirs()
    now = time.time() if now is None else now
    removed = 0
    for path in results.glob("*.json"):
        try:
            created = float(json.loads(path.read_text(encoding="utf-8")).get("created_at", 0))
        except Exception:                                                       # noqa: BLE001 — ilegible = descartable
            created = 0.0
        if now - created > RESULT_TTL_S:
            path.unlink(missing_ok=True)
            removed += 1
    for path in (results.parent / "context").glob("*.json"):
        try:
            created = float(json.loads(path.read_text(encoding="utf-8")).get("created_at", 0))
        except Exception:                                                       # noqa: BLE001
            created = 0.0
        if now - created > RESULT_TTL_S:
            path.unlink(missing_ok=True)
    return removed


def save_result(profile: dict, *, client_display: str, client_dir: str, terminal_am: str, owner: str = "",
                execution_log: list[str] | None = None, space: str = "") -> str:
    """Guarda el perfil canónico y el contexto mínimo de presentación (sin credenciales) bajo un ``result_id`` opaco.
    ``owner`` (email, minúsculas) acota quién puede ver/listar el resultado. ``execution_log`` es el log YA saneado de la corrida: metadato
    auxiliar efímero (vive y vence con el resultado) que NUNCA entra al JSON/Markdown canónico. ``space`` = el Space VALIDADO de la
    ejecución: metadato de PROCEDENCIA del resultado (se muestra en la cabecera); no entra al perfil, a su firma ni al Juicio."""
    _work, results = _dirs()
    purge_expired_results()
    result_id = uuid.uuid4().hex
    record = {"result_id": result_id, "created_at": time.time(), "client_display": client_display, "client_dir": client_dir,
              "terminal_am": terminal_am, "owner": (owner or "").strip().lower(), "execution_log": list(execution_log or []),
              "space": str(space or "").strip() or None, "profile": profile}
    fd, tmp = tempfile.mkstemp(dir=str(results), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
        os.replace(tmp, results / f"{result_id}.json")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return result_id


def load_result(result_id: str) -> dict | None:
    """El registro del resultado, o None si el id es inválido, no existe o venció (y en ese caso se elimina)."""
    if not _RESULT_ID_RE.match(result_id or ""):
        return None
    try:
        _work, results = _dirs()
    except AdhocStorageError:
        return None
    purge_expired_results()
    path = results / f"{result_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                                           # noqa: BLE001
        return None


def delete_result(result_id: str, owner: str) -> bool:
    """Elimina SOLO el resultado ad-hoc ``result_id`` (perfil + log + metadatos viven en ese único registro) si pertenece a ``owner``.
    No toca el contexto del formulario, otros resultados, el Catálogo, Spaces ni ningún pool. False si no existe/venció/es de otro usuario."""
    record = load_result(result_id)
    if record is None or str(record.get("owner") or "") != (owner or "").strip().lower():
        return False
    _work, results = _dirs()
    (results / f"{result_id}.json").unlink(missing_ok=True)
    return True


def result_status(record: dict) -> asvc.AssessmentStatus:
    """El estado CURRENT del resultado (misma estructura que usa el informe del Catálogo, sin flujo ni caché del Catálogo). El Juicio es el
    PROVISIONAL de esta ejecución (``asvc.provisional_judgment`` sobre su propio perfil): nunca el publicado en el Catálogo."""
    created = datetime.fromtimestamp(float(record["created_at"]), tz=timezone.utc).isoformat(timespec="seconds")
    return asvc.AssessmentStatus(asvc.CURRENT, record["result_id"], record["client_display"], record["terminal_am"],
                                 generated_at=created, profile=record["profile"], judgment=asvc.provisional_judgment(record["profile"]))


def owner_email(request) -> str:
    from webapp.services import auth_service
    user = auth_service.session_user(request) or {}
    return str(user.get("sub") or user.get("email") or "").strip().lower()


def list_results(owner: str) -> list[dict]:
    """Assessments ad-hoc VIVOS de ``owner`` (más recientes primero). Sin rutas del sistema de archivos; los vencidos o ilegibles no
    aparecen. Un resultado de otro usuario (o sin dueño) nunca se lista."""
    owner = (owner or "").strip().lower()
    if not owner:
        return []
    try:
        _work, results = _dirs()
    except AdhocStorageError:
        return []
    purge_expired_results()
    out: list[dict] = []
    for path in results.glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                                       # noqa: BLE001
            continue
        if str(rec.get("owner") or "") != owner or not _RESULT_ID_RE.match(str(rec.get("result_id") or "")):
            continue
        created = float(rec.get("created_at", 0))
        out.append({"id": rec["result_id"], "root_object": rec.get("terminal_am", ""), "client": rec.get("client_display", ""),
                    "created_at": created, "generated": datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    "has_log": bool(rec.get("execution_log"))})
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


# ── Contexto de formulario (SOLO campos no sensibles), por sesión, efímero ──
def session_key(request) -> str:
    """Clave opaca de la sesión actual: hash de la cookie de sesión. Un logout/login emite otro token => otro contexto."""
    from webapp.services import auth_service
    token = request.cookies.get(auth_service.COOKIE_NAME) or ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32] if token else ""


def _context_path(key: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{32}", key or ""):
        return None
    _work, results = _dirs()
    return results.parent / "context" / f"{key}.json"


def save_form_context(key: str, values: FormValues) -> None:
    """Guarda los valores NO sensibles (``NON_SECRET_FIELDS``) de la última ejecución de esta sesión. FormValues no tiene campo de
    secreto: no hay manera de que el Client Secret llegue acá."""
    try:
        path = _context_path(key)
    except AdhocStorageError:
        return
    if path is None:
        return
    data = {name: getattr(values, name) for name in NON_SECRET_FIELDS}
    if values.features is not None:
        data["features"] = ",".join(values.features)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"created_at": time.time(), "values": data}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_form_context(key: str) -> dict[str, str]:
    try:
        path = _context_path(key)
    except AdhocStorageError:
        return {}
    if path is None or not path.is_file():
        return {}
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                                           # noqa: BLE001
        return {}
    if time.time() - float(rec.get("created_at", 0)) > RESULT_TTL_S:
        path.unlink(missing_ok=True)
        return {}
    values = rec.get("values") or {}
    out = {name: str(values.get(name) or "") for name in NON_SECRET_FIELDS}
    if "features" in values:
        out["features"] = ",".join(parse_features(str(values.get("features") or "")))
    return out


# ───────────────────── operación desacoplada de la conexión HTTP ─────────────────────
# La extracción corre en su propio hilo y NO depende de la respuesta HTTP que la inició: el proxy de App Platform corta las
# conexiones (≈100 s, ver docs/CCHH_EXPORTACION_CVS_FASE2_REQUISITOS.md) y una lectura larga del CLI (p. ej. un Replication Flow
# grande) deja el stream sin bytes. Cada evento del run (start/line/phase/done) se registra acá, en memoria del proceso (réplica
# única, como los candados de `_RUNNING`), para que el navegador:
#   · reciba un latido (`{"t":"ping"}`) cada `HEARTBEAT_S` mientras no haya salida (la conexión no queda inactiva), y
#   · si la conexión igual se corta, recupere el avance y el resultado por `operation_events` SIN reiniciar la extracción.
# Solo eventos ya saneados (los mismos del stream); el secreto jamás entra. La operación es del usuario que la inició.
#
# DEPENDENCIA DE DESPLIEGUE — UN SOLO PROCESO: el registro vive en la memoria del proceso que ejecuta la extracción (sin almacenamiento
# persistente, por diseño). Requiere UNA réplica (App Platform → componente web → instance_count = 1, misma precondición documentada en
# docs/PUBLICATION_SAFE_RUNBOOK.md) y UN worker de uvicorn: el Procfile no pasa `--workers`, así que uvicorn usa `WEB_CONCURRENCY` si
# el entorno la define (si no, 1). Con más de un proceso, la consulta de recuperación puede llegar a otro proceso y responder 404: el
# navegador muestra la falla (nunca espera indefinidamente) y el resultado igual aparece en «Assessments recientes» si se guardó.
# Verificación en la Console del componente web:
#   echo "WEB_CONCURRENCY=${WEB_CONCURRENCY:-sin definir}"; ps -eo pid,ppid,args | grep "[u]vicorn"
# (esperado: WEB_CONCURRENCY sin definir o 1, y un único proceso uvicorn). Un reinicio o deploy durante la corrida pierde la operación
# igual que hoy pierde la extracción en curso; un resultado ya guardado no se pierde.
HEARTBEAT_S = 15.0
OPERATION_TTL_S = _registry.OPERATION_TTL_S          # = vida del resultado ad hoc
# Plazo máximo de ESPERA del navegador: pasado este tiempo sin `done`, la operación se cierra con OPERATION_TIMEOUT (terminal y visible).
# No mata la extracción (el CLI es compartido con la publicación y no tiene timeout por lectura): si termina después, su resultado se
# guarda igual y aparece en «Assessments recientes». Evita el único camino de espera indefinida: una lectura del CLI que nunca vuelve.
OPERATION_MAX_S = 3 * 3600
_OPERATION_MAX_EVENTS = _registry._OPERATION_MAX_EVENTS
OPERATION_TIMEOUT_MESSAGE = ("La extracción superó el tiempo máximo de seguimiento. Si termina, su resultado aparecerá en «Assessments "
                             "recientes»; revisá en el detalle técnico el último objeto que se estaba leyendo.")
# El registro vive en `operation_registry` (compartido con la publicación); estos nombres siguen siendo los de siempre.
_OPERATIONS = _registry._OPERATIONS
publish_event = _registry.publish_event
wait_events = _registry.wait_events
operation_events = _registry.operation_events


def start_operation(owner: str, *, now: float | None = None, meta: dict | None = None) -> str:
    """Operación de DIAGNÓSTICO en el registro compartido: su cierre por plazo de seguimiento es el OPERATION_TIMEOUT de siempre (no reabre)."""
    return _registry.start_operation(owner, now=now, meta={"kind": "assessment", **(meta or {})}, max_s=OPERATION_MAX_S, max_events=_OPERATION_MAX_EVENTS,
                                     timeout_event={"t": "done", "state": "FAILED", "error_code": "OPERATION_TIMEOUT", "message": OPERATION_TIMEOUT_MESSAGE,
                                                    "title": TITLE_EXTRACTION, "errors": {}, "operation_id": None, "duration_s": None, "result_url": None})


# ───────────────────────────── ejecución ─────────────────────────────
def run(values: FormValues, client_secret: str, *, user_email: str = "", on_line=None, on_phase=None) -> Outcome:
    """Valida y encadena: extracción (MISMA función que el CLI) → assessment (MISMO motor) → resultado ad-hoc. El secreto no se
    almacena ni se devuelve y el assessment recibe SOLO rutas de evidencia.
    ``on_line`` recibe, EN TIEMPO REAL y en orden, cada línea del extractor (saneada); ``on_phase(fase)`` recibe los hitos semánticos veraces (``PHASE_*``)."""
    op_id = uuid.uuid4().hex[:10]
    errors = validate(values, client_secret)
    if errors:
        return Outcome("INVALID", "INVALID_PARAMETER", ERROR_MESSAGES["INVALID_PARAMETER"], errors=errors, operation_id=op_id)
    if not cli_available():
        log.warning("datasphere_assessment op=%s status=FAILED code=DATASPHERE_CLI_UNAVAILABLE", op_id)
        return Outcome("FAILED", "DATASPHERE_CLI_UNAVAILABLE", ERROR_MESSAGES["DATASPHERE_CLI_UNAVAILABLE"], operation_id=op_id,
                       stage="extraction", title=TITLE_EXTRACTION)
    lock = _lock_for(values)
    if not lock.acquire(blocking=False):
        return Outcome("FAILED", "OPERATION_ALREADY_RUNNING", ERROR_MESSAGES["OPERATION_ALREADY_RUNNING"], operation_id=op_id,
                       stage="extraction", title=TITLE_EXTRACTION)
    # Formas derivadas del secreto que podrían aparecer en una línea de log (Basic = base64(id:secret), urlencoded): también se redactan.
    secrets = tuple(dict.fromkeys(s for s in (
        client_secret,
        base64.b64encode(f"{values.client_id}:{client_secret}".encode("utf-8")).decode("ascii"),
        urllib.parse.quote(client_secret, safe=""), urllib.parse.quote_plus(client_secret)) if s))
    workspace: Path | None = None
    try:
        try:
            work_root, _results = _dirs()
        except AdhocStorageError:
            log.error("datasphere_assessment op=%s ruta ad-hoc insegura: no se ejecuta", op_id)
            return Outcome("FAILED", "ADHOC_STORAGE_UNSAFE", ERROR_MESSAGES["ADHOC_STORAGE_UNSAFE"], operation_id=op_id,
                           stage="extraction", title=TITLE_EXTRACTION)
        workspace = work_root / op_id
        (workspace / "catalog").mkdir(parents=True)
        (workspace / "pool").mkdir(parents=True)

        from tools.catalog import DSP_CLI_Export_Lineage as dsp
        roots = {str(workspace.resolve()): "<adhoc>", str(config.BASE_DIR): "<app>"}
        request = dsp.ExportRequest(
            host=values.host, client_id=values.client_id, client_secret=client_secret, token_url=values.token_url,
            space=values.space, root_object=values.root_object, client=values.client,
            catalog_root=workspace / "catalog", csn_cache_dir=workspace / "pool",     # raíces del workspace: NADA canónico se toca
            generate_catalog=False,                                                    # evaluar != publicar: sin Catálogo ni Spaces (modo ASSESSMENT jamás espeja)
            mode="assessment",                                                         # forzado en el servidor: la UI no elige el modo
            features_requested=requested_features(values),                             # SOLO lo pedido: las dependencias las resuelve el extractor
        )

        def live(line: str) -> None:
            if on_line is not None:
                on_line(sanitize(line, secrets, roots)[:2000])

        with dsp.capture_output(on_line=live) as cap:
            result = dsp.run_export(request)
        trace = _trace(cap.lines, secrets, roots)
        if not result.ok:
            code = result.error_code or "EXTRACTION_FAILED"
            log.warning("datasphere_assessment op=%s user=%s client=%s space=%s model=%s status=FAILED stage=extraction code=%s duration=%.1fs",
                        op_id, user_email, values.client, values.space, values.root_object, code, result.duration_s)
            # El mensaje humano de los prechecks (PRECHECK_OAUTH_401, PRECHECK_AUTH_403…) lo redacta el extractor: se muestra TAL CUAL
            # (saneado), sin re-diagnosticar. Para el resto, el texto estable por código (el detalle crudo queda en el detalle técnico).
            backend = _CODE_PREFIX_RE.sub("", sanitize(result.message, secrets, roots)[:500], count=1)   # el identificador técnico NO es visible
            message = backend if (str(result.message).startswith("PRECHECK_") or code in BACKEND_WORDED_CODES) else ERROR_MESSAGES.get(code, ERROR_MESSAGES["EXTRACTION_FAILED"])
            return Outcome("FAILED", code, message, trace=trace, operation_id=op_id, duration_s=result.duration_s,
                           stage="extraction", title=TITLE_EXTRACTION)

        # ── Evidencia adquirida → assessment automático (solo rutas de evidencia; las credenciales ya no existen para esta etapa) ──
        if on_phase is not None:
            on_phase(PHASE_ACQUIRED)
            on_phase(PHASE_ASSESSING)
        client_dir = result.pool_dir.name if result.pool_dir is not None else values.client
        # Alcance de evidencia REAL de la extracción (pedido y efectivo, tal como los informa el extractor): el documento lo conserva para
        # distinguir "adquirido y ausente" de "no adquirido". Sin metadato propio de la web.
        scope = {"requested_features": list(result.requested_features or ()), "effective_features": list(result.effective_features or ())}
        profile, code = asvc.assess_evidence_root(workspace / "pool", client_dir, values.root_object, "General", evidence_scope=scope,
                                                  root_type=result.root_type)
        if code is not None or profile is None:
            code = code or "GENERATION_ERROR"
            log.warning("datasphere_assessment op=%s user=%s client=%s space=%s model=%s status=FAILED stage=assessment code=%s",
                        op_id, user_email, values.client, values.space, values.root_object, code)
            return Outcome("FAILED", code, ERROR_MESSAGES.get(code, ERROR_MESSAGES["ASSESSMENT_FAILED"]), trace=trace, operation_id=op_id,
                           duration_s=result.duration_s, stage="assessment", title=TITLE_ASSESSMENT)
        result_id = save_result(profile, client_display=values.client, client_dir=client_dir, terminal_am=values.root_object,
                                owner=user_email, execution_log=trace, space=values.space)
        if on_phase is not None:
            on_phase(PHASE_DONE)
        log.info("datasphere_assessment op=%s user=%s client=%s space=%s model=%s status=SUCCESS duration=%.1fs files=%s",
                 op_id, user_email, values.client, values.space, values.root_object, result.duration_s, result.exported_files)
        return Outcome("SUCCESS", trace=trace, operation_id=op_id, duration_s=result.duration_s, stage="assessment",
                       result_id=result_id, terminal_am=values.root_object)
    except Exception as exc:                                                    # noqa: BLE001 — jamás se muestra el texto crudo de la excepción
        log.warning("datasphere_assessment op=%s status=FAILED code=EXTRACTION_FAILED exc=%s", op_id, type(exc).__name__)
        return Outcome("FAILED", "EXTRACTION_FAILED", ERROR_MESSAGES["EXTRACTION_FAILED"], trace=[f"{type(exc).__name__}"],
                       operation_id=op_id, stage="extraction", title=TITLE_EXTRACTION)
    finally:
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)                        # la evidencia cruda nunca sobrevive a la operación
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
