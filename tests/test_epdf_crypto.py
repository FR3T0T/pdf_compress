"""Tests for epdf_crypto.py — enhanced PDF encryption (.epdf format)."""

import json
import os
import struct

import pytest

from epdf_crypto import (
    EPDF_MAGIC,
    EPDF_VERSION,
    MAX_KDF_PARAMS,
    EPDFError,
    EPDFFormatError,
    EPDFPasswordError,
    _validate_kdf_params,
    epdf_decrypt,
    epdf_encrypt,
    epdf_read_metadata,
    is_epdf,
)

# ═══════════════════════════════════════════════════════════════════
#  Encrypt / Decrypt round-trip tests
# ═══════════════════════════════════════════════════════════════════


class TestEncryptDecryptRoundtrip:
    PASSWORD = "correct-horse-battery-staple"

    @pytest.mark.integration
    def test_chacha20_poly1305(self, sample_pdf, tmp_path):
        enc = str(tmp_path / "encrypted.epdf")
        dec = str(tmp_path / "decrypted.pdf")

        info = epdf_encrypt(sample_pdf, enc, self.PASSWORD,
                            cipher="chacha20-poly1305")
        assert info["cipher"] == "chacha20-poly1305"
        assert os.path.isfile(enc)

        epdf_decrypt(enc, dec, self.PASSWORD)
        assert os.path.isfile(dec)

        # Decrypted file must start with %PDF-
        with open(dec, "rb") as f:
            assert f.read(5) == b"%PDF-"

    @pytest.mark.integration
    def test_aes_256_gcm(self, sample_pdf, tmp_path):
        enc = str(tmp_path / "encrypted.epdf")
        dec = str(tmp_path / "decrypted.pdf")

        epdf_encrypt(sample_pdf, enc, self.PASSWORD, cipher="aes-256-gcm")
        epdf_decrypt(enc, dec, self.PASSWORD)

        with open(dec, "rb") as f:
            assert f.read(5) == b"%PDF-"

    @pytest.mark.integration
    def test_camellia_256_cbc(self, sample_pdf, tmp_path):
        enc = str(tmp_path / "encrypted.epdf")
        dec = str(tmp_path / "decrypted.pdf")

        epdf_encrypt(sample_pdf, enc, self.PASSWORD,
                     cipher="camellia-256-cbc")
        epdf_decrypt(enc, dec, self.PASSWORD)

        with open(dec, "rb") as f:
            assert f.read(5) == b"%PDF-"

    @pytest.mark.integration
    def test_argon2d_kdf(self, sample_pdf, tmp_path):
        enc = str(tmp_path / "encrypted.epdf")
        dec = str(tmp_path / "decrypted.pdf")

        info = epdf_encrypt(sample_pdf, enc, self.PASSWORD, kdf="argon2d")
        assert info["kdf"] == "argon2d"

        epdf_decrypt(enc, dec, self.PASSWORD)
        with open(dec, "rb") as f:
            assert f.read(5) == b"%PDF-"

    @pytest.mark.integration
    def test_file_sizes_match(self, sample_pdf, tmp_path):
        """Decrypted file should be the same size as the original."""
        enc = str(tmp_path / "encrypted.epdf")
        dec = str(tmp_path / "decrypted.pdf")

        epdf_encrypt(sample_pdf, enc, self.PASSWORD)
        epdf_decrypt(enc, dec, self.PASSWORD)

        assert os.path.getsize(dec) == os.path.getsize(sample_pdf)


# ═══════════════════════════════════════════════════════════════════
#  Error handling tests
# ═══════════════════════════════════════════════════════════════════


class TestDecryptionErrors:
    @pytest.mark.integration
    def test_wrong_password(self, sample_pdf, tmp_path):
        enc = str(tmp_path / "encrypted.epdf")
        dec = str(tmp_path / "decrypted.pdf")

        epdf_encrypt(sample_pdf, enc, "rightpassword")

        with pytest.raises(EPDFPasswordError):
            epdf_decrypt(enc, dec, "wrongpassword")

    def test_empty_password_encrypt(self, sample_pdf, tmp_path):
        enc = str(tmp_path / "encrypted.epdf")
        with pytest.raises(EPDFError, match="empty"):
            epdf_encrypt(sample_pdf, enc, "")

    def test_empty_password_decrypt(self, sample_pdf, tmp_path):
        enc = str(tmp_path / "encrypted.epdf")
        epdf_encrypt(sample_pdf, enc, "testpass")
        with pytest.raises(EPDFError, match="empty"):
            epdf_decrypt(enc, str(tmp_path / "out.pdf"), "")


