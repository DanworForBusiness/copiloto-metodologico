"""
ui/content_panel.py — Panel izquierdo: contenido del módulo + key topics.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from config import IA_ENABLED_MODULES
from core.content import (
    key_topics_from_module_with_start,
    extract_section_by_heading,
    strip_first_h1,
    demote_markdown_headings,
    render_md_with_image_zoom,
    split_front_matter,
    section_number_base_for_path,
)
from core.state import AppState

_FONT_SIZE_KEY = "_content_font_size"
_FONT_MIN      = 12
_FONT_MAX      = 18
_FONT_DEFAULT  = 14


def render_content_panel(
    selected_stem: str,
    selected_path: str,
    context: str,
) -> None:
    st.markdown(
        "<div class='dw-content-shell-marker'></div>"
        "<div class='dw-panel-heading'><span>Contenido del módulo</span></div>",
        unsafe_allow_html=True,
    )

    # ── Scroll a heading vía scrollIntoView ───────────────────────────────────
    _slug = st.session_state.get("_scroll_to_slug")
    if False and _slug:
        # Los headings viven dentro de st.container(height=620), que Streamlit
        # renderiza como un div con overflow:auto — su propio scroll context.
        # Problema: scrollIntoView no actúa si el container está en scrollTop=0
        # (posición inicial) porque el navegador lo considera ya "en viewport".
        # Fix: forzar scrollTop=1 para "despertar" el container antes de scrollIntoView.
        _safe_slug = _slug.replace("'", "\\'").replace('"', '\\"')
        st.components.v1.html(
            f"""<script>
(function() {{
  function tryScroll(attempts) {{
    var el = window.parent.document.getElementById('{_safe_slug}');
    if (!el) {{
      if (attempts > 0) setTimeout(function() {{ tryScroll(attempts - 1); }}, 120);
      return;
    }}
    // Encontrar el scroll container (primer ancestro con overflow:auto/scroll)
    var container = el.parentElement;
    while (container && container !== window.parent.document.body) {{
      var style = window.parent.getComputedStyle(container);
      if (style.overflowY === 'auto' || style.overflowY === 'scroll') break;
      container = container.parentElement;
    }}
    // Despertar el container si está en posición inicial
    if (container && container.scrollTop === 0) {{
      container.scrollTop = 1;
    }}
    // Ahora scrollIntoView funciona correctamente
    setTimeout(function() {{
      el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    }}, 50);
  }}
  tryScroll(12);
}})();
</script>""",
            height=0,
        )

    _, body_md = split_front_matter(context)
    md_dir     = Path(selected_path).parent
    section_base = section_number_base_for_path(selected_path)
    topics     = key_topics_from_module_with_start(context, level=2, max_topics=8, section_start=section_base)

    # ── Tamaño de fuente — inyectar CSS global via :root ──────────────────────
    font_size = st.session_state.get(_FONT_SIZE_KEY, _FONT_DEFAULT)
    st.markdown(f"""
<style>
div[data-testid="stMainBlockContainer"] div[data-testid="stMarkdownContainer"] p,
div[data-testid="stMainBlockContainer"] div[data-testid="stMarkdownContainer"] li,
div[data-testid="stMainBlockContainer"] div[data-testid="stMarkdownContainer"] td,
div[data-testid="stMainBlockContainer"] div[data-testid="stMarkdownContainer"] th,
div[data-testid="stMainBlockContainer"] div[data-testid="stMarkdownContainer"] code,
div[data-testid="stMainBlockContainer"] .dw-callout__body,
div[data-testid="stMainBlockContainer"] [data-baseweb="select"] div[value],
div[data-testid="stMainBlockContainer"] [data-baseweb="select"] input,
div[data-testid="stMainBlockContainer"] div[data-testid="stCaptionContainer"] p,
div[data-testid="stMainBlockContainer"] div[data-testid="stExpander"] summary p {{
    font-size: {font_size}px !important;
    line-height: 1.5 !important;
}}
</style>
""", unsafe_allow_html=True)

    # ── Key topics ─────────────────────────────────────────────────────────────
    st.markdown("<div class='dw-topic-label'>Key topics</div>", unsafe_allow_html=True)

    # Los controles de fuente viven junto al pinner de cada encabezado. Así los
    # key topics recuperan ancho útil y quedan como navegación pura del módulo.
    # Cada topic recibe un peso mínimo según su texto. Así evitamos que cinco
    # topics cortos salten a una segunda línea solo porque el layout estaba
    # limitado a cuatro columnas fijas.
    n_topics = min(len(topics), 8) if topics else 0
    visible_topics = topics[:8] if topics else []

    def _topic_weight(topic: dict) -> float:
        label = (topic.get("label") or "").strip()
        return max(0.58, min(1.2, len(label) / 18))

    topic_weights = [_topic_weight(t) for t in visible_topics] or [1]

    topic_cols = st.columns(topic_weights, gap="small")

    if visible_topics and topic_cols:
        for idx, t in enumerate(visible_topics):
            label   = (t.get("label") or "").strip()
            heading = (t.get("heading") or label).strip()
            short   = label if len(label) <= 18 else label[:15] + "…"
            with topic_cols[idx]:
                if st.button(
                    short,
                    help=heading,
                    use_container_width=True,
                    key=f"topic_{selected_stem}_{idx}",
                ):
                    section_md = extract_section_by_heading(body_md, heading, level=(2, 3)) or ""
                    AppState.set_selected_topic(heading, section_md)

    # ── Markdown content ───────────────────────────────────────────────────────
    with st.container(height=660):
        marker_id = f"dw-content-surface-{selected_stem}"
        st.markdown(f"<div id='{marker_id}' class='dw-content-surface-marker'></div>", unsafe_allow_html=True)
        st.components.v1.html(
            f"""<script>
