"""Key derivation and AES-256-GCM encryption/decryption primitives."""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

SALT_SIZE = 16
KEY_SIZE = 32
NONCE_SIZE = 12

# Argon2id cost parameters.
#
# These are deliberately heavier than the OWASP minimums (m=19 MiB, t=2, p=1)
# because this KDF only ever runs once per CLI invocation on the user's own
# machine to unlock a local vault -- there is no login-endpoint throughput
# constraint, so we spend extra time/memory to raise the cost of offline
# password guessing against a stolen vault file.
ARGON2_TIME_COST = 3           # iterations
ARGON2_MEMORY_COST = 64 * 1024  # KiB (64 MiB)
ARGON2_PARALLELISM = 4          # lanes


def generate_salt() -> bytes:
    return os.urandom(SALT_SIZE)


def generate_nonce() -> bytes:
    return os.urandom(NONCE_SIZE)


def derive_key(
    password: bytes,
    salt: bytes,
    *,
    time_cost: int = ARGON2_TIME_COST,
    memory_cost: int = ARGON2_MEMORY_COST,
    parallelism: int = ARGON2_PARALLELISM,
) -> bytes:
    kdf = Argon2id(
        salt=salt,
        length=KEY_SIZE,
        iterations=time_cost,
        lanes=parallelism,
        memory_cost=memory_cost,
    )
    return kdf.derive(password)


def encrypt(plaintext: bytes, key: bytes, aad: bytes = b"") -> bytes:
    """Encrypt `plaintext` under `key`, returning nonce || ciphertext.

    A fresh random nonce is generated on every call and prepended to the
    returned blob so the result is self-contained and safe to store as-is.
    """
    nonce = generate_nonce()
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce + ciphertext


def decrypt(ciphertext: bytes, key: bytes, aad: bytes = b"") -> bytes:
    """Decrypt a nonce || ciphertext blob produced by `encrypt`.

    Raises `cryptography.exceptions.InvalidTag` if `key`/`aad` are wrong or
    the blob has been tampered with.
    """
    nonce, actual_ciphertext = ciphertext[:NONCE_SIZE], ciphertext[NONCE_SIZE:]
    return AESGCM(key).decrypt(nonce, actual_ciphertext, aad)
