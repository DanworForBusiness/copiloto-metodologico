"""El aviso «continúa en segundo plano» queda separado del preload con el MISMO criterio en el Diagnóstico ad hoc y en Publicar modelo.

Regla única y acotada por atributo en `tools.css` (sin CSS global sobre `.admin-help`); ambas plantillas mantienen el aviso como
`p.admin-help[data-*-bg]` inmediatamente después del preload `.section-analysis-loading`."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "webapp/static/tools.css").read_text(encoding="utf-8")
TEMPLATES = {"data-dsa-bg": "datasphere_assessment_tool.html", "data-dsp-bg": "datasphere_publication_tool.html"}


def _rule_for(selector: str) -> str:
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS):
        if selector in [s.strip() for s in sel.split(",")]:
            return body
    return ""


def test_both_background_notices_share_the_assessment_spacing():
    for attr in TEMPLATES:
        assert "margin-top: 12px" in _rule_for(f".admin-help[{attr}]"), attr


def test_the_spacing_is_scoped_and_not_global():
    assert "margin-top" not in _rule_for(".admin-help")


def test_the_notice_follows_the_preload_in_both_tools():
    for attr, name in TEMPLATES.items():
        html = (ROOT / "webapp/templates/partials" / name).read_text(encoding="utf-8")
        loading, notice = html.find('class="section-analysis-loading"'), html.find(f'class="admin-help" role="status" {attr}')
        assert 0 <= loading < notice, name
        assert "Ver ejecución" in html[notice:notice + 400], name
