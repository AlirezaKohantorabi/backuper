# tests/test_cloud.py

The cloud copy ([destinations/cloud.py](../backuper/destinations/cloud.md)), end to end with the **real rclone**
against a local folder standing in for Google Drive (skipped if rclone isn't installed).

| Name | Checks |
|---|---|
| `HAVE_RCLONE` / `needs_rclone` | Skip marker when rclone is missing. |
| `fake_type` | Registers the fake source type from the engine tests. |
| `_setup` | Archive + "local" cloud folder settings. |
| `test_authorize_command_matches_rclone` | The sign-in command is exactly what `rclone config` prints for scope `drive.file`. |
| `test_run_copies_its_zips_to_the_cloud` | A run uploads its zips (byte-identical), not those of a source with *Send to cloud* off, never a plain folder copy; the report has the ☁ line. |
| `test_missing_cloud_files_are_caught_up_and_old_days_removed` | An older day missing in the cloud is uploaded, days beyond "keep" are removed, other folders untouched; a second sync has nothing to do. |
| `test_failed_upload_makes_the_source_partial_and_is_retried` | A copy that doesn't match is deleted, the source is 🟠 partial, the next sync uploads it. |
| `test_cloud_is_off_by_default` | Nothing is uploaded until `cloud_type` is set. |
| `test_cloud_test_command` | `backuper cloud test` uploads, checks and removes a probe file. |
