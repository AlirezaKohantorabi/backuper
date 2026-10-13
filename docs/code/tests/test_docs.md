# tests/test_docs.py

Keeps the documentation honest.

| Name | Proves |
|---|---|
| `ROOT` / `CODE_DIRS` | Repository root and the folders whose `.py` files must be documented. |
| `_code_files` | Every `.py` file under `backuper/` and `tests/`. |
| `_doc` | Path of the document for a code file (`docs/code/<path>.md`). |
| `_names` | Every function, class and method defined in a file (via `ast`). |
| `test_every_code_file_has_a_document` | `docs/code/<path>.md` exists for each code file. |
| `test_every_function_and_class_is_documented` | Each name defined in a code file appears in its document. |
| `test_changelog_mentions_current_version` | `CHANGELOG.md` has an entry for the version in `backuper/__init__.py`. |
