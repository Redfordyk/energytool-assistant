import itertools
import json

import pytest
from fastapi import HTTPException

from app.lead_service import normalize_phone, validate_key
from core.rag import Chunk, validate_claims

_SUPPORTED_UNIT_PAIRS = (
    ('кВт', 'кВт'),
    ('МВт', 'мвт'),
    ('кВт·ч', 'кВт⋅ч'),
    ('кВА', 'ква'),
    ('киловольт', 'кВ'),
    ('%', 'процентов'),
    ('рублей', '₽'),
    ('дней', 'суток'),
    ('года', 'лет'),
    ('часов', 'часа'),
)
SUPPORTED_CLAIMS = tuple(
    (f'Показатель {index},5 {evidence_unit}.', f'Показатель {index},5 {claim_unit}.')
    for (evidence_unit, claim_unit), index in itertools.product(_SUPPORTED_UNIT_PAIRS, range(1, 11))
)

_INCOMPATIBLE_UNIT_PAIRS = (
    ('кВт', 'кВт·ч'),
    ('МВт', 'кВт'),
    ('кВ', 'кВт'),
    ('рублей', 'процентов'),
    ('дней', 'месяцев'),
    ('часов', 'дней'),
    ('кВА', 'МВА'),
    ('месяцев', 'лет'),
    ('суток', 'часов'),
    ('мВт·ч', 'кВт·ч'),
)
WRONG_UNIT_CLAIMS = tuple(
    (f'Показатель {index} {evidence_unit}.', f'Показатель {index} {claim_unit}.')
    for (evidence_unit, claim_unit), index in itertools.product(
        _INCOMPATIBLE_UNIT_PAIRS, range(1, 6)
    )
)
WRONG_VALUE_CLAIMS = tuple(
    (f'Показатель {index} кВт.', f'Показатель {index + 100} кВт.') for index in range(1, 51)
)

VALID_PHONES = tuple(
    f'+7 900 {index:03d} {index % 100:02d} {index % 10:02d}' for index in range(100)
)
INVALID_PHONE_SUFFIXES = ('x', '/', ';', '!', '_', 'e', '\n', '\t', '🙂', '\\')
INVALID_PHONES = tuple(
    VALID_PHONES[index] + suffix
    for index, suffix in itertools.product(range(10), INVALID_PHONE_SUFFIXES)
)

VALID_IDEMPOTENCY_KEYS = tuple(f'Case_{index:04d}-Key' for index in range(50))
INVALID_KEY_MARKERS = ('!', '.', ' ', '/', '+', 'é', '\n', '\t', '%', ':')
INVALID_IDEMPOTENCY_KEYS = tuple(
    f'case-{index:03d}{marker}'
    for index, marker in itertools.product(range(5), INVALID_KEY_MARKERS)
)


def _validate_claim(claim, evidence):
    raw = json.dumps(
        {'claims': [{'text': claim, 'source': 1, 'quote': evidence}]}, ensure_ascii=False
    )
    return validate_claims(raw, [Chunk('contract.md', evidence)])


@pytest.mark.parametrize('evidence,claim', SUPPORTED_CLAIMS)
def test_100_supported_quantities_and_unit_equivalences_are_accepted(evidence, claim):
    assert _validate_claim(claim, evidence) == [{'text': claim, 'source': 1, 'quote': evidence}]


@pytest.mark.parametrize('evidence,claim', WRONG_UNIT_CLAIMS)
def test_50_changed_physical_units_are_rejected(evidence, claim):
    assert _validate_claim(claim, evidence) is None


@pytest.mark.parametrize('evidence,claim', WRONG_VALUE_CLAIMS)
def test_50_changed_numeric_values_are_rejected(evidence, claim):
    assert _validate_claim(claim, evidence) is None


@pytest.mark.parametrize(
    'phone', VALID_PHONES, ids=[f'valid-phone-{index:03d}' for index in range(100)]
)
def test_100_possible_ru_mobile_numbers_normalize_to_e164(phone):
    expected = '+7900' + ''.join(character for character in phone[6:] if character.isdigit())
    assert normalize_phone(phone) == expected


@pytest.mark.parametrize(
    'phone', INVALID_PHONES, ids=[f'invalid-phone-{index:03d}' for index in range(100)]
)
def test_100_phones_with_unsupported_characters_are_rejected(phone):
    with pytest.raises(HTTPException) as error:
        normalize_phone(phone)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    'key',
    VALID_IDEMPOTENCY_KEYS,
    ids=[f'valid-key-{index:03d}' for index in range(50)],
)
def test_50_valid_idempotency_keys_are_accepted(key):
    validate_key(key)


@pytest.mark.parametrize(
    'key',
    INVALID_IDEMPOTENCY_KEYS,
    ids=[f'invalid-key-{index:03d}' for index in range(50)],
)
def test_50_malformed_idempotency_keys_are_rejected(key):
    with pytest.raises(HTTPException) as error:
        validate_key(key)
    assert error.value.status_code == 422
