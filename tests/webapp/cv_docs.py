"""Documentos de PRUEBA (PDF y DOCX) generados en memoria para los tests de extracción de CVs históricos. Ningún dato es real."""
from __future__ import annotations

import io
import zipfile


def _esc(text: str) -> bytes:
    return text.encode("latin-1", "replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(lines: list[str]) -> bytes:
    """PDF válido de una página con texto Helvetica/WinAnsi (lo lee `pypdf`)."""
    body = b"BT /F1 12 Tf 72 740 Td 16 TL " + b" ".join(b"(" + _esc(line) + b") Tj T*" for line in lines) + b" ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_blank_pdf() -> bytes:
    """PDF sin texto (equivale a un escaneado: sin capa de texto)."""
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_encrypted_pdf(password: str = "secret") -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.encrypt(password, algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _paragraphs(lines: list[str]) -> str:
    return "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{line.replace('&', '&amp;').replace('<', '&lt;')}</w:t></w:r></w:p>" for line in lines)


_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(body: list[str], header: list[str] | None = None, footer: list[str] | None = None, *, extra_document_xml: str | None = None) -> bytes:
    """DOCX mínimo válido (con `[Content_Types].xml` y `word/document.xml`, como exige la validación del Hub), con encabezado/pie opcionales."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", extra_document_xml or f'<?xml version="1.0" encoding="UTF-8"?><w:document {_NS}><w:body>{_paragraphs(body)}</w:body></w:document>')
        if header:
            z.writestr("word/header1.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:hdr {_NS}>{_paragraphs(header)}</w:hdr>')
        if footer:
            z.writestr("word/footer1.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:ftr {_NS}>{_paragraphs(footer)}</w:ftr>')
    return buf.getvalue()


def make_zip_bomb_docx(uncompressed_mb: int = 30) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        z.writestr("word/media/huge.bin", b"0" * (uncompressed_mb * 1024 * 1024))
    return buf.getvalue()
