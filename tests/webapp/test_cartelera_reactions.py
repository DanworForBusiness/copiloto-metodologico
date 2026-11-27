"""Reacciones de Comunidad: seis tipos (Me gusta, Celebrar, Apoyo, Gracias, Idea, Divertido) en el selector flotante, el botón base refleja la
reacción propia (ícono + nombre) y cada tipo tiene su color sin depender solo de él (ícono, nombre y anillo en la propia)."""
from __future__ import annotations

import re

from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, env, remote  # noqa: F401
from tests.webapp.test_cartelera_access import ORIGIN, editions  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import operations_users, session_client  # noqa: F401
from webapp.services import cartelera_social as social

SIX = ["like", "celebrate", "support", "thanks", "idea", "funny"]


def test_six_reaction_types_and_the_main_button_reflects_the_own_one(editions, monkeypatch):  # noqa: F811
    from webapp.routers import cartelera as router
    monkeypatch.setattr(router, "PORTAL_LAUNCHED", True)
    assert [r[0] for r in social.REACTIONS] == SIX
    post = social.save_post({"sub": OPS_A, "email": OPS_A, "name": "Ops A"}, {"category": "reconocimientos", "title": "Con reacciones"})
    target = f"post:{post['id']}"
    c = session_client(CCHH_EMAIL)
    html = c.get("/cartelera/comunidad").text
    block = html[html.index(f'data-social="{target}"'):]
    picker = block[block.index('class="ct-rx-picker"'):block.index("</div>", block.index('class="ct-rx-picker"'))]
    assert re.findall(r'class="ct-rx-opt[^"]*"[^>]*data-r="(\w+)"', picker) == SIX
    assert 'aria-label="Idea"' in picker and 'aria-label="Divertido"' in picker and picker.count('class="ct-rx-tip"') == 6
    assert c.post("/cartelera/social/reaccion", headers=ORIGIN, json={"target": target, "r": "idea"}).json()["mine"] == "idea"
    block = c.get("/cartelera/comunidad").text.split(f'data-social="{target}"', 1)[1]
    main = block[:block.index("</button>")]
    assert 'data-r="idea"' in main and 'aria-pressed="true"' in main and "<span>Idea</span>" in main     # ícono + nombre, no siempre «Me gusta»
    assert 'class="ct-rx-opt is-mine" role="menuitem" data-sx="react" data-r="idea"' in block
    assert c.post("/cartelera/social/reaccion", headers=ORIGIN, json={"target": target, "r": "risa"}).status_code >= 400   # tipos cerrados


def test_each_reaction_has_its_own_colour_token_and_reduced_motion():
    css = open("webapp/static/cartelera_social.css", encoding="utf-8").read()
    for r in SIX:
        assert f'[data-r="{r}"], .ct-rx-ic--{r} {{ --rx-c:' in css
    assert "scale(1.18)" in css and "prefers-reduced-motion" in css
    js = open("webapp/static/cartelera_social.js", encoding="utf-8").read()
    assert 'idea: "Idea", funny: "Divertido"' in js                                                     # detalle de quiénes reaccionaron
