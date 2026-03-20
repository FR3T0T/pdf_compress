"""Tests for epdf_crypto.py — enhanced PDF encryption (.epdf format)."""

import os

import pytest

from epdf_crypto import (
    EPDFError,
    EPDFPasswordError,
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
        assert meta["version"] == 1
