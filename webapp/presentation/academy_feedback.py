"""
academy_feedback.py — Presentación del cierre/feedback de Danwor Academy.

Construye el fragmento HTML del cierre de Academy (`feedback_html`) y su inyección
de assets para el iframe standalone (`feedback_assets`). Extraído de
``webapp/main.py`` (Fase 2E): lo consumen el router de Academy
(`webapp/routers/academy.py`) y ``GET /pack-assets`` — por eso vive fuera de ambos.

Clasificación: COPILOT / ACADEMY PRESENTATION. NO importa ``webapp.main``, NO
define rutas, NO persiste feedback, NO conoce FEEDBACK_LOG_DIR ni auth/session ni
resolución de pack.

Componente: el cierre ES el componente transversal de Feedback del Hub
(`partials/dw_feedback.html`) a través de su adaptador
`partials/academy_feedback.html`. Antes era un string propio que solo copiaba las
clases de `module_feedback.html`: compartía el CSS pero no el componente, y por
eso la evolución del Feedback (selector de reacciones, «Tengo una idea», SVG) no
le llegaba. Ahora cualquier evolución del componente se propaga sola.
"""
from __future__ import annotations

from webapp.templating import templates


def feedback_html(stem: str, pack: str, *, submitted: bool = False, rating: str = "", form: bool = False, error: str = "") -> str:
    """Estado del cierre (reposo, comentario del `down` histórico o agradecimiento), renderizado por el componente compartido."""
    return templates.get_template("partials/academy_feedback.html").render(
        stem=stem, pack=pack, submitted=submitted, rating=rating, form=form, error=error,
    )


def feedback_assets(stem: str, pack: str) -> str:
    """Assets + cierre inicial que /pack-assets inyecta antes de `</body>` del recorrido.

    Transporte e interacción: los MISMOS del Hub — htmx (como `catalog_guide_slides.html`) y `hub_reactions.js` — en lugar de un
    fetch() propio. El cierre se inyecta como markup para que htmx lo procese al cargar."""
    initial = feedback_html(stem, pack)
    return """
<link rel="stylesheet" href="/static/feedback.css" />
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<script defer src="/static/hub_reactions.js"></script>
<style>
/* feedback.css es la ÚNICA fuente del componente de Feedback (cierre, selector de reacciones, formularios): lo usan fin de módulo,
   catálogo, how-to Y este cierre de Academy — nada de tipografía/botones propio acá (ver catalog_guide_slides.html, mismo shim).
   Shim: feedback.css usa las variables del copiloto (--card/--soft); dw-academy.css no las define con ese nombre (usa --bg-card/--ink-soft). */
:root{ --card: var(--bg-card); --soft: var(--ink-soft); }
/* Reveal al llegar al final: propio de Academy (storytelling de una sola scrolleada); el cierre de módulo no lo necesita porque ya vive
   en un documento normal. Solo anima el wrapper — el resto es feedback.css. */
#academy-feedback { opacity: 0; transform: translateY(22px); transition: opacity .7s var(--ease-out, ease), transform .7s var(--ease-out, ease); }
#academy-feedback.is-visible { opacity: 1; transform: none; }
/* .module-feedback trae border-top:0 a propósito: en módulo esa línea la aporta el border-bottom de la ÚLTIMA sección de contenido. En
   Academy el último .stop/.topic tiene border-bottom:none (":last-of-type"), así que acá SÍ hace falta un borde propio. */
#academy-feedback { border-top: 1px solid var(--line); }
</style>
<script>
(function() {
  function observe(node) {
    if (!node || node.classList.contains('is-visible')) return;
    if (!('IntersectionObserver' in window)) { node.classList.add('is-visible'); return; }
    var io = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) { if (e.isIntersecting) { e.target.classList.add('is-visible'); io.unobserve(e.target); } });
    }, { threshold: 0.2 });
    io.observe(node);
  }
  function wireRail(fb) {
    // Suma un stop final al rail ("¿Qué te pareció?") para que el lector VEA que hay un tramo más después del último bullet.
    var rail = document.getElementById('rail');
    if (!fb || !rail) return;
    if (!rail.querySelector('[data-af-rail]')) {
      var anchor = document.createElement('a');
      anchor.href = '#academy-feedback';
      anchor.setAttribute('data-af-rail', '');
      anchor.setAttribute('data-label', 'Cierre');
      anchor.innerHTML = '<span>¿Qué te pareció?</span>';
      anchor.addEventListener('click', function(e) {
        e.preventDefault();
        var t = document.getElementById('academy-feedback');
        if (t) t.scrollIntoView({ behavior: 'smooth' });
      });
      rail.appendChild(anchor);
    }
    if (!('IntersectionObserver' in window)) return;
    var io = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) {
        var dot = rail.querySelector('[data-af-rail]');
        if (!dot) return;
        if (e.isIntersecting) {
          rail.querySelectorAll('a').forEach(function(x) { x.classList.remove('active'); });
          dot.classList.add('active');
        } else {
          dot.classList.remove('active');
        }
      });
    }, { threshold: 0.4 });
    io.observe(fb);
  }
  function hookup(node) { observe(node); wireRail(node); }
  // Tras cada swap de htmx (reacción, idea, comentario) el nuevo cierre recupera su reveal y el stop del rail.
  document.addEventListener('htmx:afterSettle', function() { hookup(document.getElementById('academy-feedback')); });
  document.addEventListener('DOMContentLoaded', function() { hookup(document.getElementById('academy-feedback')); });
})();
</script>
""" + initial
