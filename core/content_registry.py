"""Resolucion de paquetes de contenido del copiloto.

El core de la app es unico; el conocimiento vive en content packs.
Un pack puede extender a otro para reutilizar modulos, arboles, assets o
templates sin duplicarlos. La regla de resolucion es simple:

1. Buscar primero en el pack activo.
2. Si no existe, buscar en su pack padre (`extends`).
3. Si tampoco existe, caer al layout historico del proyecto.

Esto permite trabajar en `bdc_next` manteniendo vivo `datasphere_core`.
"""

from __future__ import annotations

import functools
from pathlib import Path
import re

from config import CONTENT_PACK, CONTENT_PACKS_DIR


PACK_KINDS = ("content", "trees", "assets", "templates")
CONTENT_PACK_SESSION_KEY = "content_pack_selector"

# Pack por defecto de la webapp. Se mantiene como literal (no `CONTENT_PACK`)
# para preservar EXACTAMENTE el fallback histórico del helper `_pack` de la
# webapp: caer a "datasphere_core" con independencia de DANWOR_CONTENT_PACK.
DEFAULT_PACK = "datasphere_core"


@functools.lru_cache(maxsize=64)
def _read_pack_meta(pack_name: str) -> dict[str, str]:
    # Memoizado: se llama ~6-10 veces por rerun (content_pack_chain, roots_for_kind,
    # include/hide keys). pack.yaml es estático en runtime. El dict devuelto es
    # compartido — los consumidores solo lo leen, nunca lo mutan.
    path = CONTENT_PACKS_DIR / pack_name / "pack.yaml"
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    meta: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        meta[key] = value.strip().strip("'\"")
    return meta


def _split_csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def active_content_pack() -> str:
    """Pack activo de la sesion, con fallback a la configuracion del proceso."""
    try:
        import streamlit as st
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        if get_script_run_ctx() is not None:
            selected = str(st.session_state.get(CONTENT_PACK_SESSION_KEY, "")).strip()
            if selected and (_pack_root(selected) / "pack.yaml").exists():
                return selected
    except Exception:
        pass
    return CONTENT_PACK


def available_content_packs() -> list[str]:
    """Packs instalados que poseen un pack.yaml valido."""
    if not CONTENT_PACKS_DIR.exists():
        return [CONTENT_PACK]
    packs = sorted(
        path.name
        for path in CONTENT_PACKS_DIR.iterdir()
        if path.is_dir() and (path / "pack.yaml").exists()
    )
    return packs or [CONTENT_PACK]


def resolve_pack(value: str) -> str:
    """Valida un pack pedido por el caller contra los packs instalados y cae a
    `DEFAULT_PACK` si no existe. Es la semántica exacta del helper `_pack` que
    vivía en webapp/main.py (validación + fallback), reubicada acá porque el
    contexto de content-pack pertenece al registro de contenido, no a la web."""
    return value if value in available_content_packs() else DEFAULT_PACK


def content_pack_chain(pack_name: str | None = None) -> list[str]:
    """Devuelve pack activo + padres, sin repetir ni entrar en ciclos."""
    pack = (pack_name or active_content_pack() or "datasphere_core").strip() or "datasphere_core"
    chain: list[str] = []
    seen: set[str] = set()
    while pack and pack not in seen:
        seen.add(pack)
        chain.append(pack)
        meta = _read_pack_meta(pack)
        pack = (meta.get("extends") or "").strip()
    return chain


def content_pack_label(pack_name: str | None = None) -> str:
    pack = (pack_name or active_content_pack() or "").strip()
    meta = _read_pack_meta(pack)
    return meta.get("label") or pack or "content"


def content_pack_short_label(pack_name: str | None = None) -> str:
    pack = (pack_name or active_content_pack() or "").strip()
    meta = _read_pack_meta(pack)
    return meta.get("short_label") or meta.get("label") or pack or "content"


def _kind_dir(root: Path, kind: str) -> Path:
    return root / kind


def _pack_root(pack_name: str) -> Path:
    return CONTENT_PACKS_DIR / pack_name


def roots_for_kind(kind: str, pack_name: str | None = None) -> list[Path]:
    """Directorios candidatos para un tipo de contenido, en orden de prioridad."""
    roots: list[Path] = []
    for pack in content_pack_chain(pack_name):
        path = _kind_dir(_pack_root(pack), kind)
        if path.exists():
            roots.append(path)
    # Resolución pack-pura: sin fallback a carpetas raíz legacy (BASE_DIR/kind).
    # Los packs (con su cadena de herencia) son la única fuente de verdad de la webapp.
    return roots


def active_dir_for_kind(kind: str, pack_name: str | None = None) -> Path:
    """Directorio preferido para escritura o paths legacy."""
    roots = roots_for_kind(kind, pack_name)
    if roots:
        return roots[0]
    return _kind_dir(_pack_root(pack_name or active_content_pack()), kind)


def resolve_pack_file(kind: str, relative: str | Path, pack_name: str | None = None) -> Path:
    rel = Path(relative)
    for root in roots_for_kind(kind, pack_name):
        candidate = root / rel
        if candidate.exists():
            return candidate
    return active_dir_for_kind(kind, pack_name) / rel


