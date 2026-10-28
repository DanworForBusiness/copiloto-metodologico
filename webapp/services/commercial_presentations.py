"""commercial_presentations.py — Gestión Comercial → Presentaciones (V1): template gobernado, seed, instancias, validación y autorización.

ESTRATEGIA (aprobada): extracción mínima. El template Jinja (`webapp/templates/presentations/<template_id>.html`) conserva TODO el markup, CSS y JS de la presentación original; solo
`TemplateSpec.fields` (texto plano) pasa a ser contenido de la instancia. Sin renderer universal, sin bloques genéricos, sin store transversal.

TRES COSAS DISTINTAS (no se mezclan):
  * SEED (`commercial_presentations_data/<template_id>.yaml`): presentación institucional de SOLO LECTURA; solo cambia por código/deploy. Nadie la edita desde la UI/API.
  * INSTANCIA (`commercial_strategy_runtime/presentations/<uuid4>.json`): copia independiente — contenido, identidad, `owner_id` y `revision` propios; `copied_from` = procedencia.
  * TEMPLATE (`TEMPLATES`): lenguaje visual + campos permitidos + versión. Las instancias lo COMPARTEN (CSS/JS/assets externos; la instancia no los duplica).

AUTORIZACIÓN (contrato ratificado):
  * LEER / PRESENTAR por URL: `can_read_shared_user` = sesión + `auth_service.is_authorized` (ALLOWED_USERS ∪ ACADEMY_USERS). NO exige Comercial.
  * Catálogo, copia, edición: Comercial (`commercial_access.is_commercial_authorized_user`).
  * COPIAR no requiere nada más que Comercial (cualquier presentación, sin importar quién la creó). EDITAR es otra cosa: `can_edit_presentation_user` = Comercial Y no-seed Y (responsable
    de la instancia O `CONTENT_OWNER`: el MISMO gobierno de edición de contenidos que Estrategia; `APP_OWNER` NO edita contenido por ser administrador — si necesita editar, su identidad debe estar en `CONTENT_OWNER`).
    ELIMINAR: `can_delete_presentation_user` = Comercial Y no-seed Y (responsable O `APP_OWNER`): borrar es una acción ADMINISTRATIVA del workspace y `APP_OWNER` es su superusuario (`commercial_access`, decisión post-#107).
    `created_by_*` (procedencia histórica) y `owner_id` (responsabilidad editable) están separados aunque inicialmente coincidan.
  * Sin variables, roles ni allowlists nuevos: se reusan `commercial_access`, `auth_service.is_authorized` y `auth_service.is_content_owner_user`.

PERSISTENCIA: el MISMO contrato que `profile_repository` (un JSON por entidad, escritura atómica, push síncrono, compensación). Vive bajo `commercial_strategy_runtime/` — ya registrado en
`config.PERSIST_PATHS` (restore/mirror_up/push_path cubren los subdirectorios) — para no tocar la infraestructura de persistencia. Es una decisión de infraestructura, no de pertenencia a Estrategia.
  * `read-only`: rechaza ANTES de tocar el disco. `local`: escritura local atómica, sin push. `remote`: push obligatorio; si no se confirma, se compensa.
  * Réplica única (ver docs/PUBLICATION_SAFE_RUNBOOK.md): el `_write_lock` es por proceso; si el número de instancias sube, revisar este módulo ANTES.

SEGURIDAD DE SALIDA: todo campo editable es texto plano, se valida (tipo, largo, sin caracteres de control) y se emite SOLO como texto de elemento con autoescape de Jinja. No hay markup, enlaces,
atributos ni URLs editables en V1; un campo en otro contexto exigiría un tipo de contexto nuevo con su propio validador y su propio test."""
from __future__ import annotations

import copy
import hashlib
import json
import struct
import zlib
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable
from uuid import uuid4

import yaml

import config
from core import remote_store
from core.content_registry import DEFAULT_PACK, resolve_pack_file
from webapp import access, commercial_access
from webapp.services import auth_service

_logger = logging.getLogger("danwor.commercial_presentations")

SCHEMA_VERSION = 1
_SEED_DIR = Path(__file__).parent / "commercial_presentations_data"
_PERSIST_SUBDIR = "presentations"
_RESTORE_WAIT_TIMEOUT_S = 15.0
# Logo institucional Danwor: el MISMO asset que Danwor Academy muestra a pantalla completa (variante de wordmark BLANCO, para fondo oscuro). Se referencia por `/pack-assets`; no se copia.
_INSTITUTIONAL_LOGO = "academy/assets/img/danwor-logo.png"
MAX_INSTANCES = 200                                   # tope global (V1): evita crecimiento sin control del directorio
MAX_TITLE = 120
MAX_LOGO_BYTES = 1024 * 1024                          # logo del cliente: PNG con fondo transparente, hasta 1 MB
MAX_LOGO_PIXELS = 1_500_000
_SQUARE_TOLERANCE = 0.10                              # QR: |ancho − alto| ≤ 10 % del lado mayor
_DEFAULT_QR = "img/38a9ed59b4.png"                    # QR institucional del template (se reemplaza por el de la copia, nunca se modifica)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_DEFAULT_PARTNER_LOGO = "img/718d5d28e4.png"           # logo de Vanture del template (header y pie): es el que reemplaza el logo del cliente

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
# Caracteres de control, separadores de línea/párrafo y controles bidi: ningún campo editable los admite (no son texto de negocio; los bidi permiten disfrazar el orden visual).
_FORBIDDEN_CHARS = re.compile("[\x00-\x1f\x7f  ‪-‮⁦-⁩]")


# ── Errores ────────────────────────────────────────────────────────────────────────────────────────────────────────────

class PresentationNotFound(Exception):
    """El id no existe (o no tiene el formato de una instancia / un seed registrado)."""


class PresentationUnavailable(Exception):
    """La presentación existe pero no se puede mostrar. `reason`: `unknown_template` (el template ya no está registrado), `newer_version` (la instancia usa una versión de template mayor que la
    soportada: no se adivina) o `seed` (seed ilegible o sin la forma del template)."""

    def __init__(self, message: str, reason: str = "unavailable"):
        super().__init__(message)
        self.reason = reason


class PresentationValidationError(Exception):
    """Payload rechazado — mensaje SEGURO para mostrar al usuario (nunca refleja el valor recibido)."""


class LogoValidationError(PresentationValidationError):
    """El archivo de logo no cumple el contrato (PNG, tamaño, transparencia). El mensaje es apto para mostrar."""


class SeedIsReadOnly(Exception):
    """Los seeds institucionales no se editan desde la UI/API."""


class StaleRevision(Exception):
    def __init__(self, current_revision: int):
        super().__init__(f"Revision desactualizada: actual es {current_revision}")
        self.current_revision = current_revision


class QuotaExceeded(Exception):
    """Se alcanzó el máximo de instancias (V1)."""


class PresentationStorageError(Exception):
    """Fallo de persistencia (disco o Spaces) — nunca silenciado."""


class PresentationConfigIncomplete(PresentationStorageError):
    """El entorno no puede persistir de forma durable (modo `read-only`, o `remote` sin credenciales): determinístico, no se reintenta."""


class DurablePushFailed(PresentationStorageError):
    """El push a Spaces no se confirmó; el local ya fue compensado a la versión anterior."""


class RemoteOutcomeUnknown(PresentationStorageError):
    """Alta cuyo resultado remoto no se pudo confirmar: el archivo local ya se retiró."""


# ── Template (registro) ─────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FieldSpec:
    key: str
    section: str
    label: str
    max_len: int
    kind: str = "line"            # "line" (título/etiqueta) | "paragraph" (texto corrido): hoy ambos son texto plano de una línea


@dataclass(frozen=True)
class TemplateSpec:
    template_id: str
    version: int
    label: str
    jinja: str                    # relativo a webapp/templates
    assets_dir: str               # relativo a assets/ del content pack
    fields: tuple[FieldSpec, ...]
    css: str = "vanture.css"       # archivos COMPARTIDOS de assets_dir (la instancia no los duplica)
    js: str = "vanture.js"
    upgrade: Callable[[dict, int], dict] = lambda content, from_version: content   # cambios ROTOS: sube `version` y transforma el contenido (en memoria)

    @property
    def field_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields)

    def field(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)


_SLIDE = {
    1: "Slide 1 · Portada", 2: "Slide 2 · El límite no es la IA", 3: "Slide 3 · Qué es SAP BDC", 4: "Slide 4 · Knowledge Core", 5: "Slide 5 · Ecosistema abierto", 6: "Slide 6 · Casos de uso",
    7: "Slide 7 · Por qué BDC", 8: "Slide 8 · Cómo empezar", 9: "Slide 9 · Tu punto de partida", 10: "Slide 10 · Conclusión y demo", 11: "Slide 11 · Por qué nuestra alianza", 12: "Slide 12 · Clientes y casos",
}

