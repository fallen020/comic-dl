"""Cookie value encryption at rest (AES-256-GCM).

Cookie values persist in ``cookies.db``; a reader who can see the file should
not get working session tokens. Encrypted values are stored as ``enc1.`` +
base64url(nonce + ciphertext + tag): values with that prefix are decrypted on
read, legacy plaintext rows keep working untouched, and an ``enc1.`` value
that fails authentication or decryption is discarded as corrupt.

The key is 32 random bytes held in the OS keyring and generated on first use.
``COMIC_DL_COOKIE_KEY`` (base64url of the raw key) overrides the keyring for
CI/headless machines; ``[http] cookie-encryption = \"off\"`` forces a
plaintext jar.

Pure functions only, no classes and no state: jar wiring decides whether a
key exists and warns, this module only does crypto.
"""

from __future__ import annotations

import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = "enc1."
_NONCE_BYTES = 12
_KEY_BYTES = 32
_KEY_ENV = "COMIC_DL_COOKIE_KEY"
_KEYRING_SERVICE = "comic-dl"
_KEYRING_USER = "cookie-db-key"


def is_encrypted(value: str) -> bool:
    """Tells legacy plaintext (no prefix) from envelope values, so readers
    know whether to decrypt before serving a cookie."""
    return value.startswith(_PREFIX)


def encrypt_value(key: bytes, plaintext: str) -> str:
    """Fresh randomized envelope for ``plaintext``: never deterministic.

    A new 12-byte nonce is drawn per value, so equal plaintexts never share a
    ciphertext (and re-writing the same cookie changes its stored bytes).
    """
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _PREFIX + _urlsafe_b64(nonce + ciphertext)


def decrypt_value(key: bytes, stored: str) -> str | None:
    """Plaintext for an ``enc1.`` envelope, or ``None`` when it is invalid.

    ``None`` covers corrupt blobs, truncated payloads, a wrong key, and
    non-UTF8 plaintext — callers drop the value rather than serve garbage.
    """
    payload = _urlsafe_b64decode(stored[len(_PREFIX) :])
    if payload is None or len(payload) <= _NONCE_BYTES:
        return None
    nonce = payload[:_NONCE_BYTES]
    try:
        return AESGCM(key).decrypt(nonce, payload[_NONCE_BYTES:], None).decode("utf-8")
    except Exception:
        return None


def resolve_key(mode: str) -> bytes | None:
    """Source the jar key for ``mode`` (``auto`` | ``keyring`` | ``off``).

    ``auto`` prefers ``COMIC_DL_COOKIE_KEY``, then the OS keyring (creating
    and storing a fresh key on first use). ``keyring`` skips the environment
    override. ``off`` returns ``None`` (plaintext jar). Any source that fails
    — env var set to an unusable value, no keyring backend, backend error —
    also returns ``None`` so the jar degrades to plaintext instead of breaking
    downloads; the caller's once-per-run warning covers that fallback.
    """
    if mode == "off":
        return None
    if mode == "auto":
        key = _env_key()
        if key is not None:
            return key
    if keyring_available():
        stored = _keyring_get()
        if stored is None:
            stored = secrets.token_bytes(_KEY_BYTES)
            if _keyring_set(stored):
                return stored
        elif _key_len_ok(stored):
            return stored
    return None


def keyring_available() -> bool:
    """True when an OS keyring backend is importable and registered."""
    try:
        from keyring.backend import get_all_keyring

        return bool(list(get_all_keyring()))
    except Exception:
        return False


def _keyring_get() -> bytes | None:
    """Stored key bytes, or ``None`` when absent/unusable."""
    try:
        import keyring

        stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
    except Exception:
        return None
    if not stored:
        return None
    decoded = _urlsafe_b64decode(stored)
    if not _key_len_ok(decoded):
        return None
    return decoded


def _keyring_set(key: bytes) -> bool:
    try:
        import keyring

        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, _urlsafe_b64(key))
        return True
    except Exception:
        return False


def _env_key() -> bytes | None:
    """Key from the env var, or ``None`` when unset or malformed."""
    value = os.environ.get(_KEY_ENV, "").strip()
    if not value:
        return None
    decoded = _urlsafe_b64decode(value)
    if not _key_len_ok(decoded):
        return None
    return decoded


def _key_len_ok(key: bytes | None) -> bool:
    return key is not None and len(key) == _KEY_BYTES


def _urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes | None:
    """Lenient base64url decode (tolerates missing/extra padding)."""
    padded = value.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except (ValueError, TypeError):
        return None
