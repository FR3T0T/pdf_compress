"""
EPDF Crypto — Enhanced PDF encryption engine.

Provides a custom .epdf binary format that wraps PDF files with
modern cryptographic algorithms beyond the PDF specification:

Ciphers:
  - ChaCha20-Poly1305 (256-bit, AEAD)
  - AES-256-GCM (AEAD)
  - Camellia-256-CBC + HMAC-SHA256 (encrypt-then-MAC)

Key Derivation:
  - Argon2id (recommended — resistant to both side-channel and GPU attacks)
  - Argon2d (faster — resistant to GPU attacks only)

File format:
  [8 bytes]  Magic: b"EPDF\\x00\\x01\\x00\\x00"
  [4 bytes]  Metadata length (uint32 little-endian)
  [N bytes]  JSON metadata (cipher, kdf, params, salt, nonce, etc.)
  [rest]     Encrypted PDF bytes (+ auth tag for AEAD ciphers)
"""

import hashlib
import hmac
import json
import logging
import os
import struct
import tempfile
from base64 import b64decode, b64encode
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════

EPDF_MAGIC = b"EPDF\x00\x01\x00\x00"
EPDF_VERSION = 2  # v2 authenticates the header as Associated Data (v1 still readable)

CIPHERS = {
    "chacha20-poly1305": "ChaCha20-Poly1305 256-bit",
    "aes-256-gcm":       "AES-256-GCM",
    "camellia-256-cbc":  "Camellia-256-CBC + HMAC",
}

KDFS = {
    "argon2id": "Argon2id (recommended)",
    "argon2d":  "Argon2d",
}

DEFAULT_KDF_PARAMS = {
    "time_cost":   3,
    "memory_cost": 65536,   # 64 MB
    "parallelism": 4,
}

# Hard bounds on Argon2 cost parameters. Decryption reads these from the
# .epdf header, which is attacker-controllable — an unbounded memory_cost or
# time_cost would let a hostile file OOM or hang the machine during key
# derivation (a denial-of-service vector). Values outside this range are
# rejected. The ceilings sit far above the defaults, so legitimate
# high-security files still derive normally.
MIN_KDF_PARAMS = {
    "time_cost":   1,
    "memory_cost": 8,               # Argon2's own floor
    "parallelism": 1,
}
MAX_KDF_PARAMS = {
    "time_cost":   20,
    "memory_cost": 2 * 1024 * 1024,  # 2 GiB (Argon2 memory_cost is in KiB)
    "parallelism": 16,
}

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB

# ═══════════════════════════════════════════════════════════════════
#  Exceptions
# ═══════════════════════════════════════════════════════════════════

class EPDFError(Exception):
    """Base exception for EPDF operations."""

class EPDFFormatError(EPDFError):
    """File is not a valid .epdf or has a corrupted header."""

class EPDFDecryptionError(EPDFError):
    """Decryption failed — data integrity check failed."""

class EPDFPasswordError(EPDFError):
    """Wrong password or corrupted key derivation."""

class EPDFFileTooLargeError(EPDFError):
    """Input file exceeds maximum size."""

# ═══════════════════════════════════════════════════════════════════
#  Key derivation
# ═══════════════════════════════════════════════════════════════════

def _validate_kdf_params(params: dict) -> dict:
    """Validate Argon2 cost parameters, returning a clean int-typed dict.

    Guards key derivation against hostile .epdf headers that request absurd
    memory/time — a denial-of-service vector, since decrypt takes these
    straight from the (unauthenticated-until-AEAD) file header. Raises
    EPDFFormatError on any non-integer or out-of-range value.
    """
    clean: dict = {}
    for name in ("time_cost", "memory_cost", "parallelism"):
        value = params.get(name, DEFAULT_KDF_PARAMS[name])
        # bool is an int subclass — reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            raise EPDFFormatError(
                f"Invalid KDF parameter {name!r}: must be an integer")
        if not (MIN_KDF_PARAMS[name] <= value <= MAX_KDF_PARAMS[name]):
            raise EPDFFormatError(
                f"KDF parameter {name!r}={value} is outside the allowed range "
                f"[{MIN_KDF_PARAMS[name]}, {MAX_KDF_PARAMS[name]}] — refusing "
                f"to derive key (possible denial-of-service file)")
        clean[name] = value
    # Argon2 additionally requires memory_cost >= 8 * parallelism.
    if clean["memory_cost"] < 8 * clean["parallelism"]:
        raise EPDFFormatError(
            "KDF memory_cost is too low for the requested parallelism")
    return clean


