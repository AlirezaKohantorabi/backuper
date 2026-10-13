# backuper/\_\_init\_\_.py

**Purpose:** marks `backuper` as a Python package and holds the version number.

| Name | What it is |
|---|---|
| `__version__` | The application version (e.g. `"1.4.0"`). Shown by `backuper --version` and logged at startup. Must match `version` in `pyproject.toml` and the newest entry in `CHANGELOG.md`. |

**Connections:** read by [cli.md](cli.md) (`--version`, startup log).
