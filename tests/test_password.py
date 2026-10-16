"""Tests for password generation."""

import string

import pytest

from custom_components.temporary_guest.const import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
)
from custom_components.temporary_guest.password import generate_password


@pytest.mark.parametrize("length", [16, 24, 64])
def test_generate_password_has_required_classes(length: int) -> None:
    """Generated values have the requested size and each character class."""
    password = generate_password(length)
    assert len(password) == length
    assert any(char in string.ascii_lowercase for char in password)
    assert any(char in string.ascii_uppercase for char in password)
    assert any(char in string.digits for char in password)
    assert any(char in "!@#$%*-_=+?" for char in password)
    assert not any(char in "'\"\\ `" for char in password)


def test_generate_password_is_not_reused() -> None:
    """A useful sample contains no duplicate generated passwords."""
    assert len({generate_password(24) for _ in range(200)}) == 200


@pytest.mark.parametrize("length", [MIN_PASSWORD_LENGTH - 1, MAX_PASSWORD_LENGTH + 1])
def test_generate_password_rejects_unsafe_lengths(length: int) -> None:
    """Out-of-policy lengths are rejected."""
    with pytest.raises(ValueError):
        generate_password(length)

