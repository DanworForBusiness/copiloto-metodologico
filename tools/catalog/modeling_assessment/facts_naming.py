"""Hechos de NAMING (M4) — contrato de nombre de ARTEFACTO + contrato de nombre de CAMPO, juntos. Solo HECHOS.

M4 hace observable la evidencia de Naming en los DOS niveles sin emitir juicio: no hay score global de Naming, banda,
peso, umbral, penalidad ni ranking de calidad, y ninguna etiqueta cualitativa (sistematico / fragmentado / gobernado):
se conservan los histogramas y conteos de los que una senal posterior (M5) podra hablar. La calibracion humana
(CASE 3 / CASE 4) mostro que el nivel artefacto SOLO no alcanza: CASE 3 y CASE 4 tienen un incumplimiento formal de
§28 casi igual y se separan en el nivel campo (continuidad de vocabulario de origen, etiquetas, formas de nombre).

La poblacion es la CLAUSURA SEMANTICA del AM terminal. SYSTEM (`SAP`/`SAP.*`) queda fuera del gobierno de nombres
(NOT_APPLICABLE `SYSTEM_ARTIFACT`), igual que en Propagation.

Hechos por artefacto (subject = artefacto):
  naming.artifact_name_contract    A  nombre tecnico, `parse_name()` TAL CUAL (via bridge), cumplimiento ACTUAL de §28 y
                                      motivos exactos (+ familias estables), familia de prefijo y forma estructural del
                                      nombre, procedencia M1 (con regla y certeza), relacion nombre/stem de la fuente
                                      remota (tokens agregados/quitados por el proyecto), rol DECLARADO y capa inferida
                                      (+ base) solo como CONTEXTO
  naming.field_name_contract       A  por campo: etiqueta, presencia, `label == campo`, etiqueta equivalente al campo
                                      normalizada; cobertura de etiquetas
  naming.field_name_forms          B  por campo: FORMA mecanica del nombre (ruleset versionado; heuristica descriptiva)
  naming.field_name_continuity     B  por campo: continuidad del nombre por LINAJE OBSERVABLE: ANCLA OBSERVABLE MAS TEMPRANA
                                      (`earliest_observable_anchors`, local o remota) y, por separado, ANCLA DE FUENTE REMOTA
                                      (`remote_source_anchor`, solo si hay un `remote_entity` observado), con su cadena de
                                      evidencia, y si ya estaba en un proveedor directo (continuidad capa a capa)
Hechos de clausura (subject = "closure"):
  naming.artifact_name_populations B  poblacion por procedencia: cumplimiento de §28 actual, prevalencia, histograma de
                                      motivos y de familias de incumplimiento, familias de prefijo
  naming.field_vocabulary_summary  B  agregados de campo (ocurrencias, no vocabulario distinto) + cruce ancla x forma,
                                      por procedencia y para el AM terminal
  naming.same_normalized_label_multiple_technical_names  B  etiqueta normalizada compartida por >= 2 nombres tecnicos
  naming.convention_context        D  la convencion historica/LOCAL EXPLICA un desvio, no reescribe el cumplimiento de
                                      §28 vigente; no es observable en el export (NOT_ASSESSED, CONTEXT_REQUIRED)

Lo que NO hace (a proposito):
  * §28 ACTUAL es objetivo: el contexto de convencion local NO cambia `section_28.compliant`;
  * no cobra la procedencia: SOURCE_DERIVED no es "decision del proyecto", UNKNOWN es indeterminado y NO es malo; una
    identidad UNKNOWN NO invalida los hechos independientes de la identidad (nombre, `parse_name`, §28, motivos, nombre
    de campo, etiquetas, forma): siguen ASSESSED; solo la atribucion que depende de la identidad queda limitada;
  * no infiere calidad por forma (parecer DDIC / parecer de negocio / empezar con Z / tener etiqueta);
  * no asigna "rol incorrecto" por nombre: solo copia la letra de uso parseada junto al rol DECLARADO;
  * no doble-cobra: la calidad de la fuente, el acceso a tabla cruda, el pass-through y la propagacion NO son
    defectos de Naming;
  * IGUAL ETIQUETA != MISMO CONCEPTO DE NEGOCIO: el hecho de etiqueta compartida no afirma sinonimia, terminologia
    inconsistente, conceptos duplicados ni defectos (las series numeradas lo demuestran: `HSL01..HSL16`). Esas son
    interpretaciones candidatas de M5; aqui se conserva la evidencia cruda y `is_numbered_series`;
  * ANCLA OBSERVABLE != FUENTE ORIGINAL: el linaje de Modeling solo muestra el artefacto observable mas temprano donde
    existe el nombre (puede ser local, materializado o remoto). Solo un `remote_entity` observado permite hablar de
    ancla de fuente remota; si la cadena termina en un artefacto local sin `remote_entity`, el ancla de fuente remota
    queda "no disponible" y NO se infiere nada mas alla del snapshot.

"Aparece por primera vez en este artefacto" = el nombre no esta en ningun artefacto de su linaje observable: puede ser
un campo nuevo o un renombre por alias (el export estandar no distingue ambos sin la expresion SQL): clase B, nunca A.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from . import contract as C
from . import legacy_bridge as bridge
from . import naming_rules as R
from .facts_support import entity_refs, make_fact, not_applicable, sort_facts
from .graph import EdgeKind
from .identity import IdentityClassification, IdentityProvenance, ImplementationProvenance, classify, name_tokens
from .model import (
    ApplicabilityReason, Area, Assessability, Cause, EvidenceClass, EvidenceRef, Fact, Plane,
)
from .scope import AssessmentUniverse

L = EdgeKind.LINEAGE
N_ = Area.NAMING


class FieldNameAnchor(str, Enum):
    """Donde se ANCLA un nombre de campo dentro del linaje OBSERVABLE. Nunca afirma una fuente original externa."""
    IS_REMOTE_ARTIFACT_FIELD = "IS_REMOTE_ARTIFACT_FIELD"            # el artefacto tiene `remote_entity`: es en si mismo un ancla remota observada
    IS_LOCAL_BOUNDARY_FIELD = "IS_LOCAL_BOUNDARY_FIELD"              # hoja sin `remote_entity` (tabla local/estructura): no hay linaje mas temprano observable
    NO_LINEAGE_PROVIDER = "NO_LINEAGE_PROVIDER"                      # sin proveedores de LINAJE (solo asociaciones): no hay donde buscar continuidad
    ANCHORED_AT_REMOTE_ARTIFACT = "ANCHORED_AT_REMOTE_ARTIFACT"      # alguna ancla mas temprana es un artefacto con `remote_entity`
    ANCHORED_AT_LOCAL_BOUNDARY = "ANCHORED_AT_LOCAL_BOUNDARY"        # las anclas mas tempranas son hojas LOCALES sin `remote_entity` (y ninguna remota)
    FIRST_APPEARS_IN_INTERMEDIATE = "FIRST_APPEARS_IN_INTERMEDIATE"  # el nombre aparece por primera vez en una capa intermedia y se arrastra hacia arriba
    FIRST_APPEARS_AT_THIS_ARTIFACT = "FIRST_APPEARS_AT_THIS_ARTIFACT"  # no esta en ningun artefacto del linaje observable


class AnchorKind(str, Enum):
    REMOTE_ENTITY_ARTIFACT = "REMOTE_ENTITY_ARTIFACT"
    LOCAL_BOUNDARY_ARTIFACT = "LOCAL_BOUNDARY_ARTIFACT"
    INTERMEDIATE_ARTIFACT = "INTERMEDIATE_ARTIFACT"
    THIS_ARTIFACT = "THIS_ARTIFACT"


REMOTE_ANCHOR_OBSERVED = "OBSERVED"
REMOTE_ANCHOR_UNAVAILABLE = "UNAVAILABLE_IN_OBSERVABLE_LINEAGE"      # ninguna ancla tiene `remote_entity`: no se infiere mas alla del snapshot


_CONTEXT_QUESTIONS = (
    "local_convention_for_section_28_deviations",      # p. ej. omitir el digito de capa en `V*_` ¿es convencion acordada del proyecto?
    "business_vocabulary_expected_by_project",         # p. ej. `ZED_*` ¿es el vocabulario de negocio que el proyecto espera?
    "label_name_mismatch_intent",                      # p. ej. nombre `ZED_VETNOSIS` con etiqueta `EBIT` ¿es intencional?
    "coexisting_name_vocabularies_decision",           # p. ej. `BUKRS` y `COMPANYCODE` conviviendo ¿es deuda o decision?
)


def _raw_fields(entity: Any) -> list[dict]:
    return list(getattr(entity, "fields", None) or [])


def _ratio(n: int, d: int) -> float | None:
    return None if d == 0 else round(n / d, 6)


def _anchor_counts(anchors) -> list[dict]:
    """Conteo por clase de ancla con TODAS las clases presentes (0 incluido): forma estable, sin dict de dominio."""
    got: dict[str, int] = {}
    for a in anchors:
        got[a] = got.get(a, 0) + 1
    return [{"anchor": a.value, "count": got.get(a.value, 0)} for a in FieldNameAnchor]


def _remote_anchor_counts(statuses) -> dict:
    s = list(statuses)
    return {"observed": sum(1 for x in s if x == REMOTE_ANCHOR_OBSERVED),
            "unavailable": sum(1 for x in s if x == REMOTE_ANCHOR_UNAVAILABLE)}


def _label_counts(rows: list[dict]) -> dict:
    present = sum(1 for r in rows if r["label_present"])
    return {"label_present": present, "label_absent": len(rows) - present,
            "label_equals_field": sum(1 for r in rows if r["label_equals_field"]),
            "label_matches_field_normalized": sum(1 for r in rows if r["label_matches_field_normalized"]),
            "label_differs_from_field": sum(1 for r in rows if r["label_present"] and not r["label_equals_field"])}


def naming_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    identity: Mapping[str, IdentityClassification] | None = None,
) -> tuple[Fact, ...]:
    graph = universe.graph
    closure = universe.closure_of(am)
    closure_set = set(closure)
    ident = dict(identity or {})
    for n in closure:
        ident.setdefault(n, classify(entities[n]))
    SYSTEM = IdentityProvenance.SYSTEM
    facts: list[Fact] = []

    names_of = {n: {f["campo"] for f in _raw_fields(entities[n])} for n in closure}
    has_remote = {n: bool(getattr(entities[n], "remote_entity", "")) for n in closure}
    artifact_rows: list[dict] = []      # una fila por artefacto aplicable (agregados de clausura)
    field_rows: list[dict] = []         # una fila por (artefacto, campo) aplicable

    for n in closure:
        e = entities[n]
        idc = ident[n]
        system = idc.identity is SYSTEM
        name_refs = entity_refs(e, ["name", "remote_entity"] if has_remote[n] else ["name"])
        field_refs = entity_refs(e, ["fields"])

        # ---- contrato de nombre del ARTEFACTO (A) -----------------------------------------------------------
        if system:
            facts.append(make_fact("naming.artifact_name_contract", N_, n, None, EvidenceClass.A, refs=name_refs,
                                   applicability=not_applicable(ApplicabilityReason.SYSTEM_ARTIFACT),
                                   assessability=Assessability.NOT_APPLICABLE))
        else:
            parsed = bridge.parse_name(n)                      # TAL CUAL: la verdad de §28 vigente es la del legacy
            issues = [str(i) for i in (parsed.get("issues") or [])]
            families = [R.violation_family(i).value for i in issues]
            relation = None
            if idc.implementation is ImplementationProvenance.REMOTE_IMPORTED:
                stem_tokens, name_toks = name_tokens(idc.remote_stem or ""), name_tokens(n)
                relation = {"rule": idc.rule.value, "remote_entity": getattr(e, "remote_entity", "") or None,
                            "remote_stem": idc.remote_stem, "name_equals_stem": name_toks == stem_tokens,
                            "project_added_tokens": R.token_difference(stem_tokens, name_toks),
                            "project_removed_tokens": R.token_difference(name_toks, stem_tokens)}
            shape = R.name_shape(parsed, issues)
            family = R.naming_family(n)
            facts.append(make_fact(
                "naming.artifact_name_contract", N_, n,
                {"section_28": {"applicable": True, "compliant": bool(parsed.get("canonical")), "violation_reasons": issues,
                                "violation_families": families,
                                "parsed": {k: parsed.get(k) for k in ("canonical", "tipo", "capa", "capa_nombre", "artefacto", "uso")}},
                 "name_shape": shape, "naming_family": family,
                 "identity": {"identity": idc.identity.value, "implementation": idc.implementation.value,
                              "rule": idc.rule.value, "certainty": idc.certainty.value if idc.certainty else None,
                              "remote_stem": idc.remote_stem},
                 "source_name_relation": relation,
                 "declared_role": C.declared_role(e), **C.layer_inference(e)},
                EvidenceClass.A, refs=name_refs))
            artifact_rows.append({"artifact": n, "identity": idc.identity.value,
                                  "heuristic": idc.certainty is not None and idc.certainty.value == "HEURISTIC",
                                  "compliant": bool(parsed.get("canonical")), "issues": issues, "families": families,
                                  "shape": shape, "family": family})

        # ---- nombres de CAMPO: contrato (A), formas (B), continuidad (B) --------------------------------------
        if system:
            for fid, ec in (("naming.field_name_contract", EvidenceClass.A), ("naming.field_name_forms", EvidenceClass.B),
                            ("naming.field_name_continuity", EvidenceClass.B)):
                facts.append(make_fact(fid, N_, n, None, ec, refs=field_refs,
                                       applicability=not_applicable(ApplicabilityReason.SYSTEM_ARTIFACT),
                                       assessability=Assessability.NOT_APPLICABLE))
            continue
        fields = _raw_fields(e)
        if not fields:
            for fid, ec in (("naming.field_name_contract", EvidenceClass.A), ("naming.field_name_forms", EvidenceClass.B),
                            ("naming.field_name_continuity", EvidenceClass.B)):
                facts.append(make_fact(fid, N_, n, None, ec, refs=field_refs, assessability=Assessability.NOT_ASSESSED,
                                       cause=Cause.NOT_OBSERVABLE_IN_STANDARD_EXPORT))
            continue

        n_elements = int(getattr(e, "n_elements", 0) or 0)
        label_recs = [{"field": f["campo"], **R.label_relations(f["campo"], f.get("label"))} for f in fields]
        facts.append(make_fact(
            "naming.field_name_contract", N_, n,
            {"n_fields": len(fields), "n_elements": n_elements, "elements_exceed_fields": n_elements > len(fields),
             **_label_counts(label_recs), "fields": label_recs},
            EvidenceClass.A, refs=field_refs))

        form_recs = [{"field": f["campo"], **R.field_name_form(f["campo"])} for f in fields]
        facts.append(make_fact(
            "naming.field_name_forms", N_, n,
            {"ruleset": R.FIELD_NAME_RULESET, "n_fields": len(fields),
             "form_histogram": R.histogram(r["form"] for r in form_recs),
             "case_style_histogram": R.histogram(r["case_style"] for r in form_recs),
             "custom_namespace_prefixed_count": sum(1 for r in form_recs if r["custom_namespace_prefix"]),
             "fields": form_recs},
            EvidenceClass.B, refs=field_refs))

        # continuidad del nombre por LINAJE OBSERVABLE: ancla observable mas temprana (local o remota) y, por separado,
        # ancla de fuente remota (solo si un `remote_entity` se observa). Nunca se afirma una fuente original externa.
        dist = graph.distances(n, [L])
        ancestors = sorted((a for a in dist if a != n and a in closure_set), key=lambda a: (dist[a], a))
        direct = [p for p in graph.providers(n, [L]) if p in closure_set]
        remote_anc = [a for a in ancestors if has_remote[a]]
        local_boundary_anc = [a for a in ancestors if not has_remote[a] and not graph.providers(a)]

        def anchor_kind(a: str) -> AnchorKind:
            if has_remote[a]:
                return AnchorKind.REMOTE_ENTITY_ARTIFACT
            return AnchorKind.LOCAL_BOUNDARY_ARTIFACT if not graph.providers(a) else AnchorKind.INTERMEDIATE_ARTIFACT

        cont_recs = []
        for f in fields:
            fname = f["campo"]
            in_direct = [p for p in direct if fname in names_of[p]]
            holders = [a for a in ancestors if fname in names_of[a]]
            holder_set = set(holders)
            # ancla mas temprana = artefacto del linaje con el nombre cuyos proveedores de linaje NO lo tienen
            anchors = [a for a in holders if not any(p in holder_set for p in graph.providers(a, [L]) if p in closure_set)] or holders
            if has_remote[n]:
                anchor = FieldNameAnchor.IS_REMOTE_ARTIFACT_FIELD
                anchor_recs = [{"artifact": n, "kind": AnchorKind.REMOTE_ENTITY_ARTIFACT.value}]
            elif not graph.providers(n):
                anchor = FieldNameAnchor.IS_LOCAL_BOUNDARY_FIELD
                anchor_recs = [{"artifact": n, "kind": AnchorKind.LOCAL_BOUNDARY_ARTIFACT.value}]
            elif not direct:
                anchor = FieldNameAnchor.NO_LINEAGE_PROVIDER
                anchor_recs = []
            else:
                anchor_recs = [{"artifact": a, "kind": anchor_kind(a).value} for a in anchors]
                kinds = {r["kind"] for r in anchor_recs}
                if AnchorKind.REMOTE_ENTITY_ARTIFACT.value in kinds:
                    anchor = FieldNameAnchor.ANCHORED_AT_REMOTE_ARTIFACT
                elif AnchorKind.LOCAL_BOUNDARY_ARTIFACT.value in kinds:
                    anchor = FieldNameAnchor.ANCHORED_AT_LOCAL_BOUNDARY
                elif anchor_recs:
                    anchor = FieldNameAnchor.FIRST_APPEARS_IN_INTERMEDIATE
                else:
                    anchor = FieldNameAnchor.FIRST_APPEARS_AT_THIS_ARTIFACT
                    anchor_recs = [{"artifact": n, "kind": AnchorKind.THIS_ARTIFACT.value}]
            remote_arts = [r["artifact"] for r in anchor_recs if r["kind"] == AnchorKind.REMOTE_ENTITY_ARTIFACT.value]
            cont_recs.append({"field": fname, "name_anchor": anchor.value, "earliest_observable_anchors": anchor_recs,
                              "remote_source_anchor": {"status": REMOTE_ANCHOR_OBSERVED if remote_arts else REMOTE_ANCHOR_UNAVAILABLE,
                                                       "artifacts": remote_arts},
                              "in_direct_lineage_provider": bool(in_direct),
                              "direct_lineage_providers_with_field": in_direct,
                              "derived": bool(f.get("derived"))})
        facts.append(make_fact(
            "naming.field_name_continuity", N_, n,
            {"n_fields": len(fields), "lineage_provider_count": len(direct), "lineage_ancestor_count": len(ancestors),
             "remote_artifact_ancestor_count": len(remote_anc), "local_boundary_ancestor_count": len(local_boundary_anc),
             "retained_from_direct_lineage_provider": sum(1 for r in cont_recs if r["in_direct_lineage_provider"]),
             "name_anchor_counts": _anchor_counts(r["name_anchor"] for r in cont_recs),
             "remote_source_anchor_counts": _remote_anchor_counts(r["remote_source_anchor"]["status"] for r in cont_recs),
             "fields": cont_recs},
            EvidenceClass.B,
            refs=[*field_refs, *(r for p in direct for r in entity_refs(entities[p], ["fields"]))]))

        by_field = {r["field"]: r for r in cont_recs}
        forms = {r["field"]: r for r in form_recs}
        for lr in label_recs:
            fr, cr = forms[lr["field"]], by_field[lr["field"]]
            field_rows.append({"artifact": n, "identity": idc.identity.value, **lr, "form": fr["form"],
                               "case_style": fr["case_style"], "custom_namespace_prefix": fr["custom_namespace_prefix"],
                               "anchor": cr["name_anchor"], "remote_anchor_status": cr["remote_source_anchor"]["status"]})

    # ---- poblaciones de artefacto por procedencia (B) ----------------------------------------------------------
    def group(rows: list[dict]) -> dict:
        compliant = [r for r in rows if r["compliant"]]
        non = [r for r in rows if not r["compliant"]]
        fam = R.histogram(f for r in rows for f in r["families"])
        return {"applicable": len(rows), "compliant": len(compliant), "non_compliant": len(non),
                "non_compliant_ratio": _ratio(len(non), len(rows)),
                "compliant_artifacts": [r["artifact"] for r in compliant],
                "non_compliant_artifacts": [r["artifact"] for r in non],
                "heuristic_identity_count": sum(1 for r in rows if r["heuristic"]),
                "violation_reason_histogram": R.histogram(i for r in rows for i in r["issues"]),
                "violation_family_histogram": fam, "distinct_violation_families": len(fam),
                "naming_family_histogram": R.histogram(r["family"] for r in rows),
                "name_shape_histogram": R.histogram(r["shape"] for r in rows)}

    sys_count = sum(1 for n in closure if ident[n].identity is SYSTEM)
    by_identity = []
    for prov in IdentityProvenance:
        rows = [r for r in artifact_rows if r["identity"] == prov.value]
        rec = {"identity": prov.value, "artifacts": sys_count if prov is SYSTEM else len(rows)}
        rec.update(group(rows) if prov is not SYSTEM else {"applicable": 0})
        by_identity.append(rec)
    facts.append(make_fact(
        "naming.artifact_name_populations", N_, "closure",
        {"closure_artifacts": len(closure), "system_artifacts_excluded": sys_count, "section_28": group(artifact_rows),
         "by_identity": by_identity,
         "basis": "current section 28 compliance via parse_name; provenance via identity-rules (M1); no quality judgment"},
        EvidenceClass.B, refs=[EvidenceRef(am, "closure", None, Plane.MODELING)]))

    # ---- agregados de campo (B) ----------------------------------------------------------------------------------
    def field_group(rows: list[dict]) -> dict:
        cross: dict[tuple[str, str], int] = {}
        for r in rows:
            cross[(r["anchor"], r["form"])] = cross.get((r["anchor"], r["form"]), 0) + 1
        return {"field_occurrences": len(rows), "artifacts_with_fields": len({r["artifact"] for r in rows}),
                **_label_counts(rows),
                "form_histogram": R.histogram(r["form"] for r in rows),
                "case_style_histogram": R.histogram(r["case_style"] for r in rows),
                "custom_namespace_prefixed_occurrences": sum(1 for r in rows if r["custom_namespace_prefix"]),
                "name_anchor_counts": _anchor_counts(r["anchor"] for r in rows),
                "remote_source_anchor_counts": _remote_anchor_counts(r["remote_anchor_status"] for r in rows),
                "anchor_by_form": [{"anchor": a, "form": f, "count": c} for (a, f), c in sorted(cross.items())]}

    with_fields = {r["artifact"] for r in field_rows}
    facts.append(make_fact(
        "naming.field_vocabulary_summary", N_, "closure",
        {"ruleset": R.FIELD_NAME_RULESET, "unit": "field occurrences (a name carried through several layers counts once per artifact)",
         "distinct_field_names": len({r["field"] for r in field_rows}),
         "artifacts_without_exposed_fields": [n for n in closure if ident[n].identity is not SYSTEM and n not in with_fields],
         **field_group(field_rows),
         "by_identity": [{"identity": p.value, **field_group([r for r in field_rows if r["identity"] == p.value])}
                         for p in IdentityProvenance if p is not SYSTEM],
         "terminal_am": {"artifact": am, **field_group([r for r in field_rows if r["artifact"] == am])}},
        EvidenceClass.B, refs=[EvidenceRef(am, "closure", None, Plane.MODELING)]))

    # ---- etiqueta normalizada compartida por >= 2 nombres tecnicos (B): evidencia, NO prueba de mismo concepto ----
    labelled: dict[str, dict[str, dict[str, bool]]] = {}
    for r in field_rows:
        if not r["label_present"]:
            continue                                           # sin etiqueta no hay etiqueta que compartir
        labelled.setdefault(R.normalize_label(r["label"]), {}).setdefault(r["field"], {})[r["artifact"]] = r["label_matches_field_normalized"]
    groups = []
    for label, names in labelled.items():
        if len(names) < 2:
            continue
        per_artifact: dict[str, list[str]] = {}
        for fname, arts in names.items():
            for a in arts:
                per_artifact.setdefault(a, []).append(fname)
        groups.append({"label": label,
                       "technical_names": [{"field": fname, "artifacts": sorted(arts),
                                            "label_matches_field_normalized_in_artifacts": sorted(a for a, eq in arts.items() if eq)}
                                           for fname, arts in sorted(names.items())],
                       "distinct_name_count": len(names),
                       "occurrence_count": sum(len(arts) for arts in names.values()),
                       "distinct_name_stems": len({R.numeric_suffix_stem(f) for f in names}),
                       "is_numbered_series": len({R.numeric_suffix_stem(f) for f in names}) == 1,
                       "artifacts_declaring_multiple_names": [{"artifact": a, "fields": sorted(fs)}
                                                             for a, fs in sorted(per_artifact.items()) if len(fs) > 1],
                       "terminal_am_fields": sorted(fname for fname, arts in names.items() if am in arts)})
    groups.sort(key=lambda g: (-g["distinct_name_count"], g["label"]))
    facts.append(make_fact(
        "naming.same_normalized_label_multiple_technical_names", N_, "closure",
        {"group_count": len(groups), "groups": groups,
         "basis": "equal normalized label (case-folded, whitespace-collapsed) shared by different technical names; empty labels excluded. "
                  "Equal labels do not prove that the technical names denote the same business concept, and no interpretation "
                  "is made here; numbered column series are kept in the population and flagged with is_numbered_series."},
        EvidenceClass.B, refs=[EvidenceRef(am, "closure", None, Plane.MODELING)]))

    # ---- contexto de convencion (D): explica, no reescribe ------------------------------------------------------
    facts.append(make_fact(
        "naming.convention_context", N_, "closure", {"open_context_questions": list(_CONTEXT_QUESTIONS)}, EvidenceClass.D,
        refs=[EvidenceRef(am, "closure", None, Plane.MODELING)],
        assessability=Assessability.NOT_ASSESSED, cause=Cause.CONTEXT_REQUIRED))

    return sort_facts(facts)
