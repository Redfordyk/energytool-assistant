from app.dialogue import DialogueState, state_after_answer


def test_greeting_enters_discovery():
    assert state_after_answer({'query_plan': 'deterministic_greeting'}) == DialogueState.DISCOVERY


def test_deterministic_tariff_question_enters_clarification():
    assert (
        state_after_answer({'query_plan': 'clarify_missing_tariff_input'})
        == DialogueState.CLARIFYING
    )


def test_customer_requested_escalation_enters_lead_offer():
    assert (
        state_after_answer({'query_plan': 'deterministic_escalation_offer'})
        == DialogueState.LEAD_OFFERED
    )


def test_unclassified_answers_stay_in_consulting():
    assert state_after_answer({}) == DialogueState.CONSULTING
