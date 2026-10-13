# tests/test_db.py

| Test | Proves |
|---|---|
| `test_credentials_and_secrets_are_encrypted_at_rest` | Passwords, secret source fields and the zip password never appear in clear text in the database file, and read back correctly. |
| `test_settings_defaults_and_credential_rename` | Unset settings return their defaults; renaming a credential updates the sources and the archive setting that use it; saving without a password keeps the old one. |
