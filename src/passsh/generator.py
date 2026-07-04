"""Cryptographically secure password generation."""

import secrets
import string

DEFAULT_LENGTH = 20
AMBIGUOUS_CHARS = "il1Lo0O"
SYMBOL_CHARS = "!@#$%^&*()-_=+[]{};:,.<>?"


def generate_password(
    length: int = DEFAULT_LENGTH,
    *,
    use_symbols: bool = True,
    exclude_ambiguous: bool = False,
) -> str:
    if length < 1:
        raise ValueError("length must be at least 1")

    pool = string.ascii_letters + string.digits
    if use_symbols:
        pool += SYMBOL_CHARS
    if exclude_ambiguous:
        pool = "".join(c for c in pool if c not in AMBIGUOUS_CHARS)

    return "".join(secrets.choice(pool) for _ in range(length))
