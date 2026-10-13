# tests/test_packager.py

| Name | Proves |
|---|---|
| `_entry` | Helper: an in-memory entry. |
| `test_aes_zip_roundtrip` | Every member is AES-encrypted, decrypts to the original data, and a wrong password fails. |
| `test_plain_zip_and_skip_unreadable` | A locked file is skipped with a warning (or fails the zip when skipping is off) and no partial zip is left behind. |
| `test_split_parts_concatenate_to_original` | Parts are named `.001`, `.002`…, have the right sizes, are seekable, and joined give back the exact file. |
| `test_incompressible_big_files_are_stored_not_deflated` | Random (already compressed) data is stored as-is; compressible data is deflated. |
