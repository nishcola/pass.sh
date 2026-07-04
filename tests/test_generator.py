import pytest

from passsh import generator


def test_default_length():
    assert len(generator.generate_password()) == generator.DEFAULT_LENGTH


def test_custom_length():
    assert len(generator.generate_password(32)) == 32


def test_zero_or_negative_length_raises():
    with pytest.raises(ValueError):
        generator.generate_password(0)


def test_no_symbols_excludes_symbol_chars():
    password = generator.generate_password(200, use_symbols=False)
    assert not any(c in generator.SYMBOL_CHARS for c in password)


def test_with_symbols_can_include_symbol_chars():
    # Statistically near-certain over many long samples; not flaky in practice.
    passwords = [generator.generate_password(100, use_symbols=True) for _ in range(20)]
    assert any(any(c in generator.SYMBOL_CHARS for c in p) for p in passwords)


def test_exclude_ambiguous_removes_ambiguous_chars():
    password = generator.generate_password(300, exclude_ambiguous=True)
    assert not any(c in generator.AMBIGUOUS_CHARS for c in password)


def test_passwords_are_not_identical_across_calls():
    a = generator.generate_password(40)
    b = generator.generate_password(40)
    assert a != b
