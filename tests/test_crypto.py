import os

import pytest
from cryptography.exceptions import InvalidTag

from passsh import crypto


def test_round_trip():
    key = os.urandom(crypto.KEY_SIZE)
    plaintext = b"correct horse battery staple"

    blob = crypto.encrypt(plaintext, key)
    assert crypto.decrypt(blob, key) == plaintext


def test_round_trip_empty_plaintext():
    key = os.urandom(crypto.KEY_SIZE)

    blob = crypto.encrypt(b"", key)
    assert crypto.decrypt(blob, key) == b""


def test_nonce_is_fresh_per_call():
    key = os.urandom(crypto.KEY_SIZE)
    plaintext = b"same plaintext"

    blob_a = crypto.encrypt(plaintext, key)
    blob_b = crypto.encrypt(plaintext, key)

    nonce_a = blob_a[: crypto.NONCE_SIZE]
    nonce_b = blob_b[: crypto.NONCE_SIZE]
    assert nonce_a != nonce_b
    assert blob_a != blob_b


def test_tampered_ciphertext_raises_invalid_tag():
    key = os.urandom(crypto.KEY_SIZE)
    blob = bytearray(crypto.encrypt(b"top secret", key))

    # Flip a bit in the ciphertext body (after the nonce prefix).
    blob[crypto.NONCE_SIZE] ^= 0x01

    with pytest.raises(InvalidTag):
        crypto.decrypt(bytes(blob), key)


def test_wrong_key_raises_invalid_tag():
    key = os.urandom(crypto.KEY_SIZE)
    wrong_key = os.urandom(crypto.KEY_SIZE)
    blob = crypto.encrypt(b"top secret", key)

    with pytest.raises(InvalidTag):
        crypto.decrypt(blob, wrong_key)


def test_tampered_aad_raises_invalid_tag():
    key = os.urandom(crypto.KEY_SIZE)
    blob = crypto.encrypt(b"top secret", key, aad=b"header-v1")

    with pytest.raises(InvalidTag):
        crypto.decrypt(blob, key, aad=b"header-v2")
