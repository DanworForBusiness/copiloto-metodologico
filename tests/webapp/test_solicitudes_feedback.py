import shutil
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup
from starlette.requests import Request

from webapp.templating import templates


def test_saved_feedback_is_one_paragraph_with_inline_reload():
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    response = templates.TemplateResponse(request=request, name="partials/solicitudes_feedback.html", context={
        "state": "saved", "message": "Guardado confirmado en SharePoint.", "item_id": "7",
    })
    soup = BeautifulSoup(response.body, "html.parser")
    notice = soup.select_one('[data-solicitudes-saved]')
    assert notice['role'] == 'status'
    assert 'field-hint' in notice.select_one('.callout__body')['class']
    assert len(notice.select('p')) == 1
    assert notice.select_one('p a')['href'] == '/operaciones/solicitudes/7/editar'
    assert '✓' in notice.get_text()
    assert len(notice.select('.callout__body > *')) == 1


def test_success_only_scrolls_when_outside_visible_area():
    node = shutil.which('node')
    assert node, 'Node is required for the interaction regression'
    subprocess.run([node, str(Path(__file__).with_name('solicitudes_feedback.node.js'))], check=True)


def test_uncertain_notice_reuses_themed_compact_warning():
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    response = templates.TemplateResponse(request=request, name="partials/solicitudes_feedback.html", context={
        "state": "uncertain", "message": "Revisá el resultado.", "item_id": "7",
    })
    soup = BeautifulSoup(response.body, "html.parser")
    assert soup.select_one('.callout--punch .callout__body.field-hint')
    assert not soup.select_one('a.btn-secondary')
    assert soup.select_one('a')['target'] == '_blank'