# Alcance de la edición: el CONTENIDO textual y los valores comerciales de las 12 slides. Los diagramas (arquitectura BDC, stack Knowledge Core, flujo del ecosistema), el logo-marquee, los números/íconos, el pie legal y
# el cromo de la UI los gobierna el template (ver docs/COMMERCIAL_PRESENTATIONS.md): no son campos. `kind`: "line" = título/etiqueta (una línea); "paragraph" = texto corrido (hoy también de una línea: el servidor
# no admite saltos y ningún texto del seed los necesita; habilitar saltos controlados sería un cambio local al validador y al render de los "paragraph").
VANTURE_FIELDS = (
    # Campos de la v1 original (claves y límites fijados por PINNED_V1: solo pueden crecer)
    FieldSpec("hero_title_prefix", _SLIDE[1], "Título — inicio", 120),
    FieldSpec("hero_title_accent", _SLIDE[1], "Título — palabra destacada", 40),
    FieldSpec("hero_lead", _SLIDE[1], "Bajada", 400, "paragraph"),
    FieldSpec("s2_stat_value", _SLIDE[2], "Dato — valor", 20),
    FieldSpec("s2_stat_label", _SLIDE[2], "Dato — descripción", 200, "paragraph"),
    FieldSpec("cta_title_prefix", _SLIDE[10], "Título — inicio", 120),
    FieldSpec("cta_title_accent", _SLIDE[10], "Título — frase destacada", 60),
    FieldSpec("cta_lead", _SLIDE[10], "Bajada", 300, "paragraph"),
    FieldSpec("cta_qr_caption", _SLIDE[10], "Texto junto al QR", 120),
    *(f for i in range(1, 5) for f in (
        FieldSpec(f"alliance_{i}_title", _SLIDE[11], f"Propuesta {i} — título", 80),
        FieldSpec(f"alliance_{i}_body", _SLIDE[11], f"Propuesta {i} — texto", 300, "paragraph"),
    )),
    FieldSpec("recognition_intro", _SLIDE[11], "Reconocimientos — inicio", 300, "paragraph"),
    FieldSpec("recognition_highlight", _SLIDE[11], "Reconocimientos — frase destacada", 80),
    FieldSpec("recognition_tail", _SLIDE[11], "Reconocimientos — continuación", 400, "paragraph"),
    # Campos agregados después (aditivos: las instancias anteriores caen al valor del seed)
    # Slide 1
    FieldSpec("hero_kicker", _SLIDE[1], "Etiqueta superior (dorada)", 80),
    # Slide 2
    FieldSpec("s2_nav", _SLIDE[2], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s2_title", _SLIDE[2], "Título", 180),
    FieldSpec("s2_lead", _SLIDE[2], "Subtítulo", 300, "paragraph"),
    FieldSpec("s2_cards_label", _SLIDE[2], "Rótulo sobre las tarjetas", 80),
    FieldSpec("s2_card1_title", _SLIDE[2], "Tarjeta 1 — título", 160),
    FieldSpec("s2_card1_body", _SLIDE[2], "Tarjeta 1 — texto", 300, "paragraph"),
    FieldSpec("s2_card2_title", _SLIDE[2], "Tarjeta 2 — título", 160),
    FieldSpec("s2_card2_body", _SLIDE[2], "Tarjeta 2 — texto", 300, "paragraph"),
    FieldSpec("s2_card3_title", _SLIDE[2], "Tarjeta 3 — título", 160),
    FieldSpec("s2_card3_body", _SLIDE[2], "Tarjeta 3 — texto", 300, "paragraph"),
    FieldSpec("s2_next", _SLIDE[2], "Enlace «Siguiente»", 80),
    # Slide 3
    FieldSpec("s3_nav", _SLIDE[3], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s3_title", _SLIDE[3], "Título", 160),
    FieldSpec("s3_lead", _SLIDE[3], "Subtítulo", 350, "paragraph"),
    FieldSpec("s3_lead_highlight", _SLIDE[3], "Subtítulo — frase destacada", 80),
    FieldSpec("s3_callout_intro", _SLIDE[3], "Cierre — inicio", 300, "paragraph"),
    FieldSpec("s3_callout_highlight", _SLIDE[3], "Cierre — frase destacada", 80),
    FieldSpec("s3_callout_tail", _SLIDE[3], "Cierre — continuación", 480, "paragraph"),
    FieldSpec("s3_next", _SLIDE[3], "Enlace «Siguiente»", 80),
    # Slide 4
    FieldSpec("s4_nav", _SLIDE[4], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s4_title", _SLIDE[4], "Título", 160),
    FieldSpec("s4_lead_1", _SLIDE[4], "Subtítulo — inicio", 300, "paragraph"),
    FieldSpec("s4_lead_em_1", _SLIDE[4], "Subtítulo — primer énfasis", 80),
    FieldSpec("s4_lead_2", _SLIDE[4], "Subtítulo — medio", 300, "paragraph"),
    FieldSpec("s4_lead_em_2", _SLIDE[4], "Subtítulo — segundo énfasis", 80),
    FieldSpec("s4_lead_3", _SLIDE[4], "Subtítulo — final", 300, "paragraph"),
    FieldSpec("s4_diagram_label", _SLIDE[4], "Rótulo sobre el diagrama", 80),
    FieldSpec("s4_callout_text", _SLIDE[4], "Cierre — texto", 580, "paragraph"),
    FieldSpec("s4_callout_highlight", _SLIDE[4], "Cierre — frase destacada", 300, "paragraph"),
    FieldSpec("s4_next", _SLIDE[4], "Enlace «Siguiente»", 80),
    # Slide 5
    FieldSpec("s5_nav", _SLIDE[5], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s5_title", _SLIDE[5], "Título", 160),
    FieldSpec("s5_lead", _SLIDE[5], "Subtítulo", 520, "paragraph"),
    FieldSpec("s5_cards_label", _SLIDE[5], "Rótulo sobre las tarjetas", 80),
    FieldSpec("s5_card1_title", _SLIDE[5], "Tarjeta 1 — título", 160),
    FieldSpec("s5_card1_body", _SLIDE[5], "Tarjeta 1 — texto", 300, "paragraph"),
    FieldSpec("s5_card2_title", _SLIDE[5], "Tarjeta 2 — título", 160),
    FieldSpec("s5_card2_body", _SLIDE[5], "Tarjeta 2 — texto", 300, "paragraph"),
    FieldSpec("s5_card3_title", _SLIDE[5], "Tarjeta 3 — título", 160),
    FieldSpec("s5_card3_body", _SLIDE[5], "Tarjeta 3 — texto", 300, "paragraph"),
    FieldSpec("s5_next", _SLIDE[5], "Enlace «Siguiente»", 80),
    # Slide 6
    FieldSpec("s6_nav", _SLIDE[6], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s6_title", _SLIDE[6], "Título", 160),
    FieldSpec("s6_lead", _SLIDE[6], "Subtítulo", 300, "paragraph"),
    FieldSpec("s6_label", _SLIDE[6], "Rótulo sobre las solapas", 80),
    FieldSpec("s6_cfo_tab", _SLIDE[6], "CFO — nombre de la solapa", 80),
    FieldSpec("s6_coo_tab", _SLIDE[6], "COO — nombre de la solapa", 80),
    FieldSpec("s6_chro_tab", _SLIDE[6], "CHRO — nombre de la solapa", 80),
    FieldSpec("s6_cro_tab", _SLIDE[6], "CRO — nombre de la solapa", 80),
    FieldSpec("s6_cpo_tab", _SLIDE[6], "CPO — nombre de la solapa", 80),
    FieldSpec("s6_cfo_head", _SLIDE[6], "CFO — cabecera del panel", 90),
    FieldSpec("s6_cfo_uc_label", _SLIDE[6], "CFO — rótulo de casos de uso", 80),
    FieldSpec("s6_cfo_uc_1", _SLIDE[6], "CFO — caso de uso 1", 80),
    FieldSpec("s6_cfo_uc_2", _SLIDE[6], "CFO — caso de uso 2", 80),
    FieldSpec("s6_cfo_badge", _SLIDE[6], "CFO — insignia", 100),
    FieldSpec("s6_cfo_products_label", _SLIDE[6], "CFO — rótulo de productos de datos", 80),
    FieldSpec("s6_cfo_prod_1", _SLIDE[6], "CFO — producto de datos 1", 80),
    FieldSpec("s6_cfo_prod_2", _SLIDE[6], "CFO — producto de datos 2", 80),
    FieldSpec("s6_cfo_prod_3", _SLIDE[6], "CFO — producto de datos 3", 80),
    FieldSpec("s6_cfo_prod_4", _SLIDE[6], "CFO — producto de datos 4", 80),
    FieldSpec("s6_cfo_prod_5", _SLIDE[6], "CFO — producto de datos 5", 80),
    FieldSpec("s6_cfo_prod_6", _SLIDE[6], "CFO — producto de datos 6", 80),
    FieldSpec("s6_cfo_prod_7", _SLIDE[6], "CFO — producto de datos 7", 80),
    FieldSpec("s6_coo_head", _SLIDE[6], "COO — cabecera del panel", 80),
    FieldSpec("s6_coo_uc_label", _SLIDE[6], "COO — rótulo de casos de uso", 80),
    FieldSpec("s6_coo_uc_1", _SLIDE[6], "COO — caso de uso 1", 80),
    FieldSpec("s6_coo_uc_2", _SLIDE[6], "COO — caso de uso 2", 80),
    FieldSpec("s6_coo_badge", _SLIDE[6], "COO — insignia", 80),
    FieldSpec("s6_coo_products_label", _SLIDE[6], "COO — rótulo de productos de datos", 80),
    FieldSpec("s6_coo_prod_1", _SLIDE[6], "COO — producto de datos 1", 80),
    FieldSpec("s6_coo_prod_2", _SLIDE[6], "COO — producto de datos 2", 80),
    FieldSpec("s6_coo_prod_3", _SLIDE[6], "COO — producto de datos 3", 80),
    FieldSpec("s6_coo_prod_4", _SLIDE[6], "COO — producto de datos 4", 80),
    FieldSpec("s6_chro_head", _SLIDE[6], "CHRO — cabecera del panel", 80),
    FieldSpec("s6_chro_uc_label", _SLIDE[6], "CHRO — rótulo de casos de uso", 80),
    FieldSpec("s6_chro_uc_1", _SLIDE[6], "CHRO — caso de uso 1", 80),
    FieldSpec("s6_chro_uc_2", _SLIDE[6], "CHRO — caso de uso 2", 80),
    FieldSpec("s6_chro_uc_3", _SLIDE[6], "CHRO — caso de uso 3", 80),
    FieldSpec("s6_chro_uc_4", _SLIDE[6], "CHRO — caso de uso 4", 80),
    FieldSpec("s6_chro_products_label", _SLIDE[6], "CHRO — rótulo de productos de datos", 80),
    FieldSpec("s6_chro_prod_1", _SLIDE[6], "CHRO — producto de datos 1", 80),
    FieldSpec("s6_chro_prod_2", _SLIDE[6], "CHRO — producto de datos 2", 80),
    FieldSpec("s6_chro_prod_3", _SLIDE[6], "CHRO — producto de datos 3", 80),
    FieldSpec("s6_chro_prod_4", _SLIDE[6], "CHRO — producto de datos 4", 80),
    FieldSpec("s6_chro_prod_5", _SLIDE[6], "CHRO — producto de datos 5", 80),
    FieldSpec("s6_cro_head", _SLIDE[6], "CRO — cabecera del panel", 80),
    FieldSpec("s6_cro_uc_label", _SLIDE[6], "CRO — rótulo de casos de uso", 80),
    FieldSpec("s6_cro_uc_1", _SLIDE[6], "CRO — caso de uso 1", 80),
    FieldSpec("s6_cro_uc_2", _SLIDE[6], "CRO — caso de uso 2", 90),
    FieldSpec("s6_cro_badge", _SLIDE[6], "CRO — insignia", 80),
    FieldSpec("s6_cro_products_label", _SLIDE[6], "CRO — rótulo de productos de datos", 80),
    FieldSpec("s6_cro_prod_1", _SLIDE[6], "CRO — producto de datos 1", 80),
    FieldSpec("s6_cro_prod_2", _SLIDE[6], "CRO — producto de datos 2", 80),
    FieldSpec("s6_cro_prod_3", _SLIDE[6], "CRO — producto de datos 3", 80),
    FieldSpec("s6_cro_prod_4", _SLIDE[6], "CRO — producto de datos 4", 80),
    FieldSpec("s6_cpo_head", _SLIDE[6], "CPO — cabecera del panel", 80),
    FieldSpec("s6_cpo_uc_label", _SLIDE[6], "CPO — rótulo de casos de uso", 80),
    FieldSpec("s6_cpo_uc_1", _SLIDE[6], "CPO — caso de uso 1", 80),
    FieldSpec("s6_cpo_badge", _SLIDE[6], "CPO — insignia", 80),
    FieldSpec("s6_cpo_products_label", _SLIDE[6], "CPO — rótulo de productos de datos", 80),
    FieldSpec("s6_cpo_prod_1", _SLIDE[6], "CPO — producto de datos 1", 80),
    FieldSpec("s6_cpo_prod_2", _SLIDE[6], "CPO — producto de datos 2", 80),
    FieldSpec("s6_cpo_prod_3", _SLIDE[6], "CPO — producto de datos 3", 80),
    FieldSpec("s6_cpo_prod_4", _SLIDE[6], "CPO — producto de datos 4", 80),
    FieldSpec("s6_cpo_prod_5", _SLIDE[6], "CPO — producto de datos 5", 80),
    FieldSpec("s6_card1_eyebrow", _SLIDE[6], "Tarjeta 1 — rótulo", 80),
    FieldSpec("s6_card1_title", _SLIDE[6], "Tarjeta 1 — título", 160),
    FieldSpec("s6_card1_body", _SLIDE[6], "Tarjeta 1 — texto", 520, "paragraph"),
    FieldSpec("s6_card2_eyebrow", _SLIDE[6], "Tarjeta 2 — rótulo", 80),
    FieldSpec("s6_card2_title", _SLIDE[6], "Tarjeta 2 — título", 160),
    FieldSpec("s6_card2_body", _SLIDE[6], "Tarjeta 2 — texto", 510, "paragraph"),
    FieldSpec("s6_card3_eyebrow", _SLIDE[6], "Tarjeta 3 — rótulo", 80),
    FieldSpec("s6_card3_title", _SLIDE[6], "Tarjeta 3 — título", 160),
    FieldSpec("s6_card3_body", _SLIDE[6], "Tarjeta 3 — texto", 500, "paragraph"),
    FieldSpec("s6_next", _SLIDE[6], "Enlace «Siguiente»", 80),
    # Slide 7
    FieldSpec("s7_nav", _SLIDE[7], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s7_title", _SLIDE[7], "Título", 160),
    FieldSpec("s7_lead", _SLIDE[7], "Subtítulo", 300, "paragraph"),
    FieldSpec("s7_label", _SLIDE[7], "Rótulo sobre los pilares", 80),
    FieldSpec("s7_hint", _SLIDE[7], "Indicación sobre los pilares", 110),
    FieldSpec("s7_p1_title", _SLIDE[7], "Pilar 1 — título", 160),
    FieldSpec("s7_p1_claim_1", _SLIDE[7], "Pilar 1 — afirmación 1", 80),
    FieldSpec("s7_p1_claim_2", _SLIDE[7], "Pilar 1 — afirmación 2", 80),
    FieldSpec("s7_p1_claim_3", _SLIDE[7], "Pilar 1 — afirmación 3", 180),
    FieldSpec("s7_p1_claim_4", _SLIDE[7], "Pilar 1 — afirmación 4", 80),
    FieldSpec("s7_p2_title", _SLIDE[7], "Pilar 2 — título", 160),
    FieldSpec("s7_p2_claim_1", _SLIDE[7], "Pilar 2 — afirmación 1", 80),
    FieldSpec("s7_p2_claim_2", _SLIDE[7], "Pilar 2 — afirmación 2", 130),
    FieldSpec("s7_p2_claim_3", _SLIDE[7], "Pilar 2 — afirmación 3", 90),
    FieldSpec("s7_p2_claim_4", _SLIDE[7], "Pilar 2 — afirmación 4", 80),
    FieldSpec("s7_p3_title", _SLIDE[7], "Pilar 3 — título", 160),
    FieldSpec("s7_p3_claim_1", _SLIDE[7], "Pilar 3 — afirmación 1", 80),
    FieldSpec("s7_p3_claim_2", _SLIDE[7], "Pilar 3 — afirmación 2", 80),
    FieldSpec("s7_p3_claim_3", _SLIDE[7], "Pilar 3 — afirmación 3", 110),
    FieldSpec("s7_p3_claim_4", _SLIDE[7], "Pilar 3 — afirmación 4", 80),
    FieldSpec("s7_p4_title", _SLIDE[7], "Pilar 4 — título", 160),
    FieldSpec("s7_p4_claim_1", _SLIDE[7], "Pilar 4 — afirmación 1", 80),
    FieldSpec("s7_p4_claim_2", _SLIDE[7], "Pilar 4 — afirmación 2", 90),
    FieldSpec("s7_p4_claim_3", _SLIDE[7], "Pilar 4 — afirmación 3", 90),
    FieldSpec("s7_p4_claim_4", _SLIDE[7], "Pilar 4 — afirmación 4", 90),
    FieldSpec("s7_next", _SLIDE[7], "Enlace «Siguiente»", 80),
    # Slide 8
    FieldSpec("s8_nav", _SLIDE[8], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s8_title", _SLIDE[8], "Título", 160),
    FieldSpec("s8_lead", _SLIDE[8], "Subtítulo", 300, "paragraph"),
    FieldSpec("s8_label", _SLIDE[8], "Rótulo sobre los pasos", 80),
    FieldSpec("s8_step1_title", _SLIDE[8], "Paso 1 — título", 160),
    FieldSpec("s8_step1_body", _SLIDE[8], "Paso 1 — texto", 300, "paragraph"),
    FieldSpec("s8_step2_title", _SLIDE[8], "Paso 2 — título", 160),
    FieldSpec("s8_step2_body", _SLIDE[8], "Paso 2 — texto", 300, "paragraph"),
    FieldSpec("s8_step3_title", _SLIDE[8], "Paso 3 — título", 160),
    FieldSpec("s8_step3_body", _SLIDE[8], "Paso 3 — texto", 300, "paragraph"),
    FieldSpec("s8_step4_title", _SLIDE[8], "Paso 4 — título", 160),
    FieldSpec("s8_step4_body", _SLIDE[8], "Paso 4 — texto", 300, "paragraph"),
    FieldSpec("s8_next", _SLIDE[8], "Enlace «Siguiente»", 80),
    # Slide 9
    FieldSpec("s9_nav", _SLIDE[9], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s9_title", _SLIDE[9], "Título", 160),
    FieldSpec("s9_lead", _SLIDE[9], "Subtítulo", 360, "paragraph"),
    FieldSpec("s9_label", _SLIDE[9], "Rótulo sobre los caminos", 100),
    FieldSpec("s9_path1_eyebrow", _SLIDE[9], "Camino 1 — rótulo", 80),
    FieldSpec("s9_path1_title", _SLIDE[9], "Camino 1 — título", 160),
    FieldSpec("s9_path1_body", _SLIDE[9], "Camino 1 — texto", 390, "paragraph"),
    FieldSpec("s9_path1_when_label", _SLIDE[9], "Camino 1 — rótulo «cuándo conviene»", 80),
    FieldSpec("s9_path1_when", _SLIDE[9], "Camino 1 — cuándo conviene", 310, "paragraph"),
    FieldSpec("s9_path2_eyebrow", _SLIDE[9], "Camino 2 — rótulo", 80),
    FieldSpec("s9_path2_title", _SLIDE[9], "Camino 2 — título", 160),
    FieldSpec("s9_path2_body", _SLIDE[9], "Camino 2 — texto", 300, "paragraph"),
    FieldSpec("s9_path2_when_label", _SLIDE[9], "Camino 2 — rótulo «cuándo conviene»", 80),
    FieldSpec("s9_path2_when", _SLIDE[9], "Camino 2 — cuándo conviene", 300, "paragraph"),
    FieldSpec("s9_path3_eyebrow", _SLIDE[9], "Camino 3 — rótulo", 80),
    FieldSpec("s9_path3_title", _SLIDE[9], "Camino 3 — título", 160),
    FieldSpec("s9_path3_body", _SLIDE[9], "Camino 3 — texto", 340, "paragraph"),
    FieldSpec("s9_path3_when_label", _SLIDE[9], "Camino 3 — rótulo «cuándo conviene»", 80),
    FieldSpec("s9_path3_when", _SLIDE[9], "Camino 3 — cuándo conviene", 300, "paragraph"),
    FieldSpec("s9_callout_intro", _SLIDE[9], "Cierre — inicio", 300, "paragraph"),
    FieldSpec("s9_callout_highlight", _SLIDE[9], "Cierre — frase destacada", 300, "paragraph"),
    FieldSpec("s9_callout_tail", _SLIDE[9], "Cierre — continuación", 300, "paragraph"),
    FieldSpec("s9_next", _SLIDE[9], "Enlace «Siguiente»", 80),
    # Slide 10
    FieldSpec("s10_nav", _SLIDE[10], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s10_next", _SLIDE[10], "Enlace «Siguiente»", 90),
    # Slide 11
    FieldSpec("s11_nav", _SLIDE[11], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s11_title", _SLIDE[11], "Título", 160),
    FieldSpec("recognition_title", _SLIDE[11], "Reconocimientos — rótulo", 160),
    FieldSpec("s11_next", _SLIDE[11], "Enlace «Siguiente»", 80),
    # Slide 12
    FieldSpec("s12_nav", _SLIDE[12], "Nombre de la sección (etiqueta superior y riel)", 80),
    FieldSpec("s12_title", _SLIDE[12], "Título", 160),
)

# Slides que una INSTANCIA puede quitar de su copia (todas menos la portada). La instancia guarda solo los ids (`removed_slides`), nunca markup: el template sigue siendo la fuente de la composición.
# La numeración («NN / total»), el riel y los «Siguiente» se recalculan sobre las visibles.
NUMBERED_SLIDES = tuple(f"s{i}" for i in range(2, 13))

# ── Add-ons: componentes gobernados, variantes y regiones ──────────────────────────────────────────────────────────────────────────────────────────────────────
# Un COMPONENTE es código compartido (macro del template + CSS de vanture.css); una VARIANTE es un set de parámetros institucional (`addons.yaml`) o el único contenido de un componente fijo; una REGIÓN es
# un lugar declarado del template con un tipo (`body`: dentro del contenedor de la slide · `bleed`: ancho completo) y UN solo ocupante. La instancia guarda solo ids: `blocks` = desvíos del ocupante por defecto
# de cada región (`None` = quitado). Compatibilidad = solo el tipo de región; ninguna regla semántica por slide.
COMPONENTS: dict[str, dict] = {
    "layer-stack": {"label": "Capas de arquitectura", "region": "body", "variants": {"bdc-architecture": "Arquitectura SAP BDC", "knowledge-core-stack": "Stack Knowledge Core"}},
    "ecosystem-flow": {"label": "Flujo del ecosistema", "region": "body", "variants": {"open-ecosystem-flow": "Flujo del ecosistema abierto"}},
    "logo-marquee": {"label": "Marquee de logos", "region": "bleed", "variants": {"partner-logo-marquee": "Logos de clientes y socios"}},
}
# región → (tipo, ocupante por defecto `componente:variante` | None). Las cuatro primeras ya existían en el template; las cinco `body` restantes nacen vacías y no emiten nada mientras lo estén.
REGIONS: dict[str, tuple[str, str | None]] = {
    "s2.body": ("body", None), "s3.body": ("body", "layer-stack:bdc-architecture"), "s4.body": ("body", "layer-stack:knowledge-core-stack"), "s5.body": ("body", "ecosystem-flow:open-ecosystem-flow"),
    "s6.body": ("body", None), "s7.body": ("body", None), "s8.body": ("body", None), "s9.body": ("body", None), "s12.bleed": ("bleed", "logo-marquee:partner-logo-marquee"),
}


def _ref(component: str, variant: str) -> str:
    return f"{component}:{variant}"


def _split_ref(ref: str) -> tuple[str, str]:
    component, _, variant = ref.partition(":")
    return component, variant


def region_slide(region_id: str) -> str:
    return region_id.split(".")[0]


def validate_blocks(value) -> dict:
    """`blocks`: mapa región → `None` (quitado) | `{component, variant}`. Solo regiones declaradas, componentes y variantes registrados y compatibles con el TIPO de la región; se normaliza a los desvíos del defecto
    (lo que coincide con el ocupante por defecto no se guarda). Rechazo atómico y mensajes que no reflejan el valor recibido."""
    if not isinstance(value, dict):
        raise PresentationValidationError("La composición debe ser un mapa de regiones")
    out: dict[str, dict | None] = {}
    for region_id, occupant in value.items():
        if not isinstance(region_id, str) or region_id not in REGIONS:
            raise PresentationValidationError("Se indicó una región que no existe")
        rtype, default = REGIONS[region_id]
        if occupant is None:
            if default is not None:
                out[region_id] = None
            continue
        if not isinstance(occupant, dict) or set(occupant) != {"component", "variant"} or not all(isinstance(v, str) for v in occupant.values()):
            raise PresentationValidationError("El componente indicado no es válido")
        component, variant = occupant["component"], occupant["variant"]
        spec = COMPONENTS.get(component)
        if spec is None or variant not in spec["variants"]:
            raise PresentationValidationError("El componente indicado no está en la biblioteca")
        if spec["region"] != rtype:
            raise PresentationValidationError("Ese componente no es compatible con la región elegida")
        if _ref(component, variant) != default:
            out[region_id] = {"component": component, "variant": variant}
    return {rid: out[rid] for rid in REGIONS if rid in out}


def resolve_regions(blocks: dict | None) -> dict[str, dict]:
    """Estado efectivo de cada región: `{type, default, occupant}` con `default`/`occupant` = `(componente, variante)` | None."""
    blocks = blocks or {}
    out = {}
    for rid, (rtype, default) in REGIONS.items():
        d = _split_ref(default) if default else None
        if rid in blocks:
            o = None if blocks[rid] is None else (blocks[rid]["component"], blocks[rid]["variant"])
        else:
            o = d
        out[rid] = {"type": rtype, "default": d, "occupant": o}
    return out


def component_library() -> list[dict]:
    """Biblioteca de composiciones REALES y registradas (nada más), con el tipo de región al que calzan."""
    return [{"component": c, "variant": v, "label": label, "region": spec["region"], "ref": _ref(c, v)}
            for c, spec in COMPONENTS.items() for v, label in spec["variants"].items()]


@lru_cache(maxsize=None)
def _addons() -> dict:
    """Parámetros de las variantes institucionales de `layer-stack` (`addons.yaml`), validados al cargar: tonos y rangos cerrados, textos no vacíos, sin caracteres de control."""
    path = _SEED_DIR / "addons.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PresentationUnavailable("Variantes de add-ons ilegibles", "seed") from exc
    stacks = data.get("layer-stack") if isinstance(data, dict) else None
    if not isinstance(stacks, dict) or set(stacks) != set(COMPONENTS["layer-stack"]["variants"]):
        raise PresentationUnavailable("Variantes de add-ons sin la forma esperada", "seed")

    def text(v, *, required=True):
        ok = isinstance(v, str) and (v.strip() or not required) and not _FORBIDDEN_CHARS.search(v) and len(v) <= 120
        if not ok:
            raise PresentationUnavailable("Variantes de add-ons con un texto inválido", "seed")
        return v

    for variant, v in stacks.items():
        if set(v) != {"label", "sides", "connectors", "layers"} or not isinstance(v["connectors"], bool) or not isinstance(v["layers"], list) or not v["layers"]:
            raise PresentationUnavailable("Variantes de add-ons sin la forma esperada", "seed")
        text(v["label"])
        if not isinstance(v["sides"], list) or len(v["sides"]) not in (0, 2):
            raise PresentationUnavailable("Variantes de add-ons con laterales inválidos", "seed")
        [text(x) for x in v["sides"]]
        for layer in v["layers"]:
            if set(layer) - {"tone", "label", "sub", "reveal", "boxes"} or layer.get("tone") not in ("light", "strong", "neutral") or not isinstance(layer.get("reveal"), int) or not 5 <= layer["reveal"] <= 60:
                raise PresentationUnavailable("Variantes de add-ons con una capa inválida", "seed")
            text(layer["label"])
            if "sub" in layer:
                text(layer["sub"])
            if not isinstance(layer.get("boxes"), list) or not 1 <= len(layer["boxes"]) <= 6:
                raise PresentationUnavailable("Variantes de add-ons con cajas inválidas", "seed")
            for box in layer["boxes"]:
                if set(box) - {"text", "note", "tone"} or box.get("tone") not in ("gold", "neutral"):
                    raise PresentationUnavailable("Variantes de add-ons con una caja inválida", "seed")
                text(box["text"])
                if "note" in box:
                    text(box["note"])
    return stacks

TEMPLATES: dict[str, TemplateSpec] = {
    "vanture-bdc": TemplateSpec(
        template_id="vanture-bdc", version=1, label="Vanture · La empresa autónoma comienza con los datos",
        jinja="presentations/vanture-bdc.html", assets_dir="presentations/vanture-bdc", fields=VANTURE_FIELDS,
    ),
}


def is_seed_id(presentation_id: str) -> bool:
    return presentation_id in TEMPLATES


def is_instance_id(presentation_id: str) -> bool:
    return bool(_UUID_RE.match(presentation_id or ""))


def template_spec(template_id: str) -> TemplateSpec | None:
    return TEMPLATES.get(template_id)


# ── Seed ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _seed_raw(template_id: str) -> dict:
    spec = TEMPLATES[template_id]
    path = _SEED_DIR / f"{template_id}.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PresentationUnavailable(f"Seed ilegible: {template_id}", "seed") from exc
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, dict) or set(content) != set(spec.field_keys) or not all(isinstance(v, str) and v.strip() for v in content.values()):
        raise PresentationUnavailable(f"Seed sin la forma del template: {template_id}", "seed")
    return data


def _seed_view(template_id: str) -> dict:
    spec, data = TEMPLATES[template_id], _seed_raw(template_id)
    return {
        "presentation_id": template_id, "template_id": template_id, "template_version": spec.version,
        "title": str(data.get("title") or spec.label), "revision": 1, "is_seed": True,
        "created_by_id": None, "created_by_label": "Danwor (institucional)", "owner_id": None, "copied_from": None,
        "created_at": None, "updated_at": None, "updated_by_id": None, "updated_by_label": None,
        "removed_slides": [], "blocks": {}, "qr_sha": None,
        "content": copy.deepcopy(data["content"]),
    }


# ── Validación ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def _text(value, *, max_len: int, label: str) -> str:
    if not isinstance(value, str):
        raise PresentationValidationError(f"{label}: se esperaba texto")
    cleaned = value.strip()
    if not cleaned:
        raise PresentationValidationError(f"{label}: no puede estar vacío")
    if len(cleaned) > max_len:
        raise PresentationValidationError(f"{label}: excede el máximo de {max_len} caracteres")
    if _FORBIDDEN_CHARS.search(cleaned):
        raise PresentationValidationError(f"{label}: contiene caracteres no permitidos")
    return cleaned


def validate_title(value) -> str:
    return _text(value, max_len=MAX_TITLE, label="Título")


def validate_removed_slides(value) -> list[str]:
    """`removed_slides`: lista (posiblemente vacía) de ids de slide quitables, sin repetidos. Se normaliza al orden del documento. Rechazo atómico: nada se descarta en silencio."""
    if not isinstance(value, list):
        raise PresentationValidationError("Las slides eliminadas deben ser una lista")
    if any(not isinstance(v, str) or v not in NUMBERED_SLIDES for v in value):
        raise PresentationValidationError("Se indicó una slide que no se puede eliminar")
    if len(set(value)) != len(value):
        raise PresentationValidationError("Hay slides repetidas en la lista de eliminadas")
    return [s for s in NUMBERED_SLIDES if s in value]


def validate_fields(template_id: str, fields) -> dict:
    """Valida un SUBCONJUNTO de campos editables. Rechazo atómico: ante cualquier problema no se devuelve nada. Una clave que no es de la spec se rechaza (no se descarta en silencio) y su
    nombre no se refleja en el mensaje."""
    spec = TEMPLATES.get(template_id)
    if spec is None:
        raise PresentationUnavailable("Template no disponible", "unknown_template")
    if not isinstance(fields, dict) or not fields:
        raise PresentationValidationError("No hay campos para guardar")
    out: dict[str, str] = {}
    for key, value in fields.items():
        field = spec.field(key) if isinstance(key, str) else None
        if field is None:
            raise PresentationValidationError("Se envió un campo que no es editable")
        out[key] = _text(value, max_len=field.max_len, label=f"{field.section.split(' · ')[0]} · {field.label}")      # «Slide 6 · CFO — producto de datos 3»: el mismo rótulo «Título» existe en cada slide
    return out


def full_content(template_id: str, content: dict | None) -> dict:
    """Contenido COMPLETO para renderizar: solo claves de la spec; las que falten (campo agregado después) caen al valor del seed. Nunca claves ajenas."""
    spec = TEMPLATES[template_id]
    seed = _seed_raw(template_id)["content"]
    content = content or {}
    return {k: (content[k] if isinstance(content.get(k), str) and content[k].strip() else seed[k]) for k in spec.field_keys}


# ── Persistencia (contrato de profile_repository) ─────────────────────────────────────────────────────────────────────────

_write_lock = threading.Lock()


def _dir() -> Path:
    return config.COMMERCIAL_STRATEGY_RUNTIME_DIR / _PERSIST_SUBDIR


def _path(presentation_id: str) -> Path:
    return _dir() / f"{presentation_id}.json"


def _logo_path(presentation_id: str) -> Path:
    return _dir() / f"{presentation_id}.logo.png"


def _qr_path(presentation_id: str) -> Path:
    return _dir() / f"{presentation_id}.qr.png"


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def _has_transparent_pixel(width: int, height: int, color_type: int, depth: int, idat: bytes, trns: bytes | None) -> bool | None:
    """True/False si pudo decodificar y buscar un píxel con alfa < 255 (se detiene en el primero: un logo con fondo transparente lo trae en la primera fila); None si el formato no se decodifica (se acepta por su canal/`tRNS`)."""
    if color_type == 6 and depth in (8, 16):
        bpp, alpha = (4, 3) if depth == 8 else (8, 6)
        full = 255 if depth == 8 else None
    elif color_type == 4 and depth in (8, 16):
        bpp, alpha = (2, 1) if depth == 8 else (4, 2)
        full = 255 if depth == 8 else None
    elif color_type == 3 and depth == 8 and trns is not None:
        bpp, alpha, full = 1, None, 255
    else:
        return None
    row_len = width * bpp
    expected = height * (row_len + 1)
    try:
        raw = zlib.decompressobj().decompress(idat, expected + 1)
    except zlib.error:
        raise LogoValidationError("El PNG está dañado (los datos de imagen no se pueden leer).")
    if len(raw) != expected:
        raise LogoValidationError("El PNG está dañado (los datos de imagen están incompletos).")
    prev = bytearray(row_len)
    for y in range(height):
        base = y * (row_len + 1)
        ftype, line = raw[base], bytearray(raw[base + 1: base + 1 + row_len])
        if ftype == 1:
            for i in range(bpp, row_len):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ftype == 2:
            for i in range(row_len):
                line[i] = (line[i] + prev[i]) & 255
        elif ftype == 3:
            for i in range(row_len):
                line[i] = (line[i] + (((line[i - bpp] if i >= bpp else 0) + prev[i]) >> 1)) & 255
        elif ftype == 4:
            for i in range(row_len):
                line[i] = (line[i] + _paeth(line[i - bpp] if i >= bpp else 0, prev[i], prev[i - bpp] if i >= bpp else 0)) & 255
        elif ftype != 0:
            raise LogoValidationError("El PNG está dañado (filtro de imagen inválido).")
        if alpha is not None:
            if depth == 8:
                if any(a != 255 for a in line[alpha::bpp]):
                    return True
            elif any(line[i] != 255 or line[i + 1] != 255 for i in range(alpha, row_len, bpp)):
                return True
        else:                                         # paleta con tRNS: alfa por índice (los índices fuera de `tRNS` son opacos)
            if any(i < len(trns) and trns[i] != 255 for i in set(line)):
                return True
        prev = line
    return False


def validate_png(data, *, noun: str = "logo", require_alpha: bool = True, square: bool = False) -> dict:
    """Valida una imagen PNG subida (logo del cliente o QR): PNG íntegro (firma, CRC de cada bloque, IEND), ≤ 1 MB, ≤ 1,5 M de píxeles, no animado y CON TRANSPARENCIA real (canal alfa o `tRNS`, y al menos un píxel transparente cuando se puede decodificar).
    Devuelve `{width, height, sha256, bytes}`. No re-codifica nada: se guarda tal cual se subió. Lanza `LogoValidationError` con un mensaje apto para mostrar."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise LogoValidationError(f"Elegí un archivo PNG para el {noun}.")
    data = bytes(data)
    if len(data) > MAX_LOGO_BYTES:
        raise LogoValidationError(f"El {noun} supera 1 MB: exportalo más liviano.")
    if not data.startswith(_PNG_SIGNATURE):
        raise LogoValidationError(f"El {noun} debe ser un archivo .png.")
    pos, first, ihdr, idat, trns, seen_end = len(_PNG_SIGNATURE), True, None, bytearray(), None, False
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        end = pos + 8 + length
        if end + 4 > len(data):
            raise LogoValidationError("El PNG está incompleto o dañado.")
        body = data[pos + 8:end]
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != struct.unpack(">I", data[end:end + 4])[0]:
            raise LogoValidationError("El PNG está dañado (falla la verificación de integridad).")
        if first and ctype != b"IHDR":
            raise LogoValidationError("El archivo no es un PNG válido.")
        first = False
        if ctype == b"IHDR":
            if length != 13:
                raise LogoValidationError("El archivo no es un PNG válido.")
            ihdr = struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"tRNS":
            trns = body
        elif ctype == b"acTL":
            raise LogoValidationError("No se admiten PNG animados: subí un PNG estático.")
        elif ctype == b"IEND":
            seen_end = True
            break
        pos = end + 4
    if ihdr is None or not seen_end or not idat:
        raise LogoValidationError("El PNG está incompleto o dañado.")
    width, height, depth, color_type, _compression, _filter, interlace = ihdr
    if not (width and height) or width * height > MAX_LOGO_PIXELS:
        raise LogoValidationError(f"El {noun} es demasiado grande en píxeles (máximo 1,5 millones, por ejemplo 1200 × 1200).")
    if color_type not in (0, 2, 3, 4, 6):
        raise LogoValidationError("El archivo no es un PNG válido.")
    if square and abs(width - height) > _SQUARE_TOLERANCE * max(width, height):
        raise LogoValidationError(f"El {noun} debe ser cuadrado (por ejemplo 600 × 600).")
    if require_alpha:
        if color_type not in (4, 6) and trns is None:
            raise LogoValidationError("El PNG no tiene transparencia: exportalo como PNG con fondo transparente.")
        if interlace == 0:
            found = _has_transparent_pixel(width, height, color_type, depth, bytes(idat), trns)
            if found is False:
                raise LogoValidationError("El PNG no tiene ningún píxel transparente: exportalo con fondo transparente.")
    return {"width": width, "height": height, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def validate_logo_png(data) -> dict:
    """Logo del cliente: PNG con fondo transparente (ver `validate_png`)."""
    return validate_png(data, noun="logo", require_alpha=True)


def validate_qr_png(data) -> dict:
    """QR de la instancia: PNG cuadrado; a diferencia del logo NO exige transparencia (un QR suele ser opaco y el documento lo apoya sobre un tile blanco)."""
    return validate_png(data, noun="QR", require_alpha=False, square=True)


def read_logo(presentation_id: str) -> bytes:
    """Bytes del logo propio de una instancia (NotFound si no tiene)."""
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    raw = _read_raw(presentation_id)
    if not (raw.get("logo") or {}).get("sha256"):
        raise PresentationNotFound(presentation_id)
    try:
        return _logo_path(presentation_id).read_bytes()
    except FileNotFoundError as exc:
        raise PresentationNotFound(presentation_id) from exc
    except OSError as exc:
        raise PresentationStorageError(f"No se pudo leer el logo: {exc}") from exc


def read_qr(presentation_id: str) -> bytes:
    """Bytes del QR propio de una instancia (NotFound si no tiene). Mismo contrato que `read_logo`."""
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    raw = _read_raw(presentation_id)
    if not (raw.get("qr") or {}).get("sha256"):
        raise PresentationNotFound(presentation_id)
    try:
        return _qr_path(presentation_id).read_bytes()
    except FileNotFoundError as exc:
        raise PresentationNotFound(presentation_id) from exc
    except OSError as exc:
        raise PresentationStorageError(f"No se pudo leer el QR: {exc}") from exc


def _persistence_mode() -> str:
    return str(getattr(config, "PERSISTENCE_MODE", "local"))


def _local_only() -> bool:
    return _persistence_mode() == "local"


def _require_writable_mode() -> None:
    """`read-only` no habilita escrituras de negocio: se rechaza ANTES de tocar el disco."""
    if _persistence_mode() == "read-only":
        raise PresentationConfigIncomplete("Las presentaciones están en modo de solo lectura en este entorno: no se pueden guardar cambios.")


def _ensure_restored() -> None:
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        raise PresentationStorageError(f"No se pudo escribir la presentación en disco: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".png")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        raise PresentationStorageError(f"No se pudo escribir el logo en disco: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _cleanup_failed_write(path: Path) -> None:
    """Retira un alta cuya durabilidad remota nunca se confirmó (que ni `list()` ni un `mirror_up()` posterior la completen a escondidas). Best-effort: nunca enmascara el error real."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _logger.warning("commercial_presentations.cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("commercial_presentations.remote_cleanup_failed reason=%s", type(exc).__name__)


def _restore_previous(path: Path, previous_bytes: bytes) -> None:
    try:
        path.write_bytes(previous_bytes)
    except OSError as exc:
        _logger.error("commercial_presentations.rollback_failed reason=%s", type(exc).__name__)


def _tolerant_blocks(value) -> dict:
    """Lectura tolerante de `blocks` (una instancia escrita por una versión futura o dañada no rompe el render): lo inválido se ignora entrada por entrada; al GUARDAR se valida estricto."""
    if not isinstance(value, dict):
        return {}
    ok = {}
    for rid, occupant in value.items():
        try:
            ok.update(validate_blocks({rid: occupant}))
        except PresentationValidationError:
            continue
    return {rid: ok[rid] for rid in REGIONS if rid in ok}


def _instance_view(raw: dict) -> dict:
    """Instancia persistida → vista uniforme (misma forma que el seed). El contenido se normaliza contra la spec VIGENTE; una versión de template mayor que la soportada no se adivina."""
    template_id = str(raw.get("template_id") or "")
    spec = TEMPLATES.get(template_id)
    if spec is None:
        raise PresentationUnavailable("Template no disponible", "unknown_template")
    stored_version = int(raw.get("template_version") or 1)
    if stored_version > spec.version:
        raise PresentationUnavailable("La presentación usa una versión de template más nueva que la soportada", "newer_version")
    content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
    if stored_version < spec.version:
        content = spec.upgrade(dict(content), stored_version)                   # en memoria; se persiste recién en el próximo guardado
    return {
        "presentation_id": raw["presentation_id"], "template_id": template_id, "template_version": spec.version,
        "title": str(raw.get("title") or spec.label), "revision": int(raw.get("revision") or 1), "is_seed": False,
        "created_by_id": raw.get("created_by_id"), "created_by_label": raw.get("created_by_label"),
        "owner_id": raw.get("owner_id"), "copied_from": raw.get("copied_from"),
        "created_at": raw.get("created_at"), "updated_at": raw.get("updated_at"),
        "updated_by_id": raw.get("updated_by_id"), "updated_by_label": raw.get("updated_by_label"),
        "logo_sha": (raw.get("logo") or {}).get("sha256") if isinstance(raw.get("logo"), dict) else None,
        "removed_slides": [s for s in NUMBERED_SLIDES if isinstance(raw.get("removed_slides"), list) and s in raw["removed_slides"]],      # tolerante: lo desconocido se ignora al leer
        "blocks": _tolerant_blocks(raw.get("blocks")),
        "qr_sha": (raw.get("qr") or {}).get("sha256") if isinstance(raw.get("qr"), dict) else None,
        "content": full_content(template_id, content),
    }


def _read_raw(presentation_id: str) -> dict:
    _ensure_restored()
    try:
        raw = json.loads(_path(presentation_id).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PresentationNotFound(presentation_id) from exc
    except ValueError as exc:                                                    # archivo corrupto: se trata como ausente (fail-soft, mismo criterio que profile_repository)
        raise PresentationNotFound(presentation_id) from exc
    except OSError as exc:
        raise PresentationStorageError(f"No se pudo leer la presentación: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("presentation_id") != presentation_id:
        raise PresentationNotFound(presentation_id)
    return raw


def get(presentation_id: str) -> dict:
    """Vista de un seed o de una instancia. `PresentationNotFound` si el id no tiene un formato válido (no se toca el filesystem con ids arbitrarios)."""
    if is_seed_id(presentation_id):
        return _seed_view(presentation_id)
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    return _instance_view(_read_raw(presentation_id))


def _instance_ids() -> list[str]:
    _ensure_restored()
    directory = _dir()
    if not directory.exists():
        return []
    try:
        return sorted(p.stem for p in directory.iterdir() if p.is_file() and p.suffix == ".json" and is_instance_id(p.stem))
    except OSError as exc:
        raise PresentationStorageError(f"No se pudo listar las presentaciones: {exc}") from exc


def list_presentations() -> list[dict]:
    """Seeds primero (orden del registro) y luego instancias por edición descendente. Una instancia ilegible o con template no disponible no rompe el listado: aparece marcada `available=False`."""
    items = []
    for template_id in TEMPLATES:
        try:
            view = _seed_view(template_id)
            items.append({**view, "available": True})
        except PresentationUnavailable:
            _logger.error("commercial_presentations.seed_unavailable template=%s", template_id)
    instances = []
    for pid in _instance_ids():
        try:
            instances.append({**_instance_view(_read_raw(pid)), "available": True})
        except PresentationUnavailable:
            try:
                raw = _read_raw(pid)
                instances.append({
                    "presentation_id": pid, "template_id": str(raw.get("template_id") or ""), "template_version": raw.get("template_version"),
                    "title": str(raw.get("title") or pid), "revision": int(raw.get("revision") or 1), "is_seed": False,
                    "created_by_label": raw.get("created_by_label"), "owner_id": raw.get("owner_id"), "copied_from": raw.get("copied_from"),
                    "updated_at": raw.get("updated_at"), "available": False, "content": {},
                })
            except (PresentationNotFound, PresentationStorageError):
                continue
        except PresentationNotFound:
            continue
    instances.sort(key=lambda v: str(v.get("updated_at") or ""), reverse=True)
    return items + instances


def copy_presentation(source_id: str, *, title, owner_id: str, owner_label: str, logo: bytes | None = None) -> dict:
    """Copia INDEPENDIENTE de un seed o de una instancia: uuid4 nuevo, creador y responsable = quien copia, `copied_from` = procedencia, contenido snapshot (las claves de la spec, sin
    referencias al origen). Cualquier usuario de Comercial puede copiar cualquier presentación: la autorización la aplica el router; acá no se decide quién.
    `logo` (opcional): PNG con fondo transparente que reemplaza el logo de Vanture del template; se valida ANTES de escribir nada. Sin `logo`, la copia hereda el del origen si lo tiene."""
    clean_title = validate_title(title)
    logo_meta = validate_logo_png(logo) if logo else None
    source = get(source_id)                                                      # NotFound / Unavailable propagan
    spec = TEMPLATES[source["template_id"]]
    owner_id = str(owner_id or "").strip().lower()
    if not owner_id:
        raise PresentationValidationError("No se pudo identificar al usuario")
    now = _now_iso()
    new_id = str(uuid4())
    payload = {
        "schema_version": SCHEMA_VERSION, "presentation_id": new_id, "template_id": spec.template_id, "template_version": spec.version,
        "title": clean_title, "revision": 1,
        "created_by_id": owner_id, "created_by_label": str(owner_label or owner_id), "owner_id": owner_id,
        "copied_from": {"presentation_id": source["presentation_id"], "revision": source["revision"], "template_version": source["template_version"], "at": now},
        "created_at": now, "updated_at": now, "updated_by_id": owner_id, "updated_by_label": str(owner_label or owner_id),
        "content": {k: source["content"][k] for k in spec.field_keys},
    }
    if source.get("removed_slides"):
        payload["removed_slides"] = list(source["removed_slides"])               # la copia parte de la composición del origen (solo ids)
    if source.get("blocks"):
        payload["blocks"] = copy.deepcopy(source["blocks"])
    _require_writable_mode()
    path = _path(new_id)
    logo_bytes = logo
    if logo_bytes is None and not is_seed_id(source_id):
        try:
            logo_bytes = read_logo(source_id)                                    # hereda el logo propio del origen (si lo tiene)
        except PresentationNotFound:
            logo_bytes = None
    if logo_bytes:
        meta = logo_meta if (logo and logo_meta) else validate_logo_png(logo_bytes)
        payload["logo"] = {k: meta[k] for k in ("sha256", "bytes", "width", "height")}
    logo_file = _logo_path(new_id)
    qr_bytes = None
    if not is_seed_id(source_id) and source.get("qr_sha"):
        try:
            qr_bytes = read_qr(source_id)                                        # hereda el QR propio del origen (snapshot: la copia queda independiente)
            qr_meta = validate_qr_png(qr_bytes)
            payload["qr"] = {k: qr_meta[k] for k in ("sha256", "bytes", "width", "height")}
        except PresentationNotFound:
            qr_bytes = None
    qr_file = _qr_path(new_id)

    def _discard() -> None:
        _cleanup_failed_write(path)
        if logo_bytes:
            _cleanup_failed_write(logo_file)
        if qr_bytes:
            _cleanup_failed_write(qr_file)

    def _push_all() -> bool:
        return ((not logo_bytes or remote_store.push_path(logo_file)) and (not qr_bytes or remote_store.push_path(qr_file))
                and remote_store.push_path(path))                                # los assets primero: la instancia nunca aparece remotamente sin ellos

    with _write_lock:
        if len(_instance_ids()) >= MAX_INSTANCES:
            raise QuotaExceeded()
        try:
            if logo_bytes:
                _write_bytes_atomic(logo_file, logo_bytes)
            if qr_bytes:
                _write_bytes_atomic(qr_file, qr_bytes)
            _write_atomic(path, payload)
        except PresentationStorageError:
            if logo_bytes:
                _cleanup_failed_write(logo_file)
            if qr_bytes:
                _cleanup_failed_write(qr_file)
            raise
        if _local_only():
            return _instance_view(payload)
        if _push_all():
            return _instance_view(payload)
        if not remote_store.enabled():                                           # determinístico: ni este intento ni un reintento pueden confirmar en este entorno
            _discard()
            raise PresentationConfigIncomplete("Las presentaciones todavía no están configuradas para este entorno.")
        if _push_all():
            return _instance_view(payload)
        _discard()
        raise RemoteOutcomeUnknown("No pudimos confirmar si la copia se guardó. Antes de reintentar, revisá el catálogo — si ya aparece, no la vuelvas a crear.")


def authorization_view(presentation_id: str) -> dict:
    """Lo mínimo que hace falta para AUTORIZAR sobre una instancia (`is_seed`, `owner_id`), sin exigir que su template esté disponible: una instancia de un template desconocido o más nuevo
    igual se puede eliminar. NotFound si no existe."""
    if is_seed_id(presentation_id):
        return {"is_seed": True, "owner_id": ""}
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    return {"is_seed": False, "owner_id": str(_read_raw(presentation_id).get("owner_id") or "")}


def delete_presentation(presentation_id: str) -> None:
    """ELIMINA una instancia (borrado real, no recuperable desde el Hub). El seed no se elimina nunca. Quién puede hacerlo lo decide el router (`can_delete_presentation_user`). En `remote` el borrado
    en Spaces es obligatorio: si no se confirma tras un reintento, el archivo local se restaura (503) — nunca queda borrada en un lado y viva en el otro."""
    if is_seed_id(presentation_id):
        raise SeedIsReadOnly()
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    _require_writable_mode()
    with _write_lock:
        _read_raw(presentation_id)                                               # NotFound si no existe
        path = _path(presentation_id)
        previous_bytes = path.read_bytes()
        logo_file = _logo_path(presentation_id)
        previous_logo = logo_file.read_bytes() if logo_file.exists() else None
        qr_file = _qr_path(presentation_id)
        previous_qr = qr_file.read_bytes() if qr_file.exists() else None

        def _restore() -> None:
            _restore_previous(path, previous_bytes)
            if previous_logo is not None:
                _restore_previous(logo_file, previous_logo)
            if previous_qr is not None:
                _restore_previous(qr_file, previous_qr)

        try:
            path.unlink()
            if previous_logo is not None:
                logo_file.unlink()
            if previous_qr is not None:
                qr_file.unlink()
        except OSError as exc:
            _restore()
            raise PresentationStorageError(f"No se pudo eliminar la presentación del disco: {exc}") from exc
        if _local_only():
            return
        if not remote_store.enabled():                                           # determinístico: ni este intento ni un reintento pueden confirmar en este entorno
            _restore()
            raise PresentationConfigIncomplete("Las presentaciones todavía no están configuradas para este entorno.")
        if remote_store.delete_path(path) or remote_store.delete_path(path):
            if previous_logo is not None and not remote_store.delete_path(logo_file):
                _logger.warning("commercial_presentations.delete: logo remoto huérfano (id=%s)", presentation_id)   # sin la instancia el logo no es alcanzable ni listable
            if previous_qr is not None and not remote_store.delete_path(qr_file):
                _logger.warning("commercial_presentations.delete: QR remoto huérfano (id=%s)", presentation_id)
            return
        _restore()
        _logger.error("commercial_presentations.delete: el borrado en Spaces no se confirmó — local restaurado (id=%s)", presentation_id)
        raise DurablePushFailed()


def set_qr(presentation_id: str, data, *, expected_revision: int, updated_by_id: str, updated_by_label: str) -> dict:
    """Reemplaza el QR de UNA instancia por un PNG cuadrado (`data`), o vuelve al QR institucional (`data=None`), bajo revisión optimista (una revisión por cambio). El seed no se toca nunca.
    Orden y compensación (mismo contrato que la copia): primero el ASSET y después la instancia que lo referencia, tanto en disco como en Spaces; si algo no se confirma, disco y remoto vuelven a la versión
    anterior. Si ni la compensación se puede confirmar, el resultado es INCIERTO (503 con aviso): nunca un éxito parcial presentado como éxito ni una referencia rota."""
    if is_seed_id(presentation_id):
        raise SeedIsReadOnly()
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    _require_writable_mode()
    meta = validate_qr_png(data) if data is not None else None                    # se valida ANTES de escribir nada
    with _write_lock:
        raw = _read_raw(presentation_id)
        view = _instance_view(raw)
        if view["revision"] != expected_revision:
            raise StaleRevision(view["revision"])
        path, qr_file = _path(presentation_id), _qr_path(presentation_id)
        previous_json = path.read_bytes()
        previous_qr = qr_file.read_bytes() if qr_file.exists() else None
        if meta is None and previous_qr is None and not view["qr_sha"]:
            return view                                                          # ya usa el QR institucional: nada que hacer ni revisión que quemar
        candidate = {**raw, "schema_version": SCHEMA_VERSION, "template_version": view["template_version"], "revision": expected_revision + 1,
                     "updated_at": _now_iso(), "updated_by_id": str(updated_by_id or ""), "updated_by_label": str(updated_by_label or updated_by_id or "")}
        if meta is not None:
            candidate["qr"] = {k: meta[k] for k in ("sha256", "bytes", "width", "height")}
        else:
            candidate.pop("qr", None)

        def _restore_local() -> None:
            _restore_previous(path, previous_json)
            if previous_qr is not None:
                _restore_previous(qr_file, previous_qr)
            else:
                try:
                    qr_file.unlink(missing_ok=True)                              # solo local: el remoto lo compensa más abajo, y en `local` no se toca Spaces
                except OSError as exc:
                    _logger.warning("commercial_presentations.set_qr: no se pudo retirar el asset local reason=%s", type(exc).__name__)

        try:
            if data is not None:
                _write_bytes_atomic(qr_file, data)
            elif previous_qr is not None:
                qr_file.unlink()
            _write_atomic(path, candidate)
        except (PresentationStorageError, OSError) as exc:
            _restore_local()
            raise PresentationStorageError(f"No se pudo guardar el QR: {exc}") from exc
        if _local_only():
            return _instance_view(candidate)
        if not remote_store.enabled():                                           # determinístico: ni este intento ni un reintento pueden confirmar en este entorno
            _restore_local()
            raise PresentationConfigIncomplete("Las presentaciones todavía no están configuradas para este entorno.")
        asset_done = True
        if data is not None:
            asset_done = remote_store.push_path(qr_file)
        elif previous_qr is not None:
            asset_done = remote_store.delete_path(qr_file)
        if asset_done and remote_store.push_path(path):
            return _instance_view(candidate)
        # No se confirmó: el local vuelve a la versión anterior y se intenta dejar el REMOTO igual (asset e instancia).
        _restore_local()
        compensated = True
        if data is not None or previous_qr is not None:                          # el asset pudo quedar modificado en Spaces: se lo devuelve a lo que decía la instancia anterior
            compensated = remote_store.push_path(qr_file) if previous_qr is not None else remote_store.delete_path(qr_file)
        if asset_done:                                                           # la instancia pudo haber quedado actualizada en Spaces
            compensated = remote_store.push_path(path) and compensated
        if compensated:
            _logger.error("commercial_presentations.set_qr: no se confirmó en Spaces — local y remoto revertidos (id=%s)", presentation_id)
            raise DurablePushFailed()
        _logger.error("commercial_presentations.set_qr: no se confirmó y la compensación tampoco (id=%s)", presentation_id)
        raise RemoteOutcomeUnknown("No pudimos confirmar si el cambio del QR quedó guardado. Recargá la presentación y revisá el QR antes de reintentar.")


def save(presentation_id: str, fields, *, expected_revision: int, updated_by_id: str, updated_by_label: str, removed_slides=None, blocks=None) -> dict:
    """Guarda un SUBCONJUNTO de campos y/o la lista de slides eliminadas de UNA instancia bajo revisión optimista (una sola revisión por guardado). `removed_slides=None` no la toca; una lista (aun vacía) la reemplaza.
    El seed no se edita nunca. Si el push a Spaces no se confirma, el local vuelve a la versión anterior (503)."""
    if is_seed_id(presentation_id):
        raise SeedIsReadOnly()
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    _require_writable_mode()
    with _write_lock:
        raw = _read_raw(presentation_id)
        view = _instance_view(raw)                                               # Unavailable si el template no existe o es de una versión mayor
        if view["revision"] != expected_revision:
            raise StaleRevision(view["revision"])
        removed = validate_removed_slides(removed_slides) if removed_slides is not None else None
        composition = validate_blocks(blocks) if blocks is not None else None
        clean = validate_fields(view["template_id"], fields) if (fields or (removed is None and composition is None)) else {}
        candidate = {**raw, "schema_version": SCHEMA_VERSION, "template_version": view["template_version"],
                     "content": {**view["content"], **clean}, "revision": expected_revision + 1,
                     "updated_at": _now_iso(), "updated_by_id": str(updated_by_id or ""), "updated_by_label": str(updated_by_label or updated_by_id or "")}
        if removed is not None:
            if removed:
                candidate["removed_slides"] = removed
            else:
                candidate.pop("removed_slides", None)                            # sin slides eliminadas el archivo conserva la forma de siempre
        if composition is not None:
            if composition:
                candidate["blocks"] = composition
            else:
                candidate.pop("blocks", None)                                    # sin desvíos del defecto, tampoco la clave
        path = _path(presentation_id)
        previous_bytes = path.read_bytes()
        _write_atomic(path, candidate)
        if _local_only():
            return _instance_view(candidate)
        if remote_store.push_path(path):
            return _instance_view(candidate)
        _restore_previous(path, previous_bytes)
        if not remote_store.enabled():
            raise PresentationConfigIncomplete("Las presentaciones todavía no están configuradas para este entorno.")
        _logger.error("commercial_presentations.save: push síncrono a Spaces falló — local revertido (id=%s)", presentation_id)
        raise DurablePushFailed()


# ── Autorización ───────────────────────────────────────────────────────────────────────────────────────────────────────

def _email(user: dict | None) -> str:
    return str((user or {}).get("sub") or "").strip().lower()


def can_read_shared_user(user: dict | None) -> bool:
    """LEER / PRESENTAR por URL: cualquier usuario admitido al Hub (ALLOWED_USERS ∪ ACADEMY_USERS). NO exige Comercial. Se reevalúa en cada request (la cookie solo prueba que el login fue válido)."""
    email = _email(user)
    return bool(email) and auth_service.is_authorized(email)


def can_edit_presentation_user(user: dict | None, presentation: dict) -> bool:
    """EDITAR: Comercial Y no-seed Y (responsable de la instancia O `CONTENT_OWNER`). Copiar NO implica poder editar, ni `APP_OWNER` es editor de contenido por ser administrador: es el mismo contrato de Estrategia
    (`commercial_access.can_edit_strategy_user`: sin bypass de `APP_OWNER`; si el dueño de la app edita, su identidad está explícita en `CONTENT_OWNER`)."""
    if presentation.get("is_seed"):
        return False
    if not commercial_access.is_commercial_authorized_user(user):
        return False
    email = _email(user)
    owner = str(presentation.get("owner_id") or "").strip().lower()
    return bool(email) and ((bool(owner) and email == owner) or auth_service.is_content_owner_user(email))


def can_delete_presentation_user(user: dict | None, presentation: dict) -> bool:
    """ELIMINAR: Comercial Y no-seed Y (responsable de la instancia O `APP_OWNER`). Más estricto que editar para `CONTENT_OWNER` (edita, pero no elimina lo ajeno); y distinto de editar para `APP_OWNER`: eliminar es
    administración del workspace (superusuario según `commercial_access`), no gobierno de contenido."""
    if presentation.get("is_seed"):
        return False
    if not commercial_access.is_commercial_authorized_user(user):
        return False
    email = _email(user)
    owner = str(presentation.get("owner_id") or "").strip().lower()
    return bool(email) and ((bool(owner) and email == owner) or access.is_owner_user(user))


# ── Render ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _asset_hash(template_id: str, name: str) -> str:
    path = resolve_pack_file("assets", f"{TEMPLATES[template_id].assets_dir}/{name}", DEFAULT_PACK)
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def render_context(presentation: dict, *, embed: bool = False, theme: str = "light", edit: bool = False) -> dict:
    """Contexto del documento inmersivo: `c` = contenido completo (solo claves de la spec), URLs de assets COMPARTIDAS (idénticas para toda instancia del template) con cache-busting por contenido."""
    template_id = presentation["template_id"]
    spec = TEMPLATES[template_id]
    asset_base = f"/pack-assets/{DEFAULT_PACK}/{spec.assets_dir}"
    content = full_content(template_id, presentation.get("content"))
    removed = [s for s in NUMBERED_SLIDES if s in (presentation.get("removed_slides") or [])]
    visible = [s for s in NUMBERED_SLIDES if s not in removed]
    regions = resolve_regions(presentation.get("blocks"))
    qr_custom = bool(presentation.get("qr_sha")) and _qr_path(presentation["presentation_id"]).exists()      # referencia rota (archivo perdido) → vuelve al QR institucional, nunca una imagen rota
    return {
        "c": content,
        "regions": regions, "addons": _addons(), "library": component_library() if edit else [],
        "qr_url": (f"/presentaciones/{presentation['presentation_id']}/qr?v={str(presentation['qr_sha'])[:10]}" if qr_custom else f"{asset_base}/{_DEFAULT_QR}"), "qr_custom": qr_custom,
        "removed_ids": set(removed),                                             # el modo lectura omite estas slides; el de edición las muestra colapsadas con «Restaurar»
        "slide_no": {s: i for i, s in enumerate(visible, 1)},                    # numeración dinámica: «NN / total» sobre las visibles
        "slide_total": len(visible),
        "next_of": {s: (visible[i + 1] if i + 1 < len(visible) else None) for i, s in enumerate(visible)},
        "nav_of": {s: content[f"{s}_nav"] for s in visible},
        "first_slide": visible[0] if visible else None,
        "asset_base": asset_base,
        "institutional_logo_url": f"/pack-assets/{DEFAULT_PACK}/{_INSTITUTIONAL_LOGO}",
        "partner_logo_url": (f"/presentaciones/{presentation['presentation_id']}/logo?v={str(presentation['logo_sha'])[:10]}" if presentation.get("logo_sha") else f"{asset_base}/{_DEFAULT_PARTNER_LOGO}"),
        "partner_logo_alt": "Logo del cliente" if presentation.get("logo_sha") else "Vanture — a Knowmad Mood company",
        "css_url": f"{asset_base}/{spec.css}?v={_asset_hash(template_id, spec.css)}",
        "js_url": f"{asset_base}/{spec.js}?v={_asset_hash(template_id, spec.js)}",
        "embed": bool(embed),
        "edit_mode": bool(edit),                                                 # solo lo pide el router cuando el usuario PUEDE editar esta instancia
        "theme": "dark" if theme == "dark" else "light",
    }
