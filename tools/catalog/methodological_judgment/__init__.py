"""Juicio metodologico v1 (nucleo puro). Consumidor HERMANO de la evidencia canonica del Assessment (no es parte del Assessment ni de su perfil).

    evaluate_judgment(profile, dispositions=None)      -> dict (determinista, sin escrituras)
    build_executive_input(judgment, profile)            -> dict (entrada inmutable para un futuro Asesor LLM)
    validate_executive_output(output, executive_input)  -> ValidationResult (contrato de salida del futuro LLM; NO llama a ningun modelo)
"""
from .contract import CTA_PLAYBOOK_VERSION, EXECUTIVE_INPUT_VERSION, CURRENT_RULESET_VERSION, EXECUTIVE_OUTPUT_CONTRACT_VERSION, JUDGMENT_SCHEMA_VERSION, RULESET_V2, RULESET_V3, RULESET_VERSION
from .engine import evaluate_judgment
from .executive import build_executive_input
from .executive_validator import ValidationResult, validate_executive_output

__all__ = ["evaluate_judgment", "build_executive_input", "validate_executive_output", "ValidationResult", "JUDGMENT_SCHEMA_VERSION", "RULESET_VERSION", "RULESET_V2", "RULESET_V3", "CURRENT_RULESET_VERSION",
           "EXECUTIVE_INPUT_VERSION", "EXECUTIVE_OUTPUT_CONTRACT_VERSION", "CTA_PLAYBOOK_VERSION"]
