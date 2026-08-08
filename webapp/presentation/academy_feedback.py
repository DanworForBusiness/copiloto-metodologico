"""
academy_feedback.py — Presentación del cierre/feedback de Danwor Academy.

Construye el fragmento HTML autocontenido del cierre de Academy (`feedback_html`)
y su inyección de assets para el iframe standalone (`feedback_assets`). Extraído
de ``webapp/main.py`` (Fase 2E): lo consumen tanto el router de Academy
(`webapp/routers/academy.py`) como, temporalmente, ``GET /pack-assets`` que sigue
en ``main`` — por eso vive fuera de ambos routers.

Clasificación: COPILOT / ACADEMY PRESENTATION. Que ``/pack-assets`` lo consuma no
lo vuelve PLATFORM. NO importa ``webapp.main``, NO define rutas, NO persiste
feedback, NO conoce FEEDBACK_LOG_DIR ni auth/session ni resolución de pack.
"""
from __future__ import annotations

from urllib.parse import quote


def feedback_html(stem: str, pack: str, *, submitted: bool = False, rating: str = "", form: bool = False, error: str = "") -> str:
    """Fragmento autocontenido para el iframe de Danwor Academy.

    Mismas clases que partials/module_feedback.html (estilo único en
    feedback.css) para que el cierre de Academy se vea IDÉNTICO al de módulo/
    catálogo — nada de tipografía/botones propio acá. Solo cambia el copy
    ("recorrido" en vez de "módulo") y el transporte: este HTML no carga htmx,
    así que el submit va por fetch()+outerHTML (ver feedback_assets)
    en vez de hx-post."""
    safe_stem = quote(stem, safe="")
    safe_pack = quote(pack, safe="")
    if submitted:
        icon = "&#128077;" if rating == "up" else "&#128078;"
        return (
            '<section id="academy-feedback" class="module-feedback is-visible">'
            '<div class="module-feedback__inner module-feedback__inner--thanks">'
            '<p class="module-feedback__eyebrow"><span></span>Gracias por tu recorrido</p>'
            f'<p class="module-feedback__prompt"><span>{icon}</span> ¡Gracias!</p>'
            '<p class="module-feedback__note">Registramos tu opinión sobre este contenido.</p>'
            '</div></section>'
        )
    if form:
        error_html = f'<p class="module-feedback__error">{error}</p>' if error else ""
        return (
            '<section id="academy-feedback" class="module-feedback is-visible">'
            '<div class="module-feedback__inner module-feedback__inner--form">'
            '<p class="module-feedback__eyebrow"><span></span>Contanos un poco más</p>'
            '<p class="module-feedback__prompt">¿Qué faltó o qué mejorarías?</p>'
            '<form data-academy-feedback-form method="post" '
            f'action="/academy/{safe_stem}/feedback/submit">'
            f'<input type="hidden" name="pack" value="{safe_pack}">'
            '<input type="hidden" name="rating" value="down">'
            f'{error_html}'
            '<textarea name="comment" rows="3" placeholder="Ej.: faltó contexto, un ejemplo práctico, más claridad o profundidad..." required></textarea>'
            '<div class="module-feedback__actions">'
            f'<button type="button" class="module-feedback__ghost" data-academy-feedback-cancel data-url="/academy/{safe_stem}/feedback/cancel" data-pack="{safe_pack}">Cancelar</button>'
            '<button type="submit" class="module-feedback__primary">Enviar</button>'
            '</div></form></div></section>'
        )
    return (
        '<section id="academy-feedback" class="module-feedback">'
        '<div class="module-feedback__inner">'
        '<p class="module-feedback__eyebrow"><span></span>Fin del recorrido</p>'
        '<p class="module-feedback__prompt">¿Qué te pareció este contenido?</p>'
        '<form data-academy-feedback-form method="post" '
        f'action="/academy/{safe_stem}/feedback">'
        f'<input type="hidden" name="pack" value="{safe_pack}">'
        '<div class="module-feedback__reactions">'
        '<button type="submit" name="rating" value="up" class="module-feedback__react" aria-label="Me sirvió" title="Me sirvió">'
        '<span class="module-feedback__emoji">&#128077;</span><span class="module-feedback__label">Me sirvió</span></button>'
        '<button type="submit" name="rating" value="down" class="module-feedback__react" aria-label="Puede mejorar" title="Puede mejorar">'
        '<span class="module-feedback__emoji">&#128078;</span><span class="module-feedback__label">Puede mejorar</span></button>'
        '</div></form></div></section>'
    )


def feedback_assets(stem: str, pack: str) -> str:
    initial = feedback_html(stem, pack).replace("</", "<\\/")
    return """
<link rel="stylesheet" href="/static/feedback.css" />
<style>
/* feedback.css es la ÚNICA fuente de .module-feedback* (botonera de cierre):
   la usan fin de módulo, catálogo, how-to Y este cierre de Academy — mismas
   clases en los tres para que se vean IDÉNTICOS, nada de tipografía/botones
   propio acá (ver tools.css comment y catalog_guide_slides.html, mismo shim).
   Shim: feedback.css usa las variables del copiloto (--card/--soft);
   dw-academy.css no las define con ese nombre (usa --bg-card/--ink-soft). */
:root{ --card: var(--bg-card); --soft: var(--ink-soft); }
/* Reveal al llegar al final: propio de Academy (storytelling de una sola
   scrolleada); el cierre de módulo no lo necesita porque ya vive en un
   documento normal. Solo anima el wrapper — el resto es feedback.css. */
#academy-feedback { opacity: 0; transform: translateY(22px); transition: opacity .7s var(--ease-out, ease), transform .7s var(--ease-out, ease); }
#academy-feedback.is-visible { opacity: 1; transform: none; }
/* .module-feedback trae border-top:0 a propósito: en módulo esa línea la
   aporta el border-bottom de la ÚLTIMA sección de contenido. En Academy el
   último .stop/.topic tiene border-bottom:none (":last-of-type"), así que acá
   SÍ hace falta un borde propio para no perder el separador antes del cierre. */
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
    // Suma un stop final al rail ("¿Qué te pareció?") para que el lector VEA que
    // hay un tramo más después del último bullet y scrollee hasta el cierre.
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
  function replaceFeedback(html) {
    var node = document.getElementById('academy-feedback');
    if (node) { node.outerHTML = html; hookup(document.getElementById('academy-feedback')); }
  }
  document.addEventListener('submit', function(event) {
    var form = event.target.closest('[data-academy-feedback-form]');
    if (!form) return;
    event.preventDefault();
    var data = new FormData(form);
    if (event.submitter && event.submitter.name) {
      data.set(event.submitter.name, event.submitter.value || '');
    }
    fetch(form.action, { method: 'POST', body: data, credentials: 'same-origin' })
      .then(function(response) { return response.text(); })
      .then(replaceFeedback);
  });
  document.addEventListener('click', function(event) {
    var button = event.target.closest('[data-academy-feedback-cancel]');
    if (!button) return;
    event.preventDefault();
    var data = new FormData();
    data.set('pack', button.dataset.pack || '""" + pack + """');
    fetch(button.dataset.url, { method: 'POST', body: data, credentials: 'same-origin' })
      .then(function(response) { return response.text(); })
      .then(replaceFeedback);
  });
  document.addEventListener('DOMContentLoaded', function() {
    if (!document.getElementById('academy-feedback')) {
      document.body.insertAdjacentHTML('beforeend', '""" + initial + """');
    }
    hookup(document.getElementById('academy-feedback'));
  });
})();
</script>
"""
