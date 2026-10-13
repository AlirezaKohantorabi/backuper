# tests/conftest.py

Shared pytest fixtures.

| Name | What it provides |
|---|---|
| `cfg` | An `AppConfig` pointing at a temporary data/staging folder, fresh `SECRET_KEY`, Tehran timezone, owner id 42. |
| `store` | A fresh `Store` (SQLite) in that folder, closed after the test. |