def _overlay_key(kind: str, root: Path, path: Path) -> str:
    """Clave de override entre packs.

    Para markdowns de contenido, la identidad metodologica es la secuencia
    visible (00, 01, A04, etc.), no el nombre completo del archivo. Asi un pack
    hijo puede reemplazar `01_Arquitectura...` por `01_Estrategia...` sin que
    aparezcan ambos modulos en la navegacion.
    """
    try:
        rel = path.relative_to(root)
    except Exception:
        return path.name.lower()

    rel_key = str(rel).replace("\\", "/").lower()
    if kind != "content" or path.suffix.lower() != ".md":
        return rel_key

    parent = path.parent.name.lower()
    stem = path.stem
    if parent == "apendices":
        match = re.match(r"^(a\d+)_", stem, flags=re.I)
        if match:
            return f"apendices/{match.group(1).lower()}.md"
        return rel_key

    match = re.match(r"^(\d+)_", stem)
    if match:
        return f"{match.group(1)}.md"
    return rel_key


def _content_hide_keys(pack_name: str | None = None) -> set[str]:
    """Secuencias de contenido heredado que el pack activo quiere ocultar.

    En pack.yaml:
      content_hide: 03, 04, A02

    Esto no borra ni bloquea archivos propios del pack activo; solo evita que
    aparezcan entradas heredadas cuando la estructura BDC Next no conserva todos
    los modulos/apendices del MVP.
    """
    meta = _read_pack_meta(pack_name or active_content_pack())
    out: set[str] = set()
    for raw in _split_csv(meta.get("content_hide")):
        token = raw.strip().lower()
        if not token:
            continue
        token = token.removesuffix(".md")
        if token.startswith("apendices/"):
            token = token.split("/", 1)[1]
        if re.match(r"^a\d+$", token, flags=re.I):
            out.add(f"apendices/{token.lower()}.md")
        elif re.match(r"^\d+$", token):
            out.add(f"{token}.md")
        else:
            out.add(token)
    return out


def _content_module_include_keys(pack_name: str | None = None) -> set[str]:
    """Secuencias declaradas como modulos principales del pack activo.

    En pack.yaml:
      content_modules: 00, 01, 02, 03, 04, 05

    Si no se define, se muestran todos los markdowns raiz del pack resuelto.
    Esto permite conservar archivos legacy en content/*.md sin mezclarlos con
    la navegacion principal cuando ya fueron promovidos a apendices.
    """
    meta = _read_pack_meta(pack_name or active_content_pack())
    out: set[str] = set()
    for raw in _split_csv(meta.get("content_modules")):
        token = raw.strip().lower().removesuffix(".md")
        if re.match(r"^\d+$", token):
            out.add(f"{token}.md")
        else:
            out.add(token)
    return out


def _root_content_nav_key(path: Path) -> str:
    stem = path.stem
    match = re.match(r"^(\d+)_", stem)
    if match:
        return f"{match.group(1)}.md"
    return path.name.lower()


def iter_pack_files(kind: str, pattern: str = "*", pack_name: str | None = None) -> list[Path]:
    """Lista archivos con override por ruta relativa del pack activo."""
    resolved: dict[str, Path] = {}
    selected_pack = pack_name or active_content_pack()
    roots = roots_for_kind(kind, selected_pack)
    content_mode = (_read_pack_meta(selected_pack).get("content_mode") or "overlay").strip().lower()
    hidden_content = _content_hide_keys(selected_pack) if kind == "content" else set()

    if kind == "content" and content_mode == "replace" and roots:
        roots = roots[:1]

    active_root = roots[0] if roots else None
    for root in reversed(roots):
        for path in root.glob(pattern):
            if path.is_file():
                key = _overlay_key(kind, root, path)
                if kind == "content" and key in hidden_content and root != active_root:
                    continue
                resolved[key] = path
    return sorted(resolved.values(), key=lambda p: p.name.lower())


def iter_content_module_paths(pack_name: str | None = None) -> list[Path]:
    include_keys = _content_module_include_keys(pack_name)
    paths = [
        path for path in iter_pack_files("content", "*.md", pack_name)
        if path.parent.name.lower() not in ("apendices", "academy")
    ]
    if include_keys:
        paths = [path for path in paths if _root_content_nav_key(path) in include_keys]
    return paths


def iter_content_appendix_paths(pack_name: str | None = None) -> list[Path]:
    return iter_pack_files("content", "apendices/*.md", pack_name)


def iter_content_academy_paths(pack_name: str | None = None) -> list[Path]:
    """Páginas de Danwor Academy (content/academy/*.md).

    Área desconectada de la navegación metodológica: cada .md es un envoltorio
    fino que apunta (frontmatter academy_asset) a un HTML de storytelling
    autónomo bajo assets/academy/. No se mezcla con módulos ni apéndices.
    """
    return iter_pack_files("content", "academy/*.md", pack_name)


def iter_content_markdown_paths(pack_name: str | None = None) -> list[Path]:
    return iter_content_module_paths(pack_name) + iter_content_appendix_paths(pack_name)


def resolve_content_path(stem: str, pack_name: str | None = None) -> Path:
    stem = (stem or "").strip()
    direct = resolve_pack_file("content", f"{stem}.md", pack_name)
    if direct.exists():
        return direct
    appendix = resolve_pack_file("content", Path("apendices") / f"{stem}.md", pack_name)
    if appendix.exists():
        return appendix
    return resolve_pack_file("content", Path("academy") / f"{stem}.md", pack_name)
