"""cv_extract.py — extracción LOCAL y mínima de datos de contacto de un CV (PDF o DOCX) para la importación histórica al Banco de CCHH.

Extrae SOLO cuatro datos —nombre, apellido, email y celular/WhatsApp— con heurísticas locales y sin ningún servicio externo (PDF con `pypdf`; DOCX con `zipfile` + XML de la biblioteca estándar). Es PURO:
recibe bytes y devuelve datos; no toca disco, Graph ni perfiles, y NUNCA conserva ni devuelve el texto del CV (solo los cuatro campos, sus candidatos y la evidencia de dónde salieron).

Cada resultado trae banderas: `blocking` (el registro necesita revisión humana antes de cualquier alta) y `advisory` (aviso que no bloquea). Nada se da por bueno cuando hay ambigüedad: varios emails o celulares, nombre
en conflicto entre el texto y el nombre del archivo, nombre de una sola fuente, orden o partición del nombre incierta. Los límites (páginas, caracteres, tamaño descomprimido de un DOCX, DOCTYPE/entidades XML) protegen
contra archivos hostiles.

LOS CAMPOS EXTRAÍDOS SON DATOS PERSONALES: este módulo no los registra en logs; quien los persiste (`cv_history_manifest`) los guarda solo en el área temporal protegida."""
from __future__ import annotations

import logging
import re
import unicodedata
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

logging.getLogger("pypdf").setLevel(logging.ERROR)          # pypdf advierte por objetos mal formados: ruido sin valor y sin garantía de no citar contenido

MAX_PDF_PAGES = 6
MAX_TEXT_CHARS = 40000
DOCX_MAX_UNCOMPRESSED = 20 * 1024 * 1024
DOCX_MAX_ENTRIES = 300
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Códigos de falla de lectura (terminales por documento)
NO_TEXT, PDF_ENCRYPTED, EXTRACTION_ERROR, FORMAT_UNSUPPORTED = "NO_TEXT", "PDF_ENCRYPTED", "EXTRACTION_ERROR", "FORMAT_UNSUPPORTED"
# Banderas de extracción
NAME_MISSING, NAME_CONFLICT, NAME_PARTIAL_MATCH, NAME_SINGLE_SOURCE = "NAME_MISSING", "NAME_CONFLICT", "NAME_PARTIAL_MATCH", "NAME_SINGLE_SOURCE"
NAME_ORDER_UNCERTAIN, NAME_SPLIT_AMBIGUOUS = "NAME_ORDER_UNCERTAIN", "NAME_SPLIT_AMBIGUOUS"
EMAIL_MISSING, EMAIL_MULTIPLE, PHONE_MISSING, PHONE_MULTIPLE = "EMAIL_MISSING", "EMAIL_MULTIPLE", "PHONE_MISSING", "PHONE_MULTIPLE"
BLOCKING = {NAME_MISSING, NAME_CONFLICT, NAME_PARTIAL_MATCH, NAME_SINGLE_SOURCE, NAME_ORDER_UNCERTAIN, EMAIL_MISSING, EMAIL_MULTIPLE, PHONE_MULTIPLE}
ADVISORY = {PHONE_MISSING, NAME_SPLIT_AMBIGUOUS}


