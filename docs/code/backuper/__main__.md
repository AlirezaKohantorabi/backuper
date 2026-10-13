# backuper/\_\_main\_\_.py

**Purpose:** lets the program run as `python -m backuper …` — it just calls `main()` from
[cli.py](cli.md). The installed `backuper` command (defined in `pyproject.toml` →
`[project.scripts]`) points to the same function.

No functions of its own.
