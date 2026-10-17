"""Regresión de navegación para referencias Markdown del Hub."""

from webapp.services.content_service import _pipe_links_html


def test_dual_external_reference_links_open_in_new_tabs():
    source = (
        "- Configurar Datasphere con Cloud Connector"
        " | [RISE](https://example.com/rise)"
        " | [On-Prem](https://example.com/onprem)"
    )

    result = _pipe_links_html(source)

    assert result.count('target="_blank"') == 2
    assert result.count('rel="noopener noreferrer"') == 2
    assert 'href="https://example.com/rise"' in result
    assert 'href="https://example.com/onprem"' in result
    assert "Configurar Datasphere con Cloud Connector" in result
    assert ">RISE</a>" in result
    assert ">On-Prem</a>" in result