class ExtractionFailure(Exception):
    """El documento no se pudo leer. `code` estable; sin mensaje libre (podría citar contenido)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ═══════════════════ texto ═══════════════════
def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


def _pdf_text(data: bytes) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ExtractionFailure(PDF_ENCRYPTED)
            except ExtractionFailure:
                raise
            except Exception:                                                  # noqa: BLE001 — sin la librería de cifrado o con cifrado no soportado: se informa como cifrado
                raise ExtractionFailure(PDF_ENCRYPTED) from None
        chunks: list[str] = []
        for page in list(reader.pages)[:MAX_PDF_PAGES]:
            chunks.append(page.extract_text() or "")
            if sum(len(c) for c in chunks) >= MAX_TEXT_CHARS:
                break
        return "\n".join(chunks)
    except ExtractionFailure:
        raise
    except Exception:                                                          # noqa: BLE001 — PDF corrupto o hostil: código estable, sin mensaje de la librería
        raise ExtractionFailure(EXTRACTION_ERROR) from None


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > DOCX_MAX_ENTRIES or sum(i.file_size for i in infos) > DOCX_MAX_UNCOMPRESSED:
                raise ExtractionFailure(EXTRACTION_ERROR)                       # protección contra «zip bomb»
            names = {i.filename for i in infos}
            headers = sorted(n for n in names if re.fullmatch(r"word/header\d*\.xml", n))
            footers = sorted(n for n in names if re.fullmatch(r"word/footer\d*\.xml", n))
            parts = headers + (["word/document.xml"] if "word/document.xml" in names else []) + footers     # el contacto suele estar en el encabezado
            lines: list[str] = []
            for part in parts:
                xml = archive.read(part)
                head = xml[:4096].upper()
                if b"<!DOCTYPE" in head or b"<!ENTITY" in xml.upper():
                    raise ExtractionFailure(EXTRACTION_ERROR)                   # sin DTD/entidades: evita expansión de entidades
                root = ET.fromstring(xml)
                for paragraph in root.iter(_W + "p"):
                    buf: list[str] = []
                    for node in paragraph.iter():
                        if node.tag == _W + "t":
                            buf.append(node.text or "")
                        elif node.tag == _W + "tab":
                            buf.append(" ")
                        elif node.tag in (_W + "br", _W + "cr"):
                            buf.append("\n")
                    lines.append("".join(buf))
            return "\n".join(lines)
    except ExtractionFailure:
        raise
    except Exception:                                                          # noqa: BLE001 — zip/XML inválido
        raise ExtractionFailure(EXTRACTION_ERROR) from None


def extract_text(filename: str, data: bytes) -> str:
    """Texto del documento (acotado). Lanza `ExtractionFailure` (NO_TEXT si no hay texto extraíble —p. ej. un PDF escaneado—, PDF_ENCRYPTED, EXTRACTION_ERROR, FORMAT_UNSUPPORTED)."""
    ext = _ext(filename)
    if ext == "pdf":
        text = _pdf_text(data)
    elif ext == "docx":
        text = _docx_text(data)
    else:
        raise ExtractionFailure(FORMAT_UNSUPPORTED)                             # el DOC binario queda para revisión manual
    text = text[:MAX_TEXT_CHARS]
    if not text.strip():
        raise ExtractionFailure(NO_TEXT)
    return text


# ═══════════════════ campos ═══════════════════
def fold(value: str) -> str:
    """Minúsculas, sin acentos y con espacios colapsados: clave de comparación (nunca se persiste como dato)."""
    text = unicodedata.normalize("NFKD", value or "")
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).lower().split())


_EMAIL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_BAD_EMAIL_TAIL = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


def find_emails(text: str) -> list[str]:
    seen: list[str] = []
    for match in _EMAIL_RE.findall(text or ""):
        email = match.rstrip(".-_").lower()
        if email.endswith(_BAD_EMAIL_TAIL) or "@example." in email:
            continue
        if email not in seen:
            seen.append(email)
    return seen


_PHONE_RE = re.compile(r"(?<![\w@.])(\+?\d[\d\s().\-]{6,}\d)(?![\w@])")
_PHONE_KEYWORDS = re.compile(r"(cel(?:ular)?|tel(?:[eé]fono)?|whats?\s?app|wsp|m[oó]vil|mobile|phone|contacto|fono)\W{0,12}$", re.I)
_DNI_RE = re.compile(r"^\d{1,2}\.\d{3}\.\d{3}$")
_CUIT_RE = re.compile(r"^\d{2}-\d{8}-\d$")
_YEARS_RE = re.compile(r"^(?:19|20)\d{2}(?:19|20)\d{2}$")


def find_phones(text: str) -> list[dict]:
    """Candidatos `{display, digits, score}` ordenados por puntaje. Descarta DNI, CUIT, rangos de años y cadenas de longitud implausible."""
    found: dict[str, dict] = {}
    for m in _PHONE_RE.finditer(text or ""):
        raw = m.group(1).strip()
        digits = re.sub(r"\D", "", raw)
        if not 8 <= len(digits) <= 15 or _DNI_RE.match(raw) or _CUIT_RE.match(raw) or _YEARS_RE.match(digits):
            continue
        score = 0
        if _PHONE_KEYWORDS.search(text[max(0, m.start() - 24):m.start()]):
            score += 3
        if raw.startswith("+"):
            score += 2
        if digits.startswith(("54", "11", "15", "9")):
            score += 1
        key = digits[-10:]
        entry = found.get(key)
        display = " ".join(raw.split())
        if entry is None or score > entry["score"]:
            found[key] = {"display": display, "digits": digits, "score": score}
    return sorted(found.values(), key=lambda e: (-e["score"], e["digits"]))


_NAME_TOKEN = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’\-]*\.?$")
_STOP = set(fold("""curriculum curriculo vitae cv resume resumen perfil profile datos personales personal contacto contact email mail e-mail telefono celular cel tel whatsapp linkedin github direccion domicilio ubicacion
buenos aires argentina caba cordoba rosario mendoza experiencia laboral educacion formacion estudios habilidades skills objetivo sobre acerca about me senior junior semi ssr sr jr consultor consultora analista arquitecto
arquitecta ingeniero ingeniera desarrollador desarrolladora developer engineer architect consultant specialist especialista lider lead manager gerente sap datasphere databricks data bw hana abap fiori power bi azure aws gcp
cloud python sql java scrum master agile project product owner functional funcional tecnico technical analytics ia inteligencia artificial machine learning engineering ingenieria informatica sistemas licenciado licenciada
actualizado vigente nacionalidad nacimiento fecha edad estado civil casado soltero idiomas ingles espanol portugues certificaciones cursos referencias proyectos conocimientos tecnologias herramientas""").split())
_NAME_LABEL = re.compile(r"^(?:nombre(?:s)?(?: y apellido(?:s)?)?|apellido(?:s)? y nombre(?:s)?|full name|name)\s*[:\-]\s*(.+)$", re.I)
_SEPARATORS = re.compile(r"\s*[|•·–—/]\s*|\s+-\s+")


def _clean_token(token: str) -> str:
    token = token.strip(".")
    if token.isupper() and len(token) > 1:
        token = token.capitalize()
    return token


def _name_tokens(segment: str) -> list[str] | None:
    """Tokens de un segmento si parece un nombre de persona (2–4 palabras alfabéticas, sin palabras de CV); si no, None."""
    words = segment.replace("\t", " ").split()
    if not 2 <= len(words) <= 4:
        return None
    for w in words:
        if not _NAME_TOKEN.match(w) or fold(w.strip(".")) in _STOP or len(w.strip(".")) < 2:
            return None
    return [_clean_token(w) for w in words]


def _name_from_segment(segment: str) -> dict | None:
    segment = segment.strip(" ,;:")
    if "," in segment and segment.count(",") == 1:                              # «Apellido, Nombre»
        left, right = (s.strip() for s in segment.split(","))
        l_words, r_words = left.split(), right.split()
        if 1 <= len(l_words) <= 3 and 1 <= len(r_words) <= 3:
            tokens = _name_tokens(" ".join(l_words + r_words))
            if tokens and len(tokens) >= 2:
                return {"first": " ".join(_clean_token(w) for w in r_words), "last": " ".join(_clean_token(w) for w in l_words), "tokens": tokens, "ordered": True, "split_certain": True}
    tokens = _name_tokens(segment)
    if tokens:
        return {"tokens": tokens, "ordered": False, "split_certain": False}
    return None


def _text_name(text: str) -> dict | None:
    lines = [ln.strip(" \t•·*-_=#►▪●") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    for ln in lines[:40]:
        m = _NAME_LABEL.match(ln)
        if m:
            cand = _name_from_segment(m.group(1))
            if cand:
                return cand
    for ln in lines[:15]:
        if "@" in ln or any(ch.isdigit() for ch in ln):
            continue
        for segment in _SEPARATORS.split(ln):
            cand = _name_from_segment(segment)
            if cand:
                return cand
    return None


_FILE_NOISE = set(fold("cv curriculum curriculo vitae resume resumen actualizado actual final nuevo copia copy docx pdf perfil profile danwor formato").split())


def _filename_name(filename: str) -> dict | None:
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    base = re.sub(r"[_.]+", " ", base)
    base = re.sub(r"\(\d+\)", " ", base)
    if "," in base and base.count(",") == 1:
        cand = _name_from_segment(re.sub(r"\d+", " ", " ".join(w for w in base.split() if fold(w.strip(",")) not in _FILE_NOISE)))
        if cand:
            return cand
    words = [w for w in re.split(r"[\s\-]+", base) if w and not re.search(r"\d", w) and fold(w) not in _FILE_NOISE]
    return _name_from_segment(" ".join(words))


def _token_set(tokens: list[str]) -> set[str]:
    return {fold(t) for t in tokens}


def _split(tokens: list[str]) -> tuple[str, str, bool]:
    """(nombre, apellido, partición_incierta) para «Nombre Apellido…»: con 2 palabras no hay duda; con más, la partición es una suposición (se avisa)."""
    if len(tokens) == 2:
        return tokens[0], tokens[1], False
    if len(tokens) == 3:
        return tokens[0], " ".join(tokens[1:]), True
    return " ".join(tokens[:2]), " ".join(tokens[2:]), True


def extract_fields(text: str, filename: str) -> dict:
    """Los cuatro campos, con candidatos, evidencia y banderas. Nunca devuelve el texto."""
    emails = find_emails(text)
    phones = find_phones(text)
    flags: list[str] = []
    evidence: dict = {}

    email = None
    if len(emails) == 1:
        email, evidence["email"] = emails[0], "text"
    elif not emails:
        flags.append(EMAIL_MISSING)
    else:
        flags.append(EMAIL_MULTIPLE)

    phone = phone_digits = None
    if phones:
        top = phones[0]
        phone, phone_digits, evidence["phone"] = top["display"], top["digits"], "text"
        if len(phones) > 1:
            flags.append(PHONE_MULTIPLE)
    else:
        flags.append(PHONE_MISSING)

    from_text, from_file = _text_name(text), _filename_name(filename)
    chosen = None
    if from_text and from_file:
        a, b = _token_set(from_text["tokens"]), _token_set(from_file["tokens"])
        if a == b or a <= b or b <= a:                                          # mismo conjunto, o uno contiene al otro (p. ej. el archivo omite el segundo apellido)
            chosen, evidence["name"] = from_text, ["text", "filename"]
        elif a & b:
            chosen, evidence["name"] = from_text, ["text", "filename"]
            flags.append(NAME_PARTIAL_MATCH)
        else:
            chosen, evidence["name"] = from_text, ["text"]
            flags.append(NAME_CONFLICT)
    elif from_text:
        chosen, evidence["name"] = from_text, ["text"]
    elif from_file:
        chosen, evidence["name"] = from_file, ["filename"]
        flags.append(NAME_ORDER_UNCERTAIN)                                      # el orden de un nombre de archivo («Perez Juan» / «Juan Perez») no es confiable
    else:
        flags.append(NAME_MISSING)

    first = last = None
    corroborated = False
    if chosen:
        if chosen.get("split_certain"):
            first, last = chosen["first"], chosen["last"]
        else:
            first, last, uncertain = _split(chosen["tokens"])
            if uncertain:
                flags.append(NAME_SPLIT_AMBIGUOUS)
        if email:                                                               # el email corrobora el nombre si el usuario del correo contiene un apellido o nombre
            local = fold(email.split("@")[0]).replace(".", "").replace("_", "").replace("-", "")
            corroborated = any(len(t) >= 3 and t in local for t in _token_set(chosen["tokens"]))
        if evidence["name"] == ["text"] and not corroborated:                   # una sola fuente y sin corroboración del email: queda para revisión
            flags.append(NAME_SINGLE_SOURCE)

    blocking = sorted(f for f in flags if f in BLOCKING)
    advisory = sorted(f for f in flags if f in ADVISORY)
    return {
        "first_name": first, "last_name": last, "email": email, "phone": phone, "phone_digits": phone_digits,
        "name_corroborated_by_email": corroborated,
        "evidence": evidence,
        "candidates": {"emails": emails[:3], "phones": [p["display"] for p in phones[:3]],
                       "names": {k: " ".join(v["tokens"]) for k, v in (("text", from_text), ("filename", from_file)) if v}},
        "flags": sorted(set(flags)), "blocking": blocking, "advisory": advisory,
        "state": "NEEDS_REVIEW" if blocking else "COMPLETE",
    }


def extract(filename: str, data: bytes) -> dict:
    """Texto + campos. Las fallas de lectura se devuelven como resultado (`failure`) para que el llamador las registre por documento sin interrumpir el lote."""
    try:
        text = extract_text(filename, data)
    except ExtractionFailure as exc:
        return {"failure": exc.code, "flags": [exc.code], "blocking": [exc.code], "advisory": [], "state": "NEEDS_REVIEW",
                "first_name": None, "last_name": None, "email": None, "phone": None, "phone_digits": None, "evidence": {}, "candidates": {"emails": [], "phones": [], "names": {}}}
    return extract_fields(text, filename)
