"""Symmetric encryption for secrets stored in the database."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:"


class SecretBox:
    def __init__(self, key: str):
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as e:
            raise ValueError("SECRET_KEY is not a valid Fernet key (run `backuper gen-key`)") from e

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, plain: str) -> str:
        if not plain:
            return ""
        return PREFIX + self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        if not value.startswith(PREFIX):
            # Tolerate plaintext (e.g. hand-edited rows); it gets encrypted on next save.
            return value
        try:
            return self._fernet.decrypt(value[len(PREFIX):].encode()).decode()
        except InvalidToken as e:
            raise ValueError("Stored secret can't be decrypted - was SECRET_KEY changed?") from e
