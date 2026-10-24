"""cv_extract.py — extracción LOCAL y mínima de datos de contacto de un CV (PDF o DOCX) para la importación histórica al Banco de CCHH.

Extrae SOLO cuatro datos —nombre, apellido, email y celular/WhatsApp— con heurísticas locales y sin ningún servicio externo (PDF con `pypdf`; DOCX con `zipfile` + XML de la biblioteca estándar). Es PURO:
recibe bytes y devuelve datos; no toca disco, Graph ni perfiles, y NUNCA conserva ni devuelve el texto del CV (solo los cuatro campos, sus candidatos y la evidencia de dónde salieron).

Cada resultado trae banderas: `blocking` (el registro necesita revisión humana antes de cualquier alta) y `advisory` (aviso que no bloquea). Nada se da por bueno cuando hay ambigüedad: varios emails o celulares, nombre
en conflicto, nombre sin evidencia distintiva, orden o partición del nombre incierta.

IDENTIDAD (versión 2 del extractor): un nombre solo se PROPONE si hay evidencia DISTINTIVA, nunca por su estructura. Se comparan, con reglas simples y explicables (sin puntajes ni diccionarios grandes), los candidatos del texto (hasta 3),
del nombre de archivo y la corroboración del email:
  * evidencia distintiva = (a) el usuario del email coincide de forma FUERTE con el nombre (dos palabras del nombre, o inicial + palabra de 4+ letras; un buzón de rol —«ventas», «info»…— o una sola palabra no alcanza); o (b) una
    línea con ETIQUETA explícita («Nombre: …»), que es una identidad DECLARADA (no corroborada);
  * texto y archivo que coinciden solo cuentan como corroboración si además hay un ancla (etiqueta o email fuerte): una frase funcional puede coincidir con el nombre del archivo y no por eso es una persona;
  * un candidato debe ser PLAUSIBLE: 2–4 palabras con mayúscula inicial, sin conectores de frase, sin marcadores institucionales y sin palabras de CV; las partículas de apellido (de, del, la, van…) se admiten entre palabras;
  * ante conflicto sin resolver o evidencia insuficiente NO hay nombre propuesto: se conservan TODAS las alternativas con sus fuentes y motivos para la revisión humana. Una propuesta automática nunca es una identidad verificada.
NORMALIZACIÓN (versión 3): los prefijos documentales y los títulos profesionales CLARAMENTE reconocibles («Dr», «Lic», «Ing», «HV»…) se quitan de los bordes del nombre antes de evaluarlo, conservando el valor original en
`normalization`. Un token de borde AMBIGUO («RC», cuyo significado no está confirmado) nunca se descarta en silencio: si el email corrobora con fuerza las palabras restantes se ofrece la variante sin el token y se exige
revisión del owner. Red de seguridad sin lista: una identidad que viene SOLO del nombre de archivo y cuya corroboración depende SOLO del email, con un token de borde de hasta 3 letras que el email no explica, se marca con
`NAME_EDGE_TOKEN_UNEXPLAINED` (bloqueante: pide revisión; la propuesta no cambia). Los nombres cortos legítimos («Li», «Wu»), las iniciales («J.», «P.») y las partículas de apellido se conservan.
Cada decisión queda registrada con la regla aplicada (R1–R6) en `name_decision`, sin el texto del CV. Los límites (páginas, caracteres, tamaño descomprimido de un DOCX, DOCTYPE/entidades XML) protegen
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
EXTRACTOR_VERSION = 3                                                                                                           # 2: arbitraje por evidencia (R1–R6); 3: normalización de prefijos/títulos y NAME_EDGE_TOKEN_UNEXPLAINED
NAME_MISSING, NAME_CONFLICT, NAME_PARTIAL_MATCH, NAME_SINGLE_SOURCE = "NAME_MISSING", "NAME_CONFLICT", "NAME_PARTIAL_MATCH", "NAME_SINGLE_SOURCE"     # NAME_SINGLE_SOURCE: solo en datos de la versión 1
NAME_UNCORROBORATED, NAME_ALTERNATIVE, NAME_DECLARED = "NAME_UNCORROBORATED", "NAME_ALTERNATIVE", "NAME_DECLARED"
NAME_EDGE_TOKEN_UNEXPLAINED = "NAME_EDGE_TOKEN_UNEXPLAINED"
NAME_ORDER_UNCERTAIN, NAME_SPLIT_AMBIGUOUS = "NAME_ORDER_UNCERTAIN", "NAME_SPLIT_AMBIGUOUS"
EMAIL_MISSING, EMAIL_MULTIPLE, PHONE_MISSING, PHONE_MULTIPLE = "EMAIL_MISSING", "EMAIL_MULTIPLE", "PHONE_MISSING", "PHONE_MULTIPLE"
BLOCKING = {NAME_MISSING, NAME_CONFLICT, NAME_PARTIAL_MATCH, NAME_SINGLE_SOURCE, NAME_UNCORROBORATED, NAME_EDGE_TOKEN_UNEXPLAINED, NAME_ORDER_UNCERTAIN, EMAIL_MISSING, EMAIL_MULTIPLE, PHONE_MULTIPLE}
ADVISORY = {PHONE_MISSING, NAME_SPLIT_AMBIGUOUS, NAME_ALTERNATIVE, NAME_DECLARED}


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

# Controles de plausibilidad y de distintividad: listas CHICAS y por categoría (no un diccionario de cargos).
PARTICLES = frozenset({"de", "del", "la", "las", "los", "da", "das", "do", "dos", "di", "van", "von", "bin", "el"})        # partículas de apellido: válidas solo ENTRE palabras
CONNECTORS = frozenset({"and", "of", "the", "for", "with", "in", "on", "at", "to", "as", "or", "en", "para", "con", "por", "y", "e", "a", "o", "un", "una"})   # conectores de FRASE
INSTITUTION_MARKERS = frozenset(fold("""universidad university instituto institute facultad escuela colegio banco bank empresa company corp corporation inc sa srl ltda group grupo fundacion ministerio secretaria
municipalidad hospital clinica sociedad asociacion""").split())
ROLE_MAILBOXES = frozenset(fold("""info ventas contacto contact admin administracion rrhh hr soporte support comercial compras sales mail hola empleo jobs careers recepcion gerencia""").split())
MAX_TEXT_CANDIDATES = 3
# Prefijos documentales y títulos CLARAMENTE reconocibles (se quitan de los bordes del nombre; el valor original queda en `normalization`). Lista cerrada y chica: solo lo que no puede ser un nombre de pila ni un apellido.
TITLE_PREFIXES = frozenset("dr dra lic lica ing mg mag cr cra cpn arq abog prof sr sra srta msc phd".split())
DOC_PREFIXES = frozenset({"cv", "hv"})
# Tokens de borde AMBIGUOS: parecen un prefijo pero su significado no está confirmado («RC»). Nunca se descartan en silencio: ver `_edge_review`.
AMBIGUOUS_EDGE = frozenset({"rc"})


def _is_initial(word: str) -> bool:
    """Una inicial legítima («J.», «P»): una letra, con punto o en mayúscula. Una letra suelta en minúscula, o «A»/«Y»/«E»/«O» sin punto, es un conector, no una inicial."""
    base = word.strip(".")
    if len(base) != 1 or not base.isalpha():
        return False
    if not word.endswith(".") and (base.islower() or fold(base) in CONNECTORS):
        return False
    return True


def _clean_token(token: str) -> str:
    base = token.strip(".")
    if len(base) == 1:
        return base.upper() + "."
    if base.isupper() and len(base) > 1:
        base = base.capitalize()
    return base


def _reject_reasons(segment: str, *, caps: bool = True) -> list[str]:
    """Por qué un segmento NO es un nombre plausible (vacío = plausible). Códigos estables, para trazabilidad."""
    words = segment.replace("\t", " ").split()
    reasons: list[str] = []
    initials = [w for w in words if _is_initial(w)]
    core = [w for w in words if not _is_initial(w) and fold(w.strip(".")) not in PARTICLES]
    particles = [w for w in words if fold(w.strip(".")) in PARTICLES]
    if not 2 <= len(core) <= 4 or len(particles) > 2 or len(initials) > 2 or len(words) > 6:
        reasons.append("TOKEN_COUNT")
    if words and (fold(words[0].strip(".")) in PARTICLES or fold(words[-1].strip(".")) in PARTICLES):
        reasons.append("PARTICLE_AT_EDGE")
    for w in core:
        base = w.strip(".")
        f = fold(base)
        if not _NAME_TOKEN.match(w) or len(base) < 2:
            reasons.append("NOT_ALPHABETIC")
        if f in CONNECTORS:
            reasons.append("CONNECTOR")
        if f in INSTITUTION_MARKERS:
            reasons.append("INSTITUTION_MARKER")
        if f in _STOP:
            reasons.append("CV_WORD")
        if caps and base and not base[0].isupper():
            reasons.append("NOT_CAPITALIZED")
    return sorted(set(reasons))


def _name_tokens(segment: str, *, caps: bool = True) -> list[str] | None:
    """Tokens de un segmento si es un nombre PLAUSIBLE (ver `_reject_reasons`); si no, None."""
    if _reject_reasons(segment, caps=caps):
        return None
    return [w.lower() if fold(w.strip(".")) in PARTICLES else _clean_token(w) for w in segment.replace("\t", " ").split()]


def _core(tokens: list[str]) -> list[str]:
    return [t for t in tokens if fold(t) not in PARTICLES and not _is_initial(t)]


def _name_from_segment(segment: str, *, caps: bool = True) -> dict | None:
    segment = segment.strip(" ,;:")
    if "," in segment and segment.count(",") == 1:                              # «Apellido, Nombre»
        left, right = (s.strip() for s in segment.split(","))
        l_words, r_words = left.split(), right.split()
        if 1 <= len(l_words) <= 3 and 1 <= len(r_words) <= 3:
            tokens = _name_tokens(" ".join(l_words + r_words), caps=caps)
            if tokens and len(_core(tokens)) >= 2:
                return {"first": " ".join(_clean_token(w) for w in r_words), "last": " ".join(_clean_token(w) for w in l_words), "tokens": tokens, "split_certain": True}
    words, stripped = _strip_edges(segment.split())
    tokens = _name_tokens(" ".join(words), caps=caps)
    if tokens:
        cand = {"tokens": tokens, "split_certain": False}
        if stripped:
            cand["normalization"] = {"original": segment, "stripped": stripped}                 # el valor original se conserva: nunca se descarta sin dejar rastro
        return cand
    return None


def _strip_edges(words: list[str]) -> tuple[list[str], list[dict]]:
    """Quita de los BORDES los prefijos documentales y títulos reconocibles (lista cerrada), solo mientras queden 2 o más palabras. Un token del medio nunca se quita."""
    kept, stripped = list(words), []
    while len(kept) >= 3:
        head, tail = fold(kept[0].strip(".")), fold(kept[-1].strip("."))
        if head in TITLE_PREFIXES or head in DOC_PREFIXES:
            stripped.append({"token": kept[0], "kind": "TITLE" if head in TITLE_PREFIXES else "DOCUMENT", "position": "START"})
            kept = kept[1:]
        elif tail in TITLE_PREFIXES or tail in DOC_PREFIXES:
            stripped.append({"token": kept[-1], "kind": "TITLE" if tail in TITLE_PREFIXES else "DOCUMENT", "position": "END"})
            kept = kept[:-1]
        else:
            break
    return kept, stripped


def _text_candidates(text: str) -> list[dict]:
    """Hasta 3 candidatos del texto, SIN darle prioridad al primero: primero las líneas con etiqueta explícita, luego las primeras líneas plausibles. Cada uno trae si está declarado (`label`) y si queda cerca de
    una línea de contacto (solo informativo para ordenar las alternativas)."""
    lines = [ln.strip(" \t•·*-_=#►▪●") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    contact = [i for i, ln in enumerate(lines[:80]) if "@" in ln or find_phones(ln)]
    out: list[dict] = []

    def add(cand: dict | None, index: int, label: bool) -> None:
        if not cand:
            return
        key = frozenset(fold(t) for t in _core(cand["tokens"]))
        if any(c["key"] == key for c in out):
            return
        out.append({**cand, "key": key, "source": "text", "label": label, "near_contact": any(abs(index - c) <= 3 for c in contact)})

    for i, ln in enumerate(lines[:40]):
        m = _NAME_LABEL.match(ln)
        if m:
            add(_name_from_segment(m.group(1)), i, True)
    for i, ln in enumerate(lines[:15]):
        if "@" in ln or any(ch.isdigit() for ch in ln):
            continue
        for segment in _SEPARATORS.split(ln):
            add(_name_from_segment(segment), i, False)
    return out[:MAX_TEXT_CANDIDATES]


_FILE_NOISE = set(fold("cv curriculum curriculo vitae resume resumen actualizado actual final nuevo copia copy docx pdf perfil profile danwor formato").split())


def _filename_name(filename: str) -> dict | None:
    """Candidato del nombre de archivo. La mayúscula inicial NO se exige (los archivos suelen estar en minúscula), pero sí el resto de los controles de plausibilidad."""
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    base = re.sub(r"[_.]+", " ", base)
    base = re.sub(r"\(\d+\)", " ", base)
    if "," in base and base.count(",") == 1:
        cand = _name_from_segment(re.sub(r"\d+", " ", " ".join(w for w in base.split() if fold(w.strip(",")) not in _FILE_NOISE)), caps=False)
        if cand:
            return {**cand, "key": frozenset(fold(t) for t in _core(cand["tokens"])), "source": "filename", "label": False, "near_contact": False}
    words = [w for w in re.split(r"[\s\-]+", base) if w and not re.search(r"\d", w) and fold(w) not in _FILE_NOISE]
    cand = _name_from_segment(" ".join(words), caps=False)
    return {**cand, "key": frozenset(fold(t) for t in _core(cand["tokens"])), "source": "filename", "label": False, "near_contact": False} if cand else None


def filename_edge_tokens(names: list[str], *, min_files: int = 3, max_len: int = 4, limit: int = 20) -> dict:
    """DIAGNÓSTICO de solo lectura sobre NOMBRES DE ARCHIVO (metadatos de SharePoint, sin descargar nada): tokens cortos que se repiten como PRIMER o ÚLTIMO token en `min_files` archivos o más —candidatos a prefijo o
    título—. Solo devuelve el token, su posición, cuántos archivos lo llevan y su clasificación; nunca un nombre completo ni un nombre de archivo. Los tokens por debajo de `min_files` no se informan (no identifican a nadie)."""
    counts: dict[tuple[str, str], int] = {}
    considered = 0
    for name in names:
        base = name.rsplit(".", 1)[0] if "." in name else name
        base = re.sub(r"\(\d+\)", " ", re.sub(r"[_.,]+", " ", base))
        words = [w for w in re.split(r"[\s\-]+", base) if w and not re.search(r"\d", w) and fold(w) not in _FILE_NOISE]
        if len(words) < 2:
            continue
        considered += 1
        for position, word in (("START", words[0]), ("END", words[-1])):
            token = fold(word)
            if token.isalpha() and token not in PARTICLES and 2 <= len(token) <= max_len:
                counts[(token, position)] = counts.get((token, position), 0) + 1

    def classify(token: str) -> str:
        return "TITLE" if token in TITLE_PREFIXES else "DOCUMENT" if token in DOC_PREFIXES else "AMBIGUOUS" if token in AMBIGUOUS_EDGE else "UNKNOWN"

    rows = [{"token": t.upper(), "position": pos, "files": n, "share_pct": round(100.0 * n / considered, 1), "classification": classify(t)} for (t, pos), n in counts.items() if n >= min_files]
    rows.sort(key=lambda r: (-r["files"], r["token"], r["position"]))
    return {"considered_files": considered, "min_files": min_files, "max_len": max_len, "tokens": rows[:limit]}


def _local_part(email: str) -> tuple[str, list[str]]:
    raw = email.split("@")[0]
    parts = [fold(p) for p in re.split(r"[._\-+]+", raw) if p]
    return re.sub(r"[^a-z]", "", fold(raw)), parts


def is_role_mailbox(email: str) -> bool:
    """Buzón de rol o genérico («info», «ventas», «rrhh»…): el usuario o alguno de sus componentes es un rol. No identifica a una persona."""
    local, parts = _local_part(email)
    return not local or local in ROLE_MAILBOXES or any(p in ROLE_MAILBOXES for p in parts)


def email_explained(core_tokens: list[str], emails: list[str]) -> set[str]:
    """Palabras del nombre (en minúscula, sin acentos) que explica la coincidencia DISTINTIVA con el usuario de algún email; vacío si ningún email es fuerte. Coincidencia fuerte: (a) dos palabras distintas del nombre
    (3+ letras) aparecen en el usuario —explica esas más las de hasta 3 letras que sean un componente exacto del usuario—, o (b) las iniciales de las demás palabras + una palabra de 4+ letras, al principio o al final del
    usuario (`jperez`, `jcperez`, `perezj`, `juanp`) —explica todas—. Un buzón de rol (el usuario o alguno de sus componentes es «ventas», «info», «comercial»…) o una coincidencia de una sola palabra NO alcanzan."""
    toks = [fold(t) for t in core_tokens if len(fold(t)) >= 2]
    explained: set[str] = set()
    for email in emails:
        local, parts = _local_part(email)
        if not local or local in ROLE_MAILBOXES or any(p in ROLE_MAILBOXES for p in parts):
            continue
        present = {t for t in toks if len(t) >= 3 and t in local}
        if len(present) >= 2:
            explained |= present | {t for t in toks if len(t) <= 3 and t in parts}
            continue
        for i, t in enumerate(toks):
            if len(t) < 4 or t not in local:
                continue
            initials = "".join(other[0] for j, other in enumerate(toks) if j != i)
            if initials and (local.startswith(initials + t) or local.endswith(t + initials)):
                explained |= set(toks)
                break
    return explained


def email_strong(core_tokens: list[str], emails: list[str]) -> bool:
    """¿Corrobora de forma DISTINTIVA algún email al nombre? (criterio en `email_explained`)."""
    return bool(email_explained(core_tokens, emails))


def _edge_review(ident: dict, emails: list[str]) -> None:
    """Revisa los tokens de BORDE de una identidad corroborada por email (no cambia su regla ni su estado; solo registra `edge_token`, que `decide_name` convierte en bandera bloqueante si la identidad se propone):
      * AMBIGUO («RC»): si el email corrobora con fuerza las palabras restantes se ofrece la variante SIN el token (el token original queda en `edge_token`), también cuando el nombre viene de una línea con etiqueta: la etiqueta
        declara el texto, no explica un token ambiguo;
      * SIN EXPLICAR: identidad que viene SOLO del nombre de archivo y corroborada SOLO por el email, con un token de borde de 2–3 letras que el email no explica: la propuesta se mantiene tal cual."""
    toks = ident["tokens"]
    if "EMAIL_STRONG" not in ident["evidence"]:
        return
    for position, tok in (("START", toks[0]), ("END", toks[-1])):
        folded = fold(tok.strip("."))
        if _is_initial(tok) or folded in PARTICLES or not 2 <= len(folded) <= 3:
            continue
        rest = toks[1:] if position == "START" else toks[:-1]
        if len(_core(rest)) < 2:
            continue
        info = {"token": tok, "position": position, "original": " ".join(toks), "variant": " ".join(rest)}
        if folded in AMBIGUOUS_EDGE:
            if email_strong(_core(rest), emails):
                ident["edge_token"] = {**info, "kind": "AMBIGUOUS"}
                ident["tokens"], ident["key"] = list(rest), frozenset(fold(t) for t in _core(rest))
                return
        elif ident["sources"] == ["filename"] and not ident["agree"] and folded not in email_explained(_core(toks), emails):
            ident["edge_token"] = {**info, "kind": "UNEXPLAINED"}
            return


def _overlap(a: frozenset, b: frozenset) -> str:
    shared = len(a & b)
    return "STRONG" if shared >= 2 else "WEAK" if shared == 1 else "NONE"


def decide_name(text_cands: list[dict], file_cand: dict | None, emails: list[str]) -> dict:
    """Decisión de identidad por reglas EXPLICABLES (ver el docstring del módulo). Devuelve `{rule, state, proposed, identities, flags}`: `proposed` es el índice de la identidad propuesta o None (sin propuesta)."""
    identities: list[dict] = []
    for c in text_cands:
        identities.append({"tokens": c["tokens"], "key": c["key"], "sources": ["text"], "label": c["label"], "near_contact": c["near_contact"], "agree": False, "normalization": c.get("normalization"),
                           "split_certain": c.get("split_certain", False), "first": c.get("first"), "last": c.get("last")})
    if file_cand:
        merged = False
        for ident in identities:
            if _overlap(ident["key"], file_cand["key"]) == "STRONG":
                ident["sources"].append("filename")
                ident["agree"] = True
                ident["normalization"] = ident.get("normalization") or file_cand.get("normalization")
                merged = True
                break
        if not merged:
            identities.append({"tokens": file_cand["tokens"], "key": file_cand["key"], "sources": ["filename"], "label": False, "near_contact": False, "agree": False, "normalization": file_cand.get("normalization"),
                               "split_certain": file_cand.get("split_certain", False), "first": file_cand.get("first"), "last": file_cand.get("last")})
    for ident in identities:
        ev = []
        if ident["label"]:
            ev.append("LABEL")
        if email_strong(_core(ident["tokens"]), emails):
            ev.append("EMAIL_STRONG")
        if ident["agree"]:
            ev.append("TEXT_FILE_AGREE")
        if ident["near_contact"]:
            ev.append("NEAR_CONTACT")
        ident["evidence"] = ev
        _edge_review(ident, emails)
        corroborated = "EMAIL_STRONG" in ev or ("TEXT_FILE_AGREE" in ev and "LABEL" in ev)
        ident["state"] = "CORROBORATED" if corroborated else "DECLARED" if "LABEL" in ev else "UNANCHORED_AGREEMENT" if ident["agree"] else "STRUCTURAL"
    corroborated = [i for i, d in enumerate(identities) if d["state"] == "CORROBORATED"]
    declared = [i for i, d in enumerate(identities) if d["state"] == "DECLARED"]
    flags: list[str] = []
    proposed: int | None = None
    if len(corroborated) == 1 and not [i for i in declared if i != corroborated[0]]:
        proposed = corroborated[0]
        ev = identities[proposed]["evidence"]
        rule, state = ("R1" if "TEXT_FILE_AGREE" in ev else "R2"), "CORROBORATED"
        if identities[proposed]["sources"] == ["filename"]:
            flags.append(NAME_ORDER_UNCERTAIN)                                  # el orden de un nombre que solo sale del archivo («Perez Juan» / «Juan Perez») no es confiable
    elif len(corroborated) == 0 and len(declared) == 1:
        proposed, rule, state = declared[0], "R3", "DECLARED"
        flags.append(NAME_DECLARED)                                             # declarada explícitamente en el documento, SIN corroboración independiente
    elif len(corroborated) > 1 or len(declared) > 1 or (corroborated and declared):
        rule, state = "R4", "CONFLICT"
        flags.append(NAME_CONFLICT)
    elif not identities:
        rule, state = "R6", "MISSING"
        flags.append(NAME_MISSING)
    elif len(identities) == 1:
        rule, state = "R5", "UNCORROBORATED"
        flags.append(NAME_UNCORROBORATED)
    elif any(_overlap(identities[i]["key"], identities[j]["key"]) == "WEAK" for i in range(len(identities)) for j in range(i + 1, len(identities))):
        rule, state = "R5", "PARTIAL"
        flags.append(NAME_PARTIAL_MATCH)                                        # coincidencia parcial débil (una sola palabra): no corrobora nada
    else:
        rule, state = "R4", "CONFLICT"
        flags.append(NAME_CONFLICT)
    if proposed is not None and identities[proposed].get("edge_token"):
        flags.append(NAME_EDGE_TOKEN_UNEXPLAINED)                               # bloqueante: el owner revisa el token de borde antes de aceptar la propuesta
    if proposed is not None and len(identities) > 1:
        flags.append(NAME_ALTERNATIVE)
    return {"rule": rule, "state": state, "proposed": proposed, "identities": identities, "flags": flags}


def _public_identities(decision: dict) -> list[dict]:
    return [{"tokens": " ".join(d["tokens"]), "sources": d["sources"], "evidence": d["evidence"], "state": d["state"],
             **({"normalization": d["normalization"]} if d.get("normalization") else {}), **({"edge_token": d["edge_token"]} if d.get("edge_token") else {})} for d in decision["identities"]]


def _build(emails: list[str], phone_info: dict | None, decision: dict, extra_flags: list[str], *, limits: list[str] | None = None, rejected: list[dict] | None = None) -> dict:
    flags = list(extra_flags) + list(decision["flags"])
    first = last = None
    corroborated_by_email = False
    chosen = decision["identities"][decision["proposed"]] if decision["proposed"] is not None else None
    if chosen:
        if chosen["split_certain"]:
            first, last = chosen["first"], chosen["last"]
        else:
            first, last, uncertain = _split(chosen["tokens"])
            if uncertain:
                flags.append(NAME_SPLIT_AMBIGUOUS)
        corroborated_by_email = "EMAIL_STRONG" in chosen["evidence"]
    blocking = sorted(f for f in set(flags) if f in BLOCKING)
    advisory = sorted(f for f in set(flags) if f in ADVISORY)
    alternatives = _public_identities(decision)
    return {
        "extractor_version": EXTRACTOR_VERSION,
        "first_name": first, "last_name": last, "email": emails[0] if len(emails) == 1 else None,
        "phone": (phone_info or {}).get("display"), "phone_digits": (phone_info or {}).get("digits"),
        "name_corroborated_by_email": corroborated_by_email,
        "name_state": decision["state"],
        "name_decision": {"rule": decision["rule"], "state": decision["state"], "proposed": decision["proposed"], "alternatives": alternatives,
                          **({"rejected_candidates": rejected} if rejected else {}), **({"limits": limits} if limits else {})},
        "evidence": {k: v for k, v in (("email", "text" if len(emails) == 1 else None), ("phone", "text" if phone_info else None), ("name", chosen["sources"] if chosen else None)) if v},
        "candidates": {"emails": emails[:3], "phones": [], "names": {src: " ".join(d["tokens"]) for d in decision["identities"] for src in d["sources"] if True}},
        "flags": sorted(set(flags)), "blocking": blocking, "advisory": advisory,
        "state": "NEEDS_REVIEW" if blocking else "COMPLETE",
    }


def _split(tokens: list[str]) -> tuple[str, str, bool]:
    """(nombre, apellido, partición_incierta) para «Nombre Apellido…»: con 2 palabras no hay duda; con más, la partición es una suposición (se avisa). Las iniciales no cuentan para la partición: quedan con el
    nombre si caen antes del corte y con el apellido si caen después."""
    lead = next((i for i, t in enumerate(tokens) if not _is_initial(t)), len(tokens))
    if lead and lead < len(tokens):                                           # «J. Pérez Gómez»: la inicial inicial ES el nombre de pila
        return " ".join(tokens[:lead]), " ".join(tokens[lead:]), False
    index = [i for i, t in enumerate(tokens) if not _is_initial(t)]
    plain = [tokens[i] for i in index]
    first, last, uncertain = _split_plain(plain)
    if len(plain) == len(tokens):
        return first, last, uncertain
    cut = index[len(first.split())]
    return " ".join(tokens[:cut]), " ".join(tokens[cut:]), uncertain


def _split_plain(tokens: list[str]) -> tuple[str, str, bool]:
    if len(tokens) == 2:
        return tokens[0], tokens[1], False
    if len(tokens) == 3:
        return tokens[0], " ".join(tokens[1:]), True
    return " ".join(tokens[:2]), " ".join(tokens[2:]), True


def extract_fields(text: str, filename: str) -> dict:
    """Los cuatro campos, con candidatos, evidencia y banderas. Nunca devuelve el texto."""
    emails = find_emails(text)
    phones = find_phones(text)
    extra: list[str] = []
    if not emails:
        extra.append(EMAIL_MISSING)
    elif len(emails) > 1:
        extra.append(EMAIL_MULTIPLE)
    if not phones:
        extra.append(PHONE_MISSING)
    elif len(phones) > 1:
        extra.append(PHONE_MULTIPLE)
    decision = decide_name(_text_candidates(text), _filename_name(filename), emails)
    out = _build(emails, phones[0] if phones else None, decision, extra)
    out["candidates"]["phones"] = [p["display"] for p in phones[:3]]
    return out


def rearbitrate(stored: dict, filename: str) -> dict:
    """Reevalúa con las reglas de la versión 2 un resultado YA GUARDADO (sin releer el origen), usando solo lo persistido: el único candidato de texto guardado (sin saber si estaba etiquetado y sin los demás
    candidatos del documento), el nombre de archivo original y los emails candidatos. Devuelve un resultado de la versión 2 con sus límites explícitos. Email y celular se copian tal cual."""
    if stored.get("failure"):
        return {**stored, "extractor_version": EXTRACTOR_VERSION, "name_state": "MISSING", "name_decision": {"rule": "R6", "state": "MISSING", "proposed": None, "alternatives": [], "limits": ["READ_FAILURE"]}}
    cands = stored.get("candidates") or {}
    emails = list(cands.get("emails") or ([stored["email"]] if stored.get("email") else []))
    text_name = (cands.get("names") or {}).get("text")
    text_cands, rejected = [], []
    if text_name:
        cand = _name_from_segment(text_name)
        if cand:
            text_cands.append({**cand, "key": frozenset(fold(t) for t in _core(cand["tokens"])), "source": "text", "label": False, "near_contact": False})
        else:
            rejected.append({"tokens": text_name, "source": "text", "reasons": _reject_reasons(text_name) or ["NOT_A_NAME"]})
    decision = decide_name(text_cands, _filename_name(filename), emails)
    keep = [f for f in (stored.get("flags") or []) if f in (EMAIL_MISSING, EMAIL_MULTIPLE, PHONE_MISSING, PHONE_MULTIPLE)]
    phone_info = {"display": stored.get("phone"), "digits": stored.get("phone_digits")} if stored.get("phone") else None
    out = _build(emails, phone_info, decision, keep, limits=["OFFLINE_REEVALUATION", "ONLY_FIRST_TEXT_CANDIDATE_STORED", "LABEL_UNKNOWN"], rejected=rejected)
    out["email"], out["candidates"]["phones"] = stored.get("email"), list(cands.get("phones") or [])
    return out


def extract(filename: str, data: bytes) -> dict:
    """Texto + campos. Las fallas de lectura se devuelven como resultado (`failure`) para que el llamador las registre por documento sin interrumpir el lote."""
    try:
        text = extract_text(filename, data)
    except ExtractionFailure as exc:
        return {"extractor_version": EXTRACTOR_VERSION, "failure": exc.code, "flags": [exc.code], "blocking": [exc.code], "advisory": [], "state": "NEEDS_REVIEW", "name_state": "MISSING",
                "first_name": None, "last_name": None, "email": None, "phone": None, "phone_digits": None, "evidence": {}, "candidates": {"emails": [], "phones": [], "names": {}}}
    return extract_fields(text, filename)
