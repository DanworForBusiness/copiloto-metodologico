"""
datasphere_publication_service.py — adaptador WEB de la PUBLICACIÓN de un modelo de SAP Datasphere al Catálogo gobernado.

Hermano de ``datasphere_assessment_service`` pero con el contrato OPUESTO: PUBLICAR, no evaluar. Una ejecución es

    adquirir evidencia (CSN) en el pool CANÓNICO (``catalog_local/.csn_cache``)
      → regenerar el Catálogo desde ese pool (``generate_catalog=True``)
      → invalidar la caché del Catálogo → el nuevo dominio queda disponible en la pestaña "Catálogo"
      → registrar el assessment CANÓNICO del modelo desde ESA MISMA evidencia (``register_assessment``: sin reconectar ni re-extraer)

Se apoya en la MISMA función que el CLI (``tools/catalog/DSP_CLI_Export_Lineage.py::run_export``), con el modo forzado por el
SERVIDOR (``mode="publish"``): la UI nunca elige el modo, y esta herramienta NO comparte su ruta POST con el Assessment (evaluar y
publicar son acciones de negocio distintas; ver webapp/routers/datasphere_publication.py).

Reusa del servicio de Assessment TODO lo que es manejo de entrada / saneamiento / validación (no es "acción de negocio"):
``FormValues``, ``validate``, ``cli_available``, ``sanitize``/``_trace``, ``requested_features``, ``Outcome``, los catálogos de
mensajes de error y de features, y el contexto de formulario NO sensible por sesión. Lo propio de acá es el ``run()`` que publica.

Secretos: el ``client_secret`` entra por argumento, vive solo durante la extracción, jamás se guarda ni se devuelve ni se registra.
A diferencia del Assessment, esta herramienta SÍ escribe en el pool/Catálogo canónicos (ese es su objetivo); nunca sube a Spaces
salvo que se habilite explícitamente por entorno (``DANWOR_PUBLICATION_MIRROR_UP``).
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import config
from webapp.services import datasphere_assessment_service as asd

log = logging.getLogger("danwor.datasphere_publication")

# Módulos donde la publicación está disponible (mismo alcance que el Assessment: 04 · Modelado & Desarrollo).
PUBLICATION_MODULES = frozenset({"04_Modelado_y_Desarrollo"})

TITLE_PUBLISH = "No se pudo publicar el modelo"
# Fase semántica única: "extracting" es la inicial (sin evento); al terminar la extracción se regenera el Catálogo.
PHASE_PUBLISHING = "publishing"
PHASE_REGISTERING = "registering"
PHASE_PUBLISHED = "published"
PHASE_COPY = {PHASE_PUBLISHING: "Publicando en el catálogo...", PHASE_REGISTERING: "Registrando el assessment del modelo...",
              PHASE_PUBLISHED: "Catálogo actualizado"}


# ───────────────────────────── disponibilidad / autorización ─────────────────────────────
def tool_enabled(stem: str) -> bool:
    return stem in PUBLICATION_MODULES


def can_execute(request) -> bool:
    """Publicar en el Catálogo gobernado es una operación de dueño (``access.is_owner``). NO comparte la política del Assessment
    ad-hoc, que se abrió a los consultores: este predicado es propio y no se deriva de ``asd.can_execute``."""
    from webapp import access
    return access.is_owner(request)


def tool_context(request, stem: str) -> dict:
    """Contexto para la plantilla del panel de publicación. Mismo shape que el Assessment (``enabled/can_execute/stem/form/
    feature_options``) salvo que NO hay "recientes": publicar no genera un informe ad-hoc, escribe en el Catálogo. ``form`` son
    SOLO los campos no sensibles de la última ejecución de ESTA sesión (compartidos con el Assessment: mismas credenciales de
    tenant, nunca el secreto)."""
    enabled = tool_enabled(stem)
    can = bool(enabled and can_execute(request))
    values: dict[str, str] = {}
    if can:
        values = asd.load_form_context(asd.session_key(request))
    selected = values.get("features")
    checked = list(asd.OPTIONAL_FEATURES) if selected is None else [f for f in asd.OPTIONAL_FEATURES if f in selected.split(",")]
    options = [{"value": f, "label": asd.FEATURE_LABELS[f], "checked": f in checked} for f in asd.OPTIONAL_FEATURES]
    from webapp.services import datasphere_profiles
    return {"enabled": enabled, "can_execute": can, "stem": stem, "form": values, "feature_options": options,
            "owner_profiles_url": datasphere_profiles.selector_url(stem, can)}


# ───────────────────────────── single-flight (espacio de claves propio) ─────────────────────────────
_LOCKS_GUARD = threading.Lock()
_RUNNING: dict[tuple[str, str, str], threading.Lock] = {}


def _lock_for(values: asd.FormValues) -> threading.Lock:
    key = (values.client.lower(), values.space.lower(), values.root_object.lower())
    with _LOCKS_GUARD:
        return _RUNNING.setdefault(key, threading.Lock())


def _mirror_up_enabled() -> bool:
    """Subir a DigitalOcean Spaces (persistencia remota) está APAGADO por defecto: publicar regenera el Catálogo local
    (``generate_catalog=True``), que es lo que la UI necesita mostrar. El espejo remoto se habilita explícitamente por entorno."""
    return os.getenv("DANWOR_PUBLICATION_MIRROR_UP", "").strip().lower() in {"1", "true", "yes", "on"}


def _canonical_roots() -> tuple[Path, Path]:
    """Raíces CANÓNICAS del Catálogo (las MISMAS que usa ``refresh_catalog`` del Assessment y el CLI): el directorio del Catálogo y
    su pool CSN. A diferencia del Assessment (workspace efímero por operación), publicar escribe acá a propósito."""
    catalog_root = Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(config.BASE_DIR / "catalog_local")).resolve()
    csn_cache = Path(os.getenv("DANWOR_CATALOG_CSN_CACHE", "").strip() or str(catalog_root / ".csn_cache")).resolve()
    return catalog_root, csn_cache


# ───────────────────────────── puente PUBLICAR → assessment canónico ─────────────────────────────
def register_assessment(values: asd.FormValues) -> list[tuple[str, str]]:
    """Registra el assessment CANÓNICO del modelo recién publicado. UNA adquisición, dos registros: la evidencia (CSN) que la
    extracción ya dejó en el pool canónico alimenta el catálogo Y este assessment; NO reconecta a Datasphere ni vuelve a extraer.

    Reusa SIN cambios la integración del Catálogo (``assessment_service.generate``: misma resolución entrada → AM terminal, misma
    llave de vigencia, mismo motor y misma caché operativa ``(cliente, AM)``). Es determinista y sin LLM. NUNCA lanza ni hace fallar la
    publicación: si algo falla, el modelo queda publicado y su assessment en el estado estable que corresponda (p. ej. FAILED →
    reintentable desde el Catálogo). Devuelve ``[(entry_id, estado)]`` de las entradas del Catálogo que contienen el modelo."""
    out: list[tuple[str, str]] = []
    try:
        from webapp.services import assessment_service as assess, catalog_reads
        _mod, entries = catalog_reads._catalog_entries()
        client, root = values.client.strip().lower(), values.root_object.strip()
        for entry in entries:
            if str(entry.cliente).strip().lower() == client and root in {str(a) for a in (entry.artefactos or [])}:
                out.append((entry.entry_id, assess.generate(entry).state))
    except Exception:                                                   # noqa: BLE001 — registrar el assessment jamás rompe la publicación
        log.exception("datasphere_publication: no se pudo registrar el assessment del modelo publicado")
    return out



# ───────────────────────────── contrato de resultado de la publicación SEGURA (fase 0) ─────────────────────────────
@dataclass
class PublicationOutcome(asd.Outcome):
    """``Outcome`` + el estado explícito de la publicación segura. ``state``: SUCCESS solo con ``status == COMPLETED``; PARTIAL (publicado con advertencias) para
    ``COMMITTED_UNCONFIRMED`` / ``COMMITTED_CLEANUP_DEFERRED``; FAILED para el resto. Nunca hay SUCCESS ante un estado degradado."""
    status: str = ""
    durability: str = ""
    stages: dict = field(default_factory=dict)
    deferred_reasons: list = field(default_factory=list)
    failures: list = field(default_factory=list)


STATE_PARTIAL = "PARTIAL"
TITLE_PARTIAL = "Publicación parcial"
# Mensajes estables (códigos del contrato). Jamás texto crudo del CLI, rutas ni secretos.
PUBLICATION_MESSAGES = {
    "EXTRACTION_INCOMPLETE": "La extracción desde Datasphere quedó incompleta (falló la lectura o el listado de algún objeto). No se modificó el Catálogo. Reintentá; si persiste, revisá la conexión y los permisos.",
    "POOL_IDENTITY_CONFLICT": "Un objeto ya está publicado desde otro Space o tenant con contenido distinto. No se modificó nada. Revisá el cliente, el Space y el tenant.",
    "CATALOG_BUSY": "Hay otra operación sobre el Catálogo en curso. No se modificó nada; reintentá en unos minutos.",
    "RECOVERY_REQUIRED": "Hay una operación anterior a medias que no pudo recuperarse. No se publica hasta resolverla; avisá al equipo técnico.",
    "CATALOG_GENERATION_FAILED": "La extracción terminó, pero falló la regeneración del catálogo. Se restauró el estado anterior del Catálogo.",
    "VERIFY_FAILED": "La publicación no pasó la verificación posterior. Se restauró el estado anterior del Catálogo.",
    "APPLY_FAILED": "La publicación falló al aplicarse. Se restauró el estado anterior del Catálogo.",
    "PERSISTENCE_UNCONFIRMED": "Publicado en el Catálogo local, pero la persistencia en Spaces NO está confirmada. Reintentá la publicación (es idempotente) para confirmarla.",
    "CLEANUP_DEFERRED": "Publicado, pero no se limpiaron los objetos obsoletos porque no se pudo verificar la propiedad (manifiesto ilegible o restauración incompleta). No se borró nada; reintentá más tarde.",
}


def _safe_outcome(values: asd.FormValues, result, trace: list, secrets: tuple, roots: dict, started: datetime, user_email: str, on_line, on_phase) -> "PublicationOutcome":
    """Traduce el ``ExportResult`` de la publicación segura al ``Outcome`` de la UI. SUCCESS ⇔ ``status == COMPLETED``."""
    from tools.catalog import publication_txn as txn
    code = result.error_code or ""
    common = dict(trace=trace, operation_id=result.operation_id, duration_s=result.duration_s, status=result.status, durability=result.durability,
                  stages=dict(result.stages), deferred_reasons=list(result.deferred_reasons), failures=list(result.failures))
    if result.status in txn.SUCCESS_STATUSES or result.status in txn.PARTIAL_STATUSES:
        try:                                        # el Catálogo local YA está confirmado: se invalida la caché y recién ahora se registra el assessment (post-commit)
            from webapp import main as hub_main
            hub_main._invalidate_catalog_cache()
        except Exception:                           # noqa: BLE001
            log.warning("datasphere_publication op=%s no se pudo invalidar el caché del catálogo", result.operation_id)
        if on_phase is not None:
            on_phase(PHASE_REGISTERING)
        registered = register_assessment(values)
        if on_line is not None:
            for _eid, state in registered:
                on_line(f"Assessment del modelo registrado: {state}")
        common["stages"]["assessment"] = ",".join(sorted({s for _e, s in registered})) or "SKIPPED"
        if on_phase is not None:
            on_phase(PHASE_PUBLISHED)
        duration = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        common["duration_s"] = duration
        if result.status in txn.SUCCESS_STATUSES:
            log.info("datasphere_publication op=%s user=%s client=%s space=%s model=%s status=COMPLETED durability=%s duration=%.1fs files=%s",
                     result.operation_id, user_email, values.client, values.space, values.root_object, result.durability, duration, result.exported_files)
            return PublicationOutcome("SUCCESS", stage="publish", terminal_am=values.root_object, **common)
        log.warning("datasphere_publication op=%s user=%s client=%s space=%s model=%s status=%s code=%s", result.operation_id, user_email, values.client, values.space,
                    values.root_object, result.status, code)
        return PublicationOutcome(STATE_PARTIAL, code, PUBLICATION_MESSAGES.get(code, "Publicación parcial."), stage="publish", title=TITLE_PARTIAL,
                                  terminal_am=values.root_object, **common)
    log.warning("datasphere_publication op=%s user=%s client=%s space=%s model=%s status=%s code=%s", result.operation_id, user_email, values.client, values.space,
                values.root_object, result.status, code)
    message = PUBLICATION_MESSAGES.get(code) or asd.ERROR_MESSAGES.get(code) or asd.ERROR_MESSAGES["EXTRACTION_FAILED"]
    return PublicationOutcome("FAILED", code or "EXTRACTION_FAILED", message, stage="publish", title=TITLE_PUBLISH, **common)


# ───────────────────────────── ejecución (PUBLICAR) ─────────────────────────────
def run(values: asd.FormValues, client_secret: str, *, user_email: str = "", on_line=None, on_phase=None) -> asd.Outcome:
    """Valida y ejecuta la PUBLICACIÓN: extracción + regeneración del Catálogo (MISMA función que el CLI, ``mode="publish"``,
    ``generate_catalog=True``) contra las raíces canónicas, y luego invalida la caché del Catálogo. El secreto no se almacena ni se
    devuelve. ``on_line`` recibe, en tiempo real y saneada, cada línea del extractor."""
    op_id = uuid.uuid4().hex[:10]
    errors = asd.validate(values, client_secret)
    if errors:
        return asd.Outcome("INVALID", "INVALID_PARAMETER", asd.ERROR_MESSAGES["INVALID_PARAMETER"], errors=errors, operation_id=op_id)
    if not asd.cli_available():
        log.warning("datasphere_publication op=%s status=FAILED code=DATASPHERE_CLI_UNAVAILABLE", op_id)
        return asd.Outcome("FAILED", "DATASPHERE_CLI_UNAVAILABLE", asd.ERROR_MESSAGES["DATASPHERE_CLI_UNAVAILABLE"],
                           operation_id=op_id, stage="extraction", title=TITLE_PUBLISH)
    lock = _lock_for(values)
    if not lock.acquire(blocking=False):
        return asd.Outcome("FAILED", "OPERATION_ALREADY_RUNNING", asd.ERROR_MESSAGES["OPERATION_ALREADY_RUNNING"],
                           operation_id=op_id, stage="extraction", title=TITLE_PUBLISH)
    # Formas derivadas del secreto que podrían aparecer en una línea de log (Basic = base64(id:secret), urlencoded): se redactan.
    secrets = tuple(dict.fromkeys(s for s in (
        client_secret,
        base64.b64encode(f"{values.client_id}:{client_secret}".encode("utf-8")).decode("ascii"),
        urllib.parse.quote(client_secret, safe=""), urllib.parse.quote_plus(client_secret)) if s))
    try:
        from tools.catalog import DSP_CLI_Export_Lineage as dsp
        catalog_root, csn_cache = _canonical_roots()
        catalog_root.mkdir(parents=True, exist_ok=True)
        csn_cache.mkdir(parents=True, exist_ok=True)
        from tools.catalog import publication_txn as txn
        safe = txn.safe_mode_enabled()                                   # el interruptor se lee UNA vez por operación
        try:                                                             # con o sin interruptor: un journal a medias nunca se pisa (apagar el interruptor no deja estados mixtos)
            txn.recover_pending(catalog_root)
        except txn.RecoveryError:
            return PublicationOutcome("FAILED", "RECOVERY_REQUIRED", PUBLICATION_MESSAGES["RECOVERY_REQUIRED"], operation_id=op_id, stage="publish", title=TITLE_PUBLISH,
                                      status=txn.RECOVERY_REQUIRED)
        roots = {str(catalog_root): "<catalog_local>", str(csn_cache): "<pool>", str(config.BASE_DIR): "<app>"}
        request = dsp.ExportRequest(
            host=values.host, client_id=values.client_id, client_secret=client_secret, token_url=values.token_url,
            space=values.space, root_object=values.root_object, client=values.client,
            catalog_root=catalog_root, csn_cache_dir=csn_cache,         # raíces CANÓNICAS: acá SÍ se escribe el Catálogo
            generate_catalog=True, mirror_up=(True if safe else _mirror_up_enabled()),   # seguro: la confirmación remota es OBLIGATORIA; si no, Spaces solo si se habilita
            mode="publish",                                             # forzado en el servidor: la UI no elige el modo
            features_requested=asd.requested_features(values),         # SOLO lo pedido: las dependencias las resuelve el extractor
            safe=safe,
        )

        def live(line: str) -> None:
            if on_line is not None:
                on_line(asd.sanitize(line, secrets, roots)[:2000])

        if on_phase is not None:
            on_phase(PHASE_PUBLISHING)
        started = datetime.now(timezone.utc)
        with dsp.capture_output(on_line=live) as cap:
            result = dsp.run_export(request)
        trace = asd._trace(cap.lines, secrets, roots)
        if safe:
            return _safe_outcome(values, result, trace, secrets, roots, started, user_email, on_line, on_phase)
        if not result.ok:
            code = result.error_code or "EXTRACTION_FAILED"
            log.warning("datasphere_publication op=%s user=%s client=%s space=%s model=%s status=FAILED code=%s duration=%.1fs",
                        op_id, user_email, values.client, values.space, values.root_object, code, result.duration_s)
            backend = asd._CODE_PREFIX_RE.sub("", asd.sanitize(result.message, secrets, roots)[:500], count=1)
            message = backend if (str(result.message).startswith("PRECHECK_") or code in asd.BACKEND_WORDED_CODES) \
                else asd.ERROR_MESSAGES.get(code, asd.ERROR_MESSAGES["EXTRACTION_FAILED"])
            stage = "assessment" if code in {"CATALOG_GENERATION_FAILED", "STORAGE_UPLOAD_FAILED"} else "extraction"
            return asd.Outcome("FAILED", code, message, trace=trace, operation_id=op_id, duration_s=result.duration_s,
                               stage=stage, title=TITLE_PUBLISH)
        # Publicado → invalidar la caché del Catálogo para que el nuevo dominio aparezca de inmediato.
        try:
            from webapp import main as hub_main
            hub_main._invalidate_catalog_cache()
        except Exception:                                               # noqa: BLE001
            log.warning("datasphere_publication op=%s no se pudo invalidar el caché del catálogo", op_id)
        # Mismo pool, sin reconectar: el assessment canónico del modelo publicado queda registrado junto con el Catálogo.
        if on_phase is not None:
            on_phase(PHASE_REGISTERING)
        registered = register_assessment(values)
        if on_line is not None:
            for _eid, state in registered:
                on_line(f"Assessment del modelo registrado: {state}")
        if on_phase is not None:
            on_phase(PHASE_PUBLISHED)
        duration = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        log.info("datasphere_publication op=%s user=%s client=%s space=%s model=%s status=SUCCESS duration=%.1fs files=%s",
                 op_id, user_email, values.client, values.space, values.root_object, duration, result.exported_files)
        return asd.Outcome("SUCCESS", trace=trace, operation_id=op_id, duration_s=duration, stage="publish",
                           terminal_am=values.root_object)
    except Exception as exc:                                            # noqa: BLE001 — jamás se muestra el texto crudo
        log.warning("datasphere_publication op=%s status=FAILED code=EXTRACTION_FAILED exc=%s", op_id, type(exc).__name__)
        return asd.Outcome("FAILED", "EXTRACTION_FAILED", asd.ERROR_MESSAGES["EXTRACTION_FAILED"], trace=[f"{type(exc).__name__}"],
                           operation_id=op_id, stage="extraction", title=TITLE_PUBLISH)
    finally:
        lock.release()


# Reexport para que la plantilla/el router compartan los mismos textos de campo que el Assessment.
owner_email = asd.owner_email
session_key = asd.session_key
save_form_context = asd.save_form_context
FormValues = asd.FormValues
