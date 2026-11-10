"""Deterministic consultation lifecycle labels for persisted sessions."""

from collections.abc import Mapping
from enum import StrEnum


class DialogueState(StrEnum):
    NEW = 'NEW'
    DISCOVERY = 'DISCOVERY'
    CLARIFYING = 'CLARIFYING'
    CONSULTING = 'CONSULTING'
    RECOMMENDING = 'RECOMMENDING'
    LEAD_OFFERED = 'LEAD_OFFERED'
    LEAD_COLLECTING = 'LEAD_COLLECTING'
    LEAD_REVIEW = 'LEAD_REVIEW'
    LEAD_CREATED = 'LEAD_CREATED'
    ESCALATED = 'ESCALATED'
    CLOSED = 'CLOSED'


def state_after_answer(diagnostics: Mapping[str, object]) -> DialogueState:
    """Derive only states the current deterministic router can prove."""
    if diagnostics.get('escalation_reason') or diagnostics.get('query_plan') == (
        'deterministic_escalation_offer'
    ):
        return DialogueState.LEAD_OFFERED
    if diagnostics.get('query_plan') == 'deterministic_greeting':
        return DialogueState.DISCOVERY
    if diagnostics.get('query_plan') == 'clarify_missing_tariff_input':
        return DialogueState.CLARIFYING
    return DialogueState.CONSULTING
