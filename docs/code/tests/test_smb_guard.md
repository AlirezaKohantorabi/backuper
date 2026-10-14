# tests/test_smb_guard.py

SMB robustness ([transport/smb.py](../backuper/transport/smb.md)) — the 2026-10-14 credit failures.

| Test | Checks |
|---|---|
| `test_credit_guard_is_installed` | The `smbprotocol` patch is active (fails if a library upgrade renames what we patch). |
| `_Conn` / `_Conn.__init__` | A minimal stand-in connection (credit window, max sizes) for the chunk-size test. |
| `test_chunk_size_waits_for_credits_instead_of_returning_zero` | With 0 credits a read waits for credits instead of becoming a 0-byte read (= fake end-of-file). |
| `test_each_client_has_its_own_connections` | Two `SMB` objects never share a connection cache; `with` closes it. |
| `test_check_complete` | Short reads raise `IncompleteCopy`; complete ones and size-less streams pass. |