def _derive_key(password: str, salt: bytes, kdf: str = "argon2id",
                kdf_params: dict | None = None) -> bytes:
    """Derive a 32-byte encryption key from a password using Argon2."""
    import argon2.low_level

    # kdf_params can come straight from an attacker-controlled .epdf header
    # (decrypt path); a non-mapping value (e.g. a JSON list or string) would
    # otherwise raise a raw TypeError from the ** spread below, escaping
    # _validate_kdf_params' EPDFFormatError path entirely (CRY-02).
    if kdf_params is not None and not isinstance(kdf_params, dict):
        raise EPDFFormatError(
            f"Invalid kdf_params: expected an object, got {type(kdf_params).__name__}")

    params = _validate_kdf_params({**DEFAULT_KDF_PARAMS, **(kdf_params or {})})

    if kdf == "argon2id":
        variant = argon2.low_level.Type.ID
    elif kdf == "argon2d":
        variant = argon2.low_level.Type.D
    else:
        raise EPDFError(f"Unsupported KDF: {kdf}")

    return argon2.low_level.hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=params["time_cost"],
        memory_cost=params["memory_cost"],
        parallelism=params["parallelism"],
        hash_len=32,
        type=variant,
    )


# ═══════════════════════════════════════════════════════════════════
#  Cipher operations
# ═══════════════════════════════════════════════════════════════════

def _encrypt_chacha20(key: bytes, nonce: bytes, plaintext: bytes,
                      aad: bytes | None) -> bytes:
    """Encrypt with ChaCha20-Poly1305. Returns ciphertext+tag."""
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    return ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)


def _decrypt_chacha20(key: bytes, nonce: bytes, ciphertext: bytes,
                      aad: bytes | None) -> bytes:
    """Decrypt with ChaCha20-Poly1305."""
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    try:
        return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, aad)
    except Exception as exc:
        raise EPDFPasswordError("Decryption failed — wrong password or corrupted/tampered data") from exc


def _encrypt_aes_gcm(key: bytes, nonce: bytes, plaintext: bytes,
                     aad: bytes | None) -> bytes:
    """Encrypt with AES-256-GCM. Returns ciphertext+tag (tag appended)."""
    from Crypto.Cipher import AES
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    if aad:
        cipher.update(aad)
    ct, tag = cipher.encrypt_and_digest(plaintext)
    return ct + tag  # 16-byte tag appended


