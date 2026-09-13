"""
test_profile_document_validation.py — Capital Humano Phase 4.1: contrato
puro de `webapp.services.profile_document_validation` (sin filesystem, sin
HTTP, sin Graph)."""
from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from webapp.services import profile_document_validation as v


def _minimal_docx_bytes(*, include_content_types=True, include_document_xml=True) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if include_content_types:
            archive.writestr("[Content_Types].xml", "<Types/>")
        if include_document_xml:
            archive.writestr("word/document.xml", "<document/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
    return buffer.getvalue()


# --- PDF ---------------------------------------------------------------

def test_valid_pdf_passes():
    ext, content_type = v.validate_upload("cv.pdf", b"%PDF-1.4\n...")
    assert ext == "pdf"
    assert content_type == "application/pdf"


def test_pdf_extension_with_wrong_magic_bytes_is_rejected():
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv.pdf", b"not a real pdf")


# --- DOC -----------------------------------------------------------------

def test_valid_doc_passes():
    data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
    ext, content_type = v.validate_upload("cv.doc", data)
    assert ext == "doc"
    assert content_type == "application/msword"


def test_doc_extension_with_wrong_magic_bytes_is_rejected():
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv.doc", b"PK\x03\x04 not really OLE2")


# --- DOCX ------------------------------------------------------------------

def test_valid_docx_passes():
    ext, content_type = v.validate_upload("cv.docx", _minimal_docx_bytes())
    assert ext == "docx"
    assert content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_docx_missing_document_xml_is_rejected():
    data = _minimal_docx_bytes(include_document_xml=False)
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv.docx", data)


def test_docx_missing_content_types_is_rejected():
    data = _minimal_docx_bytes(include_content_types=False)
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv.docx", data)


def test_docx_that_is_not_a_zip_at_all_is_rejected():
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv.docx", b"not a zip file at all")


def test_generic_zip_without_word_entries_is_rejected():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.csv", "a,b,c")
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv.docx", buffer.getvalue())


# --- extensión / MIME incorrectos ------------------------------------------

def test_unsupported_extension_is_rejected():
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv.txt", b"plain text content")


def test_extension_mismatch_docx_bytes_named_pdf_is_rejected():
    # El nombre dice .pdf pero el contenido es un DOCX real -- la
    # extensión gobierna QUÉ firma se exige, así que esto falla la
    # verificación de magic bytes de PDF.
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv.pdf", _minimal_docx_bytes())


def test_no_extension_at_all_is_rejected():
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv", b"%PDF-1.4")


# --- tamaño / ausencia -------------------------------------------------

def test_oversize_file_is_rejected():
    data = b"%PDF-" + b"0" * (v.MAX_CV_UPLOAD_BYTES + 1)
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv.pdf", data)


def test_file_at_exact_max_size_is_accepted():
    data = b"%PDF-" + b"0" * (v.MAX_CV_UPLOAD_BYTES - len(b"%PDF-"))
    assert len(data) == v.MAX_CV_UPLOAD_BYTES
    ext, _content_type = v.validate_upload("cv.pdf", data)
    assert ext == "pdf"


def test_empty_file_is_rejected():
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("cv.pdf", b"")


def test_missing_filename_is_rejected():
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("", b"%PDF-1.4")


def test_blank_filename_is_rejected():
    with pytest.raises(v.ProfileDocumentValidationError):
        v.validate_upload("   ", b"%PDF-1.4")
