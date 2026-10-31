# tests/test_zkteco.py

The ZKTeco source ([sources/zkteco.py](../backuper/sources/zkteco.md)).

| Name | Checks / role |
|---|---|
| `FAKE_TOOL` / `fake_kit` | A stand-in `zk_backup.py` that behaves as `FAKE_MODE` says (ok, warning, read_error, locked, badsum, stale) and writes what the real one writes. |
| `_source` | Creates the "Paliz Device" source with an archive and a zip password. |
| `test_ok_run_archives_the_verified_snapshot` | `--keep 14`, `--faces`, the Comm Key are passed; the zip holds the snapshot, `attendance_master.csv`, `last_run.json`, `state.json`. |
| `test_tool_outcomes_become_source_statuses` | warning → ⚠️ with the device's text; read error → ❌; lock → ⏭; a snapshot that doesn't match its SHA256SUMS → ❌; an old `last_run.json` is not trusted. |
| `test_comm_key_must_be_a_number` | A non-numeric Comm Key is refused clearly. |
| `_free_udp_port` | A free port for the simulator. |
| `test_real_kit_against_its_device_simulator` | The **real, unchanged** `zk_backup.py` against the kit's simulated VF600 (`fake_zk.py faces150 vx7`), through a full bot run: ✅, 5 users / 200 punches / 4 faces, no non-whitelisted command reached the device, `data/zk` is owner-only. Skipped without `.venv-zk`. |
