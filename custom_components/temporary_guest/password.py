"""Password generation helpers.

This module intentionally has no Home Assistant imports so its security-critical
logic can be tested in isolation.
"""

import random
import secrets
import string

from .const import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

_LOWER = string.ascii_lowercase
_UPPER = string.ascii_uppercase
_DIGITS = string.digits
# Avoid quotes, backslashes, spaces and visually troublesome punctuation.
_SYMBOLS = "!@#$%*-_=+?"
_ALPHABET = _LOWER + _UPPER + _DIGITS + _SYMBOLS
_SYSTEM_RANDOM = random.SystemRandom()


def generate_password(length: int) -> str:
    """Return a cryptographically strong password with all character classes."""
    if not MIN_PASSWORD_LENGTH <= length <= MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password length must be between {MIN_PASSWORD_LENGTH} and "
            f"{MAX_PASSWORD_LENGTH}"
        )

    characters = [
        secrets.choice(_LOWER),
        secrets.choice(_UPPER),
        secrets.choice(_DIGITS),
        secrets.choice(_SYMBOLS),
    ]
    characters.extend(secrets.choice(_ALPHABET) for _ in range(length - 4))
    _SYSTEM_RANDOM.shuffle(characters)
    return "".join(characters)

