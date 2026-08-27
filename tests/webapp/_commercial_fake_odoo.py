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
    def __init__(self, *, stages, tags, leads, lost_reasons=None, activities=None,
                 messages=None, tracking=None, configured=True):
        self.stages = stages
        self.tags = tags
        self.leads = leads
        self.lost_reasons = lost_reasons or []
        self.activities = activities or []
        # Historial de cambios de campo (`mail.message` + `mail.tracking.value`):
        # es lo que Odoo usa para saber CUÁNDO una oportunidad cambió de
        # etapa o dejó de ser lead. Sin esto no se pueden probar tiempo por
        # etapa, conversión etapa a etapa, estancadas ni la entrada al
        # pipeline que usa Creadas.
        self.messages = messages or []
        self.tracking = tracking or []
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
            # `is_won`/`sequence` viajan SIEMPRE (como en el modelo real de
            # Odoo, donde ambos existen con default aunque la fixture no los
            # setee) — antes se devolvía SÓLO id/name sin importar `fields`,
            # así que cualquier código que pidiera `is_won` (ver
            # `_build_lead_conversion_detail`, Analítica del Pipeline) nunca
            # podía verlo aunque la fixture lo trajera.
            return [{"id": s["id"], "name": s["name"], "is_won": bool(s.get("is_won")),
                     "sequence": s.get("sequence", 0)} for s in self.stages]
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
        if model == "mail.message":
            matched = []
            for msg in self.messages:
                ok = True
                for cond in domain:
                    field, op, value = cond
                    if field == "tracking_value_ids.field_id.name":
                        ok = msg.get("tracked_field") == value
                    elif field == "res_id" and op == "in":
                        ok = msg.get("res_id") in value
                    elif op == "=":
                        ok = msg.get(field) == value
                    elif op in (">=", "<=", ">", "<"):
                        actual = str(msg.get(field) or "")
                        limite = str(value)
                        ok = (actual >= limite if op == ">=" else
                              actual <= limite if op == "<=" else
                              actual > limite if op == ">" else actual < limite)
                    else:
                        raise AssertionError(f"unsupported {op!r} on mail.message {field!r}")
                    if not ok:
                        break
                if ok:
                    matched.append(msg)
            if order == "date asc":
                matched = sorted(matched, key=lambda m: m.get("date") or "")
            return [{f: m.get(f, False) for f in (fields or [])} | {"id": m["id"]} for m in matched]
        if model == "mail.tracking.value":
            matched = []
            for tv in self.tracking:
                ok = True
                for cond in domain:
                    field, op, value = cond
                    if field == "mail_message_id" and op == "in":
                        ok = tv.get("mail_message_id") in value
                    elif field == "field_id.name":
                        ok = tv.get("field") == value
                    elif op == "ilike":
                        ok = str(value).lower() in str(tv.get(field) or "").lower()
                    elif op == "=":
                        ok = tv.get(field) == value
                    else:
                        raise AssertionError(f"unsupported {op!r} on mail.tracking.value {field!r}")
                    if not ok:
                        break
                if ok:
                    matched.append(tv)
            return [{f: t.get(f, False) for f in (fields or [])} for t in matched]
        if model == "mail.activity":
            # `mail.activity` no tiene el flag `active` de crm.lead: en Odoo el
            # modelo SOLO contiene actividades pendientes (las hechas se
            # borran), así que no hay default implícito que emular.
            matched = [a for a in self.activities if self._matches_activity(a, domain)]
            if order == "date_deadline asc":
            # NULLs al final, MISMO criterio que PostgreSQL con ASC.
                matched = sorted(matched, key=lambda a: (a.get("date_deadline") in (None, False), a.get("date_deadline") or ""))
            return [self._project_activity(a, fields) for a in matched]
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
            elif op == "in":
                # `["active", "in", [True, False]]` es la forma real de pedirle
                # a Odoo "traeme archivados TAMBIÉN" (la usa el join de
                # Actividades). `active` ausente = True, igual que en Odoo.
                actual = lead.get("active", True) if field == "active" else lead.get(field)
                ok = actual in value
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

    def _matches_activity(self, act: dict, domain: list) -> bool:
        for field, op, value in domain:
            if field == "res_id" and op == "in":
                ok = act.get("res_id") in value
            elif op == "=":
                ok = act.get(field) == value
            elif op == "!=":
                ok = act.get(field) != value
            else:
                raise AssertionError(f"unsupported operator {op!r} on mail.activity field {field!r}")
            if not ok:
                return False
        return True

    def _project_activity(self, act: dict, fields: list | None) -> dict:
        base = {"id": act.get("id", 0)}
        for f in (fields or []):
            if f in ("user_id", "activity_type_id"):
                pair = act.get(f)
                base[f] = list(pair) if pair else False
            else:
                base[f] = act.get(f, False)
        return base

    def _project(self, lead: dict, fields: list | None) -> dict:
        base = {"id": lead["id"]}
        for f in (fields or []):
            if f == "stage_id":
                stage = self._stage_lookup(lead.get("stage_id"))
                base["stage_id"] = [lead["stage_id"], stage["name"]] if stage else False
            else:
                base[f] = lead.get(f, False)
        return base
