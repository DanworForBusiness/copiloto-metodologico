"""
profile_document_validation.py — Capital Humano Phase 4.1 §12: validación
de FORMATO únicamente para uploads de documento de Profile (PDF/DOC/DOCX)
— NUNCA una garantía de seguridad/antivirus (ticket §12, explícito: "no
claim of malware safety"), solo descarta el caso más común de un archivo
mal nombrado/corrupto ANTES de gastar una llamada a Graph.

Tres formatos aceptados, cada uno verificado por su propia firma
ESTRUCTURAL — nunca solo la extensión del nombre de archivo, que el
browser/usuario puede mentir:
  - PDF:  magic bytes `%PDF-` al inicio del archivo.
  - DOC (legado, binario OLE2/CFBF): magic bytes
    `D0 CF 11 E0 A1 B1 1A E1` al inicio.
  - DOCX: ZIP válido que además contiene `[Content_Types].xml` Y
    `word/document.xml` — un ZIP cualquiera (o un XLSX/PPTX, que también
    son ZIP con OOXML) no alcanza; se exige la presencia de AMBAS
    entradas específicas de un documento de Word.

Puro (sin filesystem, sin Graph, sin FastAPI) — testeable en aislamiento
completo."""
from __future__ import annotations

import zipfile
from io import BytesIO

# Bound de PRIMERA REBANADA (ticket §12), decisión de producto/operación —
# MISMO criterio de "acotado, nunca indefinido" que el resto del Hub
# (ver profile_import_service.MAX_UPLOAD_BYTES).
MAX_CV_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB

_PDF_MAGIC = b"%PDF-"
_DOC_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

ACCEPTED_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class ProfileDocumentValidationError(Exception):
    """Archivo rechazado — sin archivo, vacío, extensión no soportada,
    tamaño excedido, o estructura/magic bytes que no coincide con el
    formato declarado por la extensión. SIEMPRE un mensaje seguro para
    mostrar al usuario, nunca un traceback de `zipfile`."""


def _detect_and_verify_format(filename: str, data: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ACCEPTED_CONTENT_TYPES:
        raise ProfileDocumentValidationError("Formato no soportado. Subí un archivo PDF, DOC o DOCX.")
    if ext == "pdf":
        if not data.startswith(_PDF_MAGIC):
            raise ProfileDocumentValidationError("El archivo no es un PDF válido.")
    elif ext == "doc":
        if not data.startswith(_DOC_MAGIC):
            raise ProfileDocumentValidationError("El archivo no es un DOC válido.")
    else:  # docx
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            raise ProfileDocumentValidationError("El archivo no es un DOCX válido.") from None
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise ProfileDocumentValidationError("El archivo no es un DOCX válido.")
    return ext


def validate_upload(filename: str, data: bytes) -> tuple[str, str]:
    """Devuelve `(ext, content_type)` si el archivo pasa validación de
    tamaño + formato. Levanta `ProfileDocumentValidationError` (mensaje
    seguro) si no. Orden: nombre presente → contenido no vacío → tamaño
    (`MAX_CV_UPLOAD_BYTES` PRIMERO, nunca vale la pena parsear un archivo
    que ya excede el límite) → formato/magic bytes."""
    if not filename or not filename.strip():
        raise ProfileDocumentValidationError("Elegí un archivo para continuar.")
    if not data:
        raise ProfileDocumentValidationError("El archivo está vacío.")
    if len(data) > MAX_CV_UPLOAD_BYTES:
        raise ProfileDocumentValidationError(
            f"El archivo supera el máximo de {MAX_CV_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    ext = _detect_and_verify_format(filename, data)
    return ext, ACCEPTED_CONTENT_TYPES[ext]
