# backuper/crypto.py

**Purpose:** encrypts secrets (passwords, API tokens, the zip password, secret source fields) before
they are written to SQLite, and decrypts them when read. Uses `cryptography`'s **Fernet**
(AES-128-CBC + HMAC-SHA256, with a timestamp) keyed by `SECRET_KEY` from `.env`.

**Used by:** [db.py](db.md) only (the `Store` encrypts on write and decrypts on read), and
[cli.py](cli.md) `gen-key`.

| Name | What it does |
|---|---|
| `PREFIX` | `"enc:"` — marks an encrypted value, so plaintext and encrypted values can be told apart. |
| `SecretBox` | Wraps one Fernet key. |
| `SecretBox.__init__` | Validates the key (readable error if `SECRET_KEY` is malformed). |
| `SecretBox.generate_key` | New random key (used by `install.sh` via `backuper gen-key`). |
| `SecretBox.encrypt` | `"secret"` → `"enc:…"`; empty stays empty. |
| `SecretBox.decrypt` | Reverses `encrypt`. A value without the prefix is returned as-is (tolerates hand-edited rows; it gets encrypted on the next save). A value that can't be decrypted raises a clear "was SECRET_KEY changed?" error. |

**Why Fernet?** Authenticated, simple, no key/IV handling mistakes possible, ships with a dependency we
already need. **Threat model:** protects secrets if the database file is copied; it does not protect
against someone who has both `data/backuper.db` and `.env` (that's why `.env` is `chmod 600`).