# ═══════════════════════════════════════════════════════════════════
#  Format detection and metadata tests
# ═══════════════════════════════════════════════════════════════════


class TestIsEpdf:
    @pytest.mark.integration
    def test_epdf_file(self, sample_pdf, tmp_path):
        enc = str(tmp_path / "test.epdf")
        epdf_encrypt(sample_pdf, enc, "pass123")
        assert is_epdf(enc) is True

    def test_regular_pdf(self, sample_pdf):
        assert is_epdf(sample_pdf) is False

    def test_nonexistent_file(self):
        assert is_epdf("/nonexistent/file.epdf") is False


class TestReadMetadata:
    @pytest.mark.integration
    def test_metadata_fields(self, sample_pdf, tmp_path):
        enc = str(tmp_path / "test.epdf")
        epdf_encrypt(sample_pdf, enc, "pass123",
                     cipher="aes-256-gcm", kdf="argon2id")

        meta = epdf_read_metadata(enc)
        assert meta["cipher"] == "aes-256-gcm"
        assert meta["kdf"] == "argon2id"
        assert "salt" in meta
        assert "nonce" in meta
        assert meta["version"] == EPDF_VERSION


# ═══════════════════════════════════════════════════════════════════
#  KDF parameter validation (denial-of-service hardening)
# ═══════════════════════════════════════════════════════════════════


class TestValidateKdfParams:
    def test_defaults_pass(self):
        clean = _validate_kdf_params({})
        assert clean["memory_cost"] == 65536
        assert clean["time_cost"] == 3
        assert clean["parallelism"] == 4

    def test_rejects_absurd_memory_cost(self):
        with pytest.raises(EPDFFormatError, match="memory_cost"):
            _validate_kdf_params({"memory_cost": MAX_KDF_PARAMS["memory_cost"] + 1})

    def test_rejects_absurd_time_cost(self):
        with pytest.raises(EPDFFormatError, match="time_cost"):
            _validate_kdf_params({"time_cost": 10_000})

    def test_rejects_absurd_parallelism(self):
        with pytest.raises(EPDFFormatError, match="parallelism"):
            _validate_kdf_params({"parallelism": 1024})

    def test_rejects_non_integer(self):
        with pytest.raises(EPDFFormatError, match="integer"):
            _validate_kdf_params({"memory_cost": "99999999"})

    def test_rejects_bool(self):
        # bool is an int subclass — must not slip through.
        with pytest.raises(EPDFFormatError, match="integer"):
            _validate_kdf_params({"time_cost": True})

    def test_rejects_below_minimum(self):
        with pytest.raises(EPDFFormatError):
            _validate_kdf_params({"memory_cost": 0})


class TestDecryptRejectsHostileKdf:
    @pytest.mark.integration
    def test_tampered_memory_cost_refused_before_derivation(self, sample_pdf, tmp_path):
        """A .epdf whose header demands an absurd Argon2 memory_cost must be
        refused during key derivation, not allowed to OOM the machine."""
        enc = str(tmp_path / "hostile.epdf")
        epdf_encrypt(sample_pdf, enc, "pw")

        # Rewrite the header, demanding 64 GiB of memory (in KiB).
        with open(enc, "rb") as f:
            data = f.read()
        meta_len = struct.unpack("<I", data[8:12])[0]
        meta = json.loads(data[12:12 + meta_len])
        payload = data[12 + meta_len:]
        meta["kdf_params"]["memory_cost"] = 64 * 1024 * 1024
        new_meta = json.dumps(meta, separators=(",", ":")).encode("utf-8")
        with open(enc, "wb") as f:
            f.write(EPDF_MAGIC + struct.pack("<I", len(new_meta)) + new_meta + payload)

        with pytest.raises(EPDFError):  # EPDFFormatError is an EPDFError subclass
            epdf_decrypt(enc, str(tmp_path / "out.pdf"), "pw")
