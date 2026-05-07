"""
llm.py — Gateway para el LLM.

Encapsula completamente la lógica de simulación vs. API Real.
El resto de la app nunca pregunta por el modo: simplemente llama a ask().

IMPORTANTE — threading:
  st.session_state NO está disponible en threads secundarios (ScriptRunContext).
  Por eso _llm_call_direct y _llm_call_with_cases capturan AppState.is_simulation()
  en el hilo principal y llaman a _simulate() o _call_api() directamente,
  sin pasar por ask() que volvería a consultar session_state.

Uso:
    from core.llm import LLMGateway
    gw = LLMGateway.get()
    answer = gw.ask(system_prompt, context, user_prompt)         # hilo principal
    answer = gw._call_api(system_prompt, context, user_prompt)  # desde thread
"""

from __future__ import annotations

import os
import streamlit as st
from openai import OpenAI

from config import MODEL
from core.state import AppState


class LLMGateway:
    """
    Singleton por session para el cliente OpenAI.
    El cliente se construye una sola vez y se reutiliza.

    La decisión simulación/real se toma en ask() consultando AppState,
    pero SOLO cuando se llama desde el hilo principal (donde session_state
    está disponible). Para threads secundarios usar _call_api() directamente.
    """

    _SESSION_KEY = "_llm_gateway_client"

    @classmethod
    def _get_client(cls) -> OpenAI | None:
        """Devuelve el cliente OpenAI cacheado, construyéndolo si no existe."""
        if cls._SESSION_KEY not in st.session_state:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                st.error("Falta OPENAI_API_KEY. Configurala en tu entorno y reinicia la app.")
                st.stop()
            st.session_state[cls._SESSION_KEY] = OpenAI(api_key=api_key)
        return st.session_state[cls._SESSION_KEY]

    @classmethod
    def get(cls) -> "LLMGateway":
        return cls()

    # ── Llamada desde hilo principal (evalúa modo dinámicamente) ─────────────
    def ask(
        self,
        system_prompt: str,
        context: str,
        user_prompt: str,
        *,
        model: str = MODEL,
        max_tokens: int = 800,
        temperature: float = 0.1,
    ) -> str:
        """
        Para llamadas desde el hilo principal — consulta session_state para
        determinar el modo. No usar desde threads secundarios.
        """
        if AppState.is_simulation():
            return self._simulate(system_prompt, context, user_prompt)
        return self._call_api(system_prompt, context, user_prompt,
                              model=model, max_tokens=max_tokens, temperature=temperature)

    # ── Llamada directa a la API — segura desde threads secundarios ───────────
    def _call_api(
        self,
        system_prompt: str,
        context: str,
        user_prompt: str,
        *,
        model: str = MODEL,
        max_tokens: int = 800,
        temperature: float = 0.1,
    ) -> str:
        """
        Llama a la API de OpenAI directamente, sin consultar session_state.
        Usar desde threads secundarios donde session_state no está disponible.
        """
        client = self._get_client()
        if client is None:
            return "⚠ Cliente no disponible. Revisá la API key."
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"CONTEXTO (Marco Danwor)\n"
                            f"{'─'*40}\n{context}\n\n"
                            f"SOLICITUD\n{'─'*40}\n{user_prompt}"
                        ),
                    },
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as exc:
            return f"⚠ Error al llamar al modelo: {exc}"

    # ── Simulación ────────────────────────────────────────────────────────────
    @staticmethod
    def _simulate(system_prompt: str, context: str, user_prompt: str) -> str:
        return (
            "🧪 **Simulación activa** — no se consumieron créditos.\n\n"
            f"**SYSTEM PROMPT**\n```\n{system_prompt}\n```\n\n"
            f"**CONTEXTO** *(primeros 800 chars)*\n```\n{context[:800]}\n```\n\n"
            f"**USER PROMPT**\n```\n{user_prompt}\n```"
        )

    # ── Compat: acceso al cliente para código legacy ──────────────────────────
    @property
    def _client(self) -> OpenAI | None:
        if AppState.is_simulation():
            return None
        return self._get_client()


# ── Compat shim ───────────────────────────────────────────────────────────────
def get_client() -> OpenAI | None:
    """Deprecado. Usar LLMGateway.get().ask() directamente."""
    return LLMGateway.get()._client


def ask_llm(client, model: str, system_prompt: str, context: str, user_prompt: str) -> str:
    """Deprecado. Usar LLMGateway.get().ask() directamente."""
    return LLMGateway.get().ask(system_prompt, context, user_prompt, model=model)