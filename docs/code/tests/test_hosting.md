# tests/test_hosting.py

| Name | Proves / provides |
|---|---|
| `FakeFS` | An in-memory `RemoteFS` (listdir/download/remove). |
| `FakeFS.__init__` | Starts with the given file names. |
| `FakeFS.__aenter__` / `FakeFS.__aexit__` / `FakeFS.listdir` / `FakeFS.download` / `FakeFS.remove` | Fake protocol operations. |
| `_ctx` | A `RunContext` for a fake cPanel source. |
| `test_default_deletes_only_the_new_backup` | Default mode deletes the new archive (and its `.json`) from the host and nothing else; the archive is zipped without re-compression. |
| `test_keep_on_host_keeps_newest_and_removes_older_archives` | Keep mode leaves the new archive, deletes older `backup-*` archives, leaves other files alone. |
| `test_replace_mode_clears_old_backups_before_the_new_one` | `replace` deletes existing archives (and their `.json`) before a new backup. |
| `test_keep_modes_and_side_files` | Mode mapping incl. old yes/no configs; DirectAdmin's `.json` side file is recognised. |
