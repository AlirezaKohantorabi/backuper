# tests/test_packager.py

| Name | Proves |
|---|---|
| `_entry` | Helper: an in-memory entry. |
| `test_aes_zip_roundtrip` | Every member is AES-encrypted, decrypts to the original data, and a wrong password fails. |
| `test_plain_zip_and_skip_unreadable` | A locked file is skipped with a warning (or fails the zip when skipping is off) and no partial zip is left behind. |
| `test_split_parts_concatenate_to_original` | Parts are named `.001`, `.002`…, have the right sizes, are seekable, and joined give back the exact file. |
| `test_incompressible_big_files_are_stored_not_deflated` | Random (already compressed) data is stored as-is; compressible data is deflated. |
| `test_hashes_are_taken_while_zipping_and_checked_after_unzipping` | SHA-256 per file while zipping; `verify_zip` passes for the real zip and catches a changed file, a missing file, a wrong password and one flipped bit on disk; `.sha256` text round-trips. |
| `test_short_read_fails_instead_of_making_a_truncated_zip` | A source that "ends" early (like a 0-credit SMB read) fails the zip instead of producing a valid-looking truncated one. |
