"""Cryptographic and room derivation helpers for Adlos E2EE protocol."""

import os
import re
import base64
import hashlib
import logging
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

_LOGGER = logging.getLogger(__name__)


def derive_room_id(id1: str, id2: str) -> str:
    """Derives the primary deterministic 15-character Room ID (sha256 with single '_')."""
    ids = sorted([str(id1).strip(), str(id2).strip()])
    digest = hashlib.sha256("_".join(ids).encode("utf-8")).hexdigest()
    clean = re.sub(r"[^a-zA-Z0-9]", "", digest).lower()
    return clean[:15]


def derive_room_id_alt(id1: str, id2: str) -> str:
    """Derives the alternative deterministic 15-character Room ID (sha256 with double '__')."""
    ids = sorted([str(id1).strip(), str(id2).strip()])
    digest = hashlib.sha256("__".join(ids).encode("utf-8")).hexdigest()
    clean = re.sub(r"[^a-zA-Z0-9]", "", digest).lower()
    return clean[:15]


def all_candidate_rooms(user_id: str, bot_id: str, user_name: str = "") -> set[str]:
    """Returns all possible room IDs that Adlos might use for a given user and bot."""
    rooms = {
        bot_id.strip(),
        derive_room_id(user_id, bot_id),
        derive_room_id_alt(user_id, bot_id),
    }
    if user_name and user_name.strip():
        rooms.add(derive_room_id(user_name.strip(), bot_id))
        rooms.add(derive_room_id_alt(user_name.strip(), bot_id))
    return rooms


def derive_deterministic_room_key(room_id: str) -> str:
    """Fallback deterministic room key matching EncryptionHelper.deriveDeterministicRoomKey in Dart."""
    raw = f"adlos_e2ee_secret_v1_{room_id}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8")


def decode_key_bytes(key_input: str | bytes | None) -> bytes:
    """Decodes a base64/base64url string or bytes into a 32-byte AES key."""
    if not key_input:
        return b""
    if isinstance(key_input, bytes):
        if len(key_input) == 32:
            return key_input
        return hashlib.sha256(key_input).digest()

    raw_str = str(key_input).strip()
    if not raw_str:
        return b""

    # Try URL-safe base64 with padding
    try:
        b64_url = raw_str
        while len(b64_url) % 4 != 0:
            b64_url += "="
        decoded = base64.urlsafe_b64decode(b64_url)
        if len(decoded) == 32:
            return decoded
    except Exception:
        pass

    # Try standard base64 with padding
    try:
        b64 = raw_str.replace("-", "+").replace("_", "/")
        while len(b64) % 4 != 0:
            b64 += "="
        decoded = base64.b64decode(b64)
        if len(decoded) == 32:
            return decoded
    except Exception:
        pass

    return hashlib.sha256(raw_str.encode("utf-8")).digest()


def encrypt_text(plain_text: str, key: str | bytes | None) -> str:
    """Encrypts plain text with AES-256-CBC and PKCS7 padding. Returns iv_base64:ciphertext_base64."""
    if not plain_text:
        return ""

    key_bytes = decode_key_bytes(key)
    if not key_bytes or len(key_bytes) != 32:
        return plain_text

    try:
        iv = os.urandom(16)
        padder = padding.PKCS7(128).padder()
        padded_data = padder.update(plain_text.encode("utf-8")) + padder.finalize()

        cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()

        iv_b64 = base64.b64encode(iv).decode("utf-8")
        cipher_b64 = base64.b64encode(ciphertext).decode("utf-8")
        return f"{iv_b64}:{cipher_b64}"
    except Exception as err:
        _LOGGER.error("ADLOS_CRYPTO: Text encryption failed: %s", err)
        return plain_text


def decrypt_text(cipher_text: str, key: str | bytes | None) -> str:
    """Decrypts iv_base64:ciphertext_base64 with AES-256-CBC and PKCS7 padding. Returns plain text."""
    if not cipher_text:
        return ""

    if ":" not in cipher_text:
        return cipher_text

    key_bytes = decode_key_bytes(key)
    if not key_bytes or len(key_bytes) != 32:
        return cipher_text

    try:
        parts = cipher_text.split(":", 1)
        iv = base64.b64decode(parts[0])
        ct = base64.b64decode(parts[1])

        cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded_plain = decryptor.update(ct) + decryptor.finalize()

        unpadder = padding.PKCS7(128).unpadder()
        plain_bytes = unpadder.update(padded_plain) + unpadder.finalize()
        return plain_bytes.decode("utf-8")
    except Exception as err:
        _LOGGER.debug("ADLOS_CRYPTO: Text decryption failed: %s", err)
        return cipher_text


def encrypt_bytes(data_bytes: bytes, key: str | bytes | None) -> bytes:
    """Encrypts raw file bytes using AES-256-CBC with PKCS7 padding (16-byte IV prefix + ciphertext)."""
    if not data_bytes:
        return data_bytes

    key_bytes = decode_key_bytes(key)
    if not key_bytes or len(key_bytes) != 32:
        return data_bytes

    try:
        iv = os.urandom(16)
        padder = padding.PKCS7(128).padder()
        padded_data = padder.update(data_bytes) + padder.finalize()

        cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()
        return iv + ciphertext
    except Exception as err:
        _LOGGER.error("ADLOS_CRYPTO: File byte encryption failed: %s", err)
        return data_bytes