(function() {{
  function paintContentSurface(attempts) {{
    var marker = window.parent.document.getElementById('{marker_id}');
    if (!marker) {{
      if (attempts > 0) setTimeout(function() {{ paintContentSurface(attempts - 1); }}, 120);
      return;
    }}

    var node = marker.parentElement;
    var scrollBox = null;
    while (node && node !== window.parent.document.body) {{
      var style = window.parent.getComputedStyle(node);
      if (style.overflowY === 'auto' || style.overflowY === 'scroll') {{
        scrollBox = node;
        break;
      }}
      node = node.parentElement;
    }}

    if (!scrollBox) return;

    var target = marker.closest('div[data-testid="stVerticalBlockBorderWrapper"]') || scrollBox;

    // Si el ancestro detectado incluye el panel derecho, es demasiado amplio:
    // no lo pintamos para evitar blanquear el shell completo de la app.
    if (target.querySelector && target.querySelector('.dw-tools-shell-marker')) return;

    target.style.background = '#ffffff';
    target.style.backgroundColor = '#ffffff';
    target.style.borderRadius = '8px';
  }}
  paintContentSurface(12);
}})();
</script>""",
            height=0,
        )
        display_md = demote_markdown_headings(strip_first_h1(body_md), shift=2)
        render_md_with_image_zoom(
            display_md,
            base_dir=md_dir,
            key_prefix=f"mdimg_{selected_stem}",
            section_start=section_base,
            show_pinners=selected_stem in IA_ENABLED_MODULES,
        )
        _scroll_content_to_top = st.session_state.pop("_scroll_content_to_top", False)
        if _scroll_content_to_top:
            st.components.v1.html(
                f"""<script>
(function() {{
  function scrollContentTop(attempts) {{
    var marker = window.parent.document.getElementById('{marker_id}');
    if (!marker) {{
      if (attempts > 0) setTimeout(function() {{ scrollContentTop(attempts - 1); }}, 120);
      return;
    }}

    var container = marker.parentElement;
    while (container && container !== window.parent.document.body) {{
      var style = window.parent.getComputedStyle(container);
      if (style.overflowY === 'auto' || style.overflowY === 'scroll') break;
      container = container.parentElement;
    }}

    if (container) {{
      setTimeout(function() {{
        container.scrollTop = 0;
      }}, 80);
    }}
  }}
  scrollContentTop(16);
}})();
</script>""",
                height=0,
            )
        _slug_after_render = st.session_state.pop("_scroll_to_slug", None)
        if _slug_after_render:
            _safe_slug = _slug_after_render.replace("'", "\\'").replace('"', '\\"')
            st.components.v1.html(
                f"""<script>
(function() {{
  function tryScroll(attempts) {{
    var el = window.parent.document.getElementById('{_safe_slug}');
    if (!el) {{
      if (attempts > 0) setTimeout(function() {{ tryScroll(attempts - 1); }}, 120);
      return;
    }}
    var container = el.parentElement;
    while (container && container !== window.parent.document.body) {{
      var style = window.parent.getComputedStyle(container);
      if (style.overflowY === 'auto' || style.overflowY === 'scroll') break;
      container = container.parentElement;
    }}
    if (container && container.scrollTop === 0) {{
      container.scrollTop = 1;
    }}
    setTimeout(function() {{
      el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    }}, 80);
  }}
  tryScroll(16);
}})();
</script>""",
                height=0,
            )
