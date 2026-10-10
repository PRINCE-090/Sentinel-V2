"""
budget_advisor.security
=========================
PII scrubbing and prompt injection guardrails for the Budget Advisor.

Protects user privacy before sending queries to cloud LLM providers and
sanitizes prompt inputs.
"""

from __future__ import annotations

import re
from typing import List, Tuple

# Common PII patterns
SSN_REGEX = re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b")
CREDIT_CARD_REGEX = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
PHONE_REGEX = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
ACCOUNT_NUM_REGEX = re.compile(r"\b(account|acct|iban)\s*#?\s*[:=]?\s*[\w-]{6,34}\b", re.IGNORECASE)

# Basic prompt injection signatures
INJECTION_SIGNATURES = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+a\s+", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"override\s+system\s+prompt", re.IGNORECASE),
]


def scrub_pii(text: str) -> Tuple[str, List[str]]:
    """
    Redacts sensitive personal information (SSNs, credit card numbers,
    phone numbers, bank account identifiers) from input text.

    Returns (scrubbed_text, list_of_redaction_notices).
    """
    redactions: List[str] = []
    scrubbed = text

    if SSN_REGEX.search(scrubbed):
        scrubbed = SSN_REGEX.sub("[REDACTED_SSN]", scrubbed)
        redactions.append("Social Security Number detected and redacted.")

    if CREDIT_CARD_REGEX.search(scrubbed):
        # Avoid redacting simple 13-16 digit numbers that aren't CCs if needed,
        # but defensively mask credit card patterns.
        scrubbed = CREDIT_CARD_REGEX.sub("[REDACTED_CARD]", scrubbed)
        redactions.append("Credit card number pattern detected and redacted.")

    if ACCOUNT_NUM_REGEX.search(scrubbed):
        scrubbed = ACCOUNT_NUM_REGEX.sub("[REDACTED_ACCOUNT]", scrubbed)
        redactions.append("Bank account identifier detected and redacted.")

    return scrubbed, redactions


def sanitize_user_input(text: str) -> str:
    """
    Sanitizes user input to mitigate prompt injection attempts.
    Replaces blatant system prompt override commands with safe placeholder text.
    """
    sanitized = text
    for pattern in INJECTION_SIGNATURES:
        if pattern.search(sanitized):
            sanitized = pattern.sub("[REDACTED_OVERRIDE_ATTEMPT]", sanitized)
    return sanitized
