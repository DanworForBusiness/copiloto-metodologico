from types import SimpleNamespace

from webapp.services.content_service import load_module
from webapp.services.section_analysis import (
    SectionAnalysisService,
    normalize_llm_markdown,
    render_llm_html,
)


class FakeCompletions:
    def __init__(self):
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        message = SimpleNamespace(
            content=(
                "**Decisión:**\u00a0-\u00a0Mantener clean core "
                "– Validar gobierno • Documentar evidencia. <script>alert(1)</script>"
            )
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


def test_section_analysis_reuses_core_prompt_and_renders_safe_markdown(monkeypatch):
    fake = FakeClient()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    service = SectionAnalysisService(client_factory=lambda **_: fake, execution_mode="API Real")
    document = load_module("01_Arquitectura_y_Conectividad")
    section = document.sections[0]

    result = service.analyze(document, section.slug, "Checklist ejecutable", "datasphere_mvp")

    assert section.title in result.prompt
    assert "INSTRUCCIÓN ESTRICTA" in result.prompt
    assert fake.chat.completions.request["model"]
    assert "<strong>Decisión:</strong>" in result.html
    assert result.html.count("<li>") == 3
    assert "<ul>" in result.html
    assert "<script>" not in result.html
    assert "&lt;script&gt;" in result.html


def test_normalizes_unicode_and_non_breaking_inline_bullets():
    answer = (
        "**¿Qué resuelve esta sección?** Texto introductorio. "
        "**Lo que el cliente necesita saber:**\u00a0-\u00a0Primer punto "
        "– Segundo punto • Tercer punto"
    )

    normalized = normalize_llm_markdown(answer)

    assert "\n\n**Lo que el cliente necesita saber:**" in normalized
    assert "\n- Primer punto" in normalized
    assert "\n- Segundo punto" in normalized
    assert "\n- Tercer punto" in normalized


def test_does_not_turn_normal_hyphenated_prose_into_a_list():
    answer = "Compará federación - persistencia antes de decidir."

    assert normalize_llm_markdown(answer) == answer


def test_separates_standalone_group_heading_from_list_and_following_text():
    answer = (
        "- Primer punto\n"
        "- Segundo punto\n"
        "**Decisiones que se derivan:**\n"
        "Validar el modelo de integracion."
    )

    normalized = normalize_llm_markdown(answer)
    rendered = render_llm_html(answer)

    assert "- Segundo punto\n\n**Decisiones que se derivan:**" in normalized
    assert "**Decisiones que se derivan:**\n\nValidar" in normalized
    assert "</ul>\n<p><strong>Decisiones que se derivan:</strong></p>" in rendered
    assert "</strong></p>\n<p>Validar el modelo de integracion.</p>" in rendered


def test_separates_standalone_group_heading_from_paragraphs():
    answer = (
        "Contexto introductorio.\n"
        "**Lo que el cliente necesita saber:**\n"
        "Detalle metodologico."
    )

    assert normalize_llm_markdown(answer) == (
        "Contexto introductorio.\n\n"
        "**Lo que el cliente necesita saber:**\n\n"
        "Detalle metodologico."
    )


def test_keeps_regular_inline_bold_text_unchanged():
    answer = "Este concepto es **importante** dentro del texto."

    assert normalize_llm_markdown(answer) == answer


def test_separates_markdown_table_from_preceding_list_and_following_text():
    answer = (
        "Definiciones:\n\n"
        "- D = capacidad total\n"
        "- M = memoria total\n"
        "- N = cantidad de espacios\n"
        "| Tipo de espacio | Disk sugerido | Memory sugerida | Criterio |\n"
        "|---|---|---|---|\n"
        "| **DEV / Desarrollo** | D x 10% | M x 10% | Para desarrollo |\n"
        "| **QA / Calidad** | D x 20% | M x 20% | Para QA |\n"
        "Distribución orientativa."
    )

    normalized = normalize_llm_markdown(answer)
    rendered = render_llm_html(answer)

    assert "- N = cantidad de espacios\n\n| Tipo de espacio" in normalized
    assert "| **QA / Calidad** | D x 20% | M x 20% | Para QA |\n\nDistribución" in normalized
    assert "</ul>\n<table>" in rendered
    assert "<td><strong>DEV / Desarrollo</strong></td>" in rendered
    assert "</table>\n<p>Distribución orientativa.</p>" in rendered
    assert "| <strong>DEV / Desarrollo</strong> |" not in rendered