def _decrypt_aes_gcm(key: bytes, nonce: bytes, ciphertext: bytes,
                     aad: bytes | None) -> bytes:
    """Decrypt with AES-256-GCM."""
    from Crypto.Cipher import AES
    if len(ciphertext) < 16:
        raise EPDFDecryptionError("Ciphertext too short for AES-GCM")
    ct, tag = ciphertext[:-16], ciphertext[-16:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    if aad:
        cipher.update(aad)
    try:
        return cipher.decrypt_and_verify(ct, tag)
    except Exception as exc:
        raise EPDFPasswordError("Decryption failed — wrong password or corrupted/tampered data") from exc


def _encrypt_camellia(key: bytes, iv: bytes, plaintext: bytes,
                      aad: bytes | None) -> bytes:
    """Encrypt with Camellia-256-CBC + HMAC-SHA256 (encrypt-then-MAC).

    The HMAC covers (aad + iv + ciphertext) so the header is authenticated
    alongside the payload.  Returns ciphertext+hmac.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()

    cipher = Cipher(algorithms.Camellia(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()

    auth_key = hashlib.sha256(key + b"camellia-auth").digest()
    mac = hmac.new(auth_key, (aad or b"") + iv + ct, hashlib.sha256).digest()
    return ct + mac  # 32-byte HMAC appended


def _decrypt_camellia(key: bytes, iv: bytes, ciphertext: bytes,
                      aad: bytes | None) -> bytes:
    """Decrypt with Camellia-256-CBC, verify HMAC-SHA256 over header+iv+ct."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    if len(ciphertext) < 48:  # at least 16 bytes data + 32 bytes HMAC
        raise EPDFDecryptionError("Ciphertext too short for Camellia")

    ct, received_mac = ciphertext[:-32], ciphertext[-32:]

    auth_key = hashlib.sha256(key + b"camellia-auth").digest()
    expected_mac = hmac.new(auth_key, (aad or b"") + iv + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(received_mac, expected_mac):
        raise EPDFPasswordError("Decryption failed — wrong password or corrupted/tampered data")

    cipher = Cipher(algorithms.Camellia(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()

    try:
        unpadder = PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise EPDFDecryptionError("Invalid padding after decryption") from exc


#: Nonce/IV length per cipher
_NONCE_LEN = {
    "chacha20-poly1305": 12,
    "aes-256-gcm":       12,
    "camellia-256-cbc":  16,
}


# Dispatch tables
_ENCRYPTORS = {
    "chacha20-poly1305": _encrypt_chacha20,
    "aes-256-gcm":       _encrypt_aes_gcm,
    "camellia-256-cbc":  _encrypt_camellia,
}

_DECRYPTORS = {
    "chacha20-poly1305": _decrypt_chacha20,
    "aes-256-gcm":       _decrypt_aes_gcm,
    "camellia-256-cbc":  _decrypt_camellia,
}


# ═══════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════

def is_epdf(filepath: str) -> bool:
    """Check whether a file is an .epdf encrypted container."""
    try:
        with open(filepath, "rb") as f:
            return f.read(8) == EPDF_MAGIC
    except (OSError, IOError):
        return False


def epdf_read_metadata(filepath: str) -> dict:
    """Read .epdf header metadata without decrypting the payload.

    Returns a dict with keys: cipher, kdf, kdf_params, salt, nonce,
    original_filename, created, version.
    """
    try:
        with open(filepath, "rb") as f:
            magic = f.read(8)
            if magic != EPDF_MAGIC:
                raise EPDFFormatError("Not a valid .epdf file")

            meta_len_bytes = f.read(4)
            if len(meta_len_bytes) < 4:
                raise EPDFFormatError("Truncated .epdf header")

            meta_len = struct.unpack("<I", meta_len_bytes)[0]
            if meta_len > 1_000_000:  # sanity check: 1 MB max metadata
                raise EPDFFormatError("Metadata too large")

            meta_bytes = f.read(meta_len)
            if len(meta_bytes) < meta_len:
                raise EPDFFormatError("Truncated metadata")

            return json.loads(meta_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EPDFFormatError("Corrupted .epdf metadata") from exc


def epdf_encrypt(input_path: str, output_path: str, password: str,
                 cipher: str = "chacha20-poly1305", kdf: str = "argon2id",
                 kdf_params: dict | None = None) -> dict:
    """Encrypt a PDF file into the .epdf format.

    Args:
        input_path:  Path to the source PDF file.
        output_path: Path for the output .epdf file.
        password:    Encryption password.
        cipher:      One of: chacha20-poly1305, aes-256-gcm, camellia-256-cbc
        kdf:         One of: argon2id, argon2d
        kdf_params:  Optional dict overriding time_cost, memory_cost, parallelism.

    Returns:
        dict with keys: input_size, output_size, cipher, kdf
    """
    if cipher not in _ENCRYPTORS:
        raise EPDFError(f"Unsupported cipher: {cipher}")
    if kdf not in KDFS:
        raise EPDFError(f"Unsupported KDF: {kdf}")
    if not password:
        raise EPDFError("Password cannot be empty")

    file_size = os.path.getsize(input_path)
    if file_size > MAX_FILE_SIZE:
        raise EPDFFileTooLargeError(
            f"File too large: {file_size / (1024**3):.1f} GB (max 2 GB)")

    # Read source PDF
    with open(input_path, "rb") as f:
        pdf_bytes = f.read()

    # Derive key
    salt = os.urandom(16)
    params = {**DEFAULT_KDF_PARAMS, **(kdf_params or {})}
    key = _derive_key(password, salt, kdf, params)

    # Generate nonce/IV up front so it can be recorded in the (authenticated)
    # header before encryption.
    nonce = os.urandom(_NONCE_LEN[cipher])

    # Build metadata + header FIRST, then bind the header as AAD so the
    # cipher, KDF params, salt, and nonce cannot be tampered with or
    # downgraded without failing authentication.
    metadata = {
        "version":           EPDF_VERSION,
        "cipher":            cipher,
        "kdf":               kdf,
        "kdf_params":        params,
        "salt":              b64encode(salt).decode(),
        "nonce":             b64encode(nonce).decode(),
        "original_filename": os.path.basename(input_path),
        "original_size":     file_size,
        "created":           datetime.now(timezone.utc).isoformat(),
    }
    meta_bytes = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    header = EPDF_MAGIC + struct.pack("<I", len(meta_bytes)) + meta_bytes

    # Encrypt with the header as Associated Data
    encrypt_fn = _ENCRYPTORS[cipher]
    encrypted = encrypt_fn(key, nonce, pdf_bytes, header)

    # Atomic write
    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".epdf", dir=out_dir)
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(header)
            f.write(encrypted)
        os.replace(tmp, output_path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    output_size = os.path.getsize(output_path)
    log.info("EPDF encrypted %s → %s (%s, %s)",
             input_path, output_path, cipher, kdf)

    return {
        "input_size":  file_size,
        "output_size": output_size,
        "cipher":      cipher,
        "kdf":         kdf,
    }


def epdf_decrypt(input_path: str, output_path: str, password: str) -> dict:
    """Decrypt an .epdf file back to the original PDF.

    Args:
        input_path:  Path to the .epdf file.
        output_path: Path for the decrypted PDF output.
        password:    Decryption password.

    Returns:
        dict with keys: input_size, output_size, cipher, kdf, original_filename
    """
    if not password:
        raise EPDFError("Password cannot be empty")

    metadata = epdf_read_metadata(input_path)

    cipher = metadata.get("cipher")
    kdf = metadata.get("kdf", "argon2id")
    try:
        version = int(metadata.get("version", 1))
    except (TypeError, ValueError) as exc:
        raise EPDFFormatError(f"Invalid version in .epdf metadata: {exc}") from exc

    if cipher not in _DECRYPTORS:
        raise EPDFFormatError(f"Unsupported cipher in file: {cipher}")

    # Read encrypted payload and reconstruct the exact header bytes (needed
    # as Associated Data for v2 files).
    with open(input_path, "rb") as f:
        f.seek(8)  # skip magic
        meta_len = struct.unpack("<I", f.read(4))[0]
        f.seek(0)
        header = f.read(12 + meta_len)   # magic + len + metadata, verbatim
        encrypted = f.read()

    if not encrypted:
        raise EPDFFormatError("Empty encrypted payload")

    # v1 files were written without header authentication.
    aad = header if version >= 2 else None

    # Derive key
    try:
        salt = b64decode(metadata["salt"])
        nonce = b64decode(metadata["nonce"])
    except (KeyError, ValueError) as exc:
        # ValueError also catches binascii.Error (its subclass) from a
        # non-base64 value; KeyError from a missing salt/nonce field.
        raise EPDFFormatError(f"Invalid salt/nonce in .epdf metadata: {exc}") from exc
    kdf_params = metadata.get("kdf_params", DEFAULT_KDF_PARAMS)
    key = _derive_key(password, salt, kdf, kdf_params)

    # Decrypt
    decrypt_fn = _DECRYPTORS[cipher]
    pdf_bytes = decrypt_fn(key, nonce, encrypted, aad)

    # Verify it looks like a PDF
    if not pdf_bytes[:5] == b"%PDF-":
        raise EPDFDecryptionError(
            "Decrypted data does not appear to be a valid PDF")

    # Atomic write
    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(pdf_bytes)
        os.replace(tmp, output_path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    input_size = os.path.getsize(input_path)
    output_size = os.path.getsize(output_path)
    log.info("EPDF decrypted %s → %s (%s, %s)",
             input_path, output_path, cipher, kdf)

    return {
        "input_size":      input_size,
        "output_size":     output_size,
        "cipher":          cipher,
        "kdf":             kdf,
        "original_filename": metadata.get("original_filename", ""),
    }
