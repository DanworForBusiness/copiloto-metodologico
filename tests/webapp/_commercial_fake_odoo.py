"""
_commercial_fake_odoo.py — Fake/stub transporte Odoo para tests (Commercial
Hub Odoo Batch 1). CI NUNCA depende de Odoo real. Implementa la MISMA
interfaz que `webapp.services.odoo_client.OdooClient`
(`is_configured`/`search_read`/`search_count`) con un evaluador GENÉRICO de
dominio (AND-only, mismos operadores que realmente usa
`commercial_service.py`: `=`/`!=`/`>=`/`<=`/`in`, + el campo puntuado
`stage_id.is_won` y el default implícito de Odoo `active=True` cuando el
dominio no lo menciona) — así los tests verifican que el SERVICE construye
los dominios correctos, no solo que "algo" se devuelve.

Import solo desde tests — no es parte del código de producción.
"""
from __future__ import annotations

from webapp.services.odoo_client import OdooTransportError


class FakeOdooClient:
    def __init__(self, *, stages, tags, leads, lost_reasons=None, configured=True):
        self.stages = stages
        self.tags = tags
        self.leads = leads
        self.lost_reasons = lost_reasons or []
        self._configured = configured
        self.calls: list[tuple[str, list, list | None]] = []
        self._raise_for_model: str | None = None
        self._raise_stage = "execute"

    def is_configured(self) -> bool:
        return self._configured

    def raise_on(self, model: str, *, stage: str = "execute") -> None:
        """Fuerza que la PRÓXIMA consulta a `model` levante OdooTransportError."""
        self._raise_for_model = model
        self._raise_stage = stage

    def _stage_lookup(self, stage_id):
        return next((s for s in self.stages if s["id"] == stage_id), None)

    def search_read(self, model, domain=None, fields=None, limit=None, order=None):
        domain = domain or []
        self.calls.append((model, domain, fields))
        if self._raise_for_model == model:
            self._raise_for_model = None
            raise OdooTransportError(f"forced failure on {model}", stage=self._raise_stage)

        if model == "crm.stage":
            return [{"id": s["id"], "name": s["name"]} for s in self.stages]
        if model == "crm.tag":
            return [{"id": t["id"], "name": t["name"]} for t in self.tags]
        if model == "crm.lost.reason":
            wanted = None
            for cond in domain:
                if cond[0] == "id" and cond[1] == "in":
                    wanted = set(cond[2])
            return [{"id": r["id"], "name": r["name"]} for r in self.lost_reasons if wanted is None or r["id"] in wanted]
        if model == "crm.lead":
            matched = [lead for lead in self.leads if self._matches(lead, domain)]
            return [self._project(lead, fields) for lead in matched]
        raise AssertionError(f"unexpected model {model}")

    def search_count(self, model, domain=None):
        return len(self.search_read(model, domain=domain))

    def _matches(self, lead: dict, domain: list) -> bool:
        has_active_cond = any(c[0] == "active" for c in domain)
        if not has_active_cond and not lead.get("active", True):
            return False  # Odoo default: oculta inactivos salvo que se pida explícito
        for field, op, value in domain:
            if field == "stage_id.is_won":
                stage = self._stage_lookup(lead.get("stage_id"))
                actual = bool(stage and stage.get("is_won"))
                ok = (actual == value) if op == "=" else (actual != value)
            elif field == "stage_id" and op == "in":
                ok = lead.get("stage_id") in value
            elif field == "id" and op == "in":
                ok = lead.get("id") in value
            elif op == "=":
                ok = lead.get(field) == value
            elif op == "!=":
                ok = lead.get(field) != value
            elif op == ">=":
                ok = (lead.get(field) or "") >= value
            elif op == "<=":
                ok = (lead.get(field) or "") <= value
            else:
                raise AssertionError(f"unsupported operator {op!r} on field {field!r}")
            if not ok:
                return False
        return True

    def _project(self, lead: dict, fields: list | None) -> dict:
        base = {"id": lead["id"]}
        for f in (fields or []):
            if f == "stage_id":
                stage = self._stage_lookup(lead.get("stage_id"))
                base["stage_id"] = [lead["stage_id"], stage["name"]] if stage else False
            else:
                base[f] = lead.get(f, False)
        return base
