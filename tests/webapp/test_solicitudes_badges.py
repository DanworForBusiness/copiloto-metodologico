import pytest
from bs4 import BeautifulSoup
from webapp.templating import templates


@pytest.mark.parametrize('kind,value,tone', [
    ('priority','Alta','red'), ('priority','Media','amber'), ('priority','Baja','grey'),
    ('funnel','Solicitado','amber'), ('funnel','Aprobado para Arquitectura','green'),
    ('funnel','Rechazado, Aplica Operaciones','red'),
    ('architecture','Pendiente','grey'), ('architecture','Planificado','violet'),
    ('architecture','En curso','blue'), ('architecture','Finalizado','green'),
    ('architecture','Suspendido','amber'),
])
def test_badges_preserve_catalog_values(kind, value, tone):
    macro = templates.get_template('partials/solicitudes_badge.html').module.request_badge
    badge = BeautifulSoup(str(macro(kind, value)), 'html.parser').span
    assert badge.get_text() == value
    assert badge['title'] == value
    assert badge['data-badge-mapped'] == 'true'
    assert 'solicitudes-status-badge--' + tone in badge['class']


@pytest.mark.parametrize('value', [None, '', 'Nuevo estado', '<script>alert(1)</script>'])
def test_badge_unknown_empty_and_unsafe_values_are_neutral(value):
    macro = templates.get_template('partials/solicitudes_badge.html').module.request_badge
    soup = BeautifulSoup(str(macro('architecture', value)), 'html.parser')
    assert not soup.script
    assert soup.span.get_text() == (value or '—')
    assert soup.span['data-badge-mapped'] == 'false'
    assert 'solicitudes-status-badge--grey' in soup.span['class']
