# tests/test_engine.py

End-to-end runs with a fake source type and a fake Telegram sender, against a real (temporary) archive.

| Name | Role / proves |
|---|---|
| `FakeSource` | A source type whose config says what to produce (sizes, failure, hold, plain copy). |
| `FakeSource.collect` / `FakeSource.after_success` | Produce artifacts / record which artifacts reached retention. |
| `FakeSender` | Records uploads and messages; can fail chosen files. |
| `FakeSender.__init__` / `FakeSender.topic_title` / `FakeSender.upload_file` / `FakeSender.send_text` / `FakeSender.notify_admins` | Fake sender methods. |
| `SlowSender` / `SlowSender.__init__` / `SlowSender.upload_file` | Uploads take time; records the maximum number of simultaneous uploads. |
| `fake_type` | Fixture registering `FakeSource` as type `fake`. |
| `test_full_run` | Smallest-first upload across sources, AES zips in the Jalali folder, archive retention keeps N date folders and ignores other folders, statuses, retention only for stored data, staging cleaned. |
| `test_upload_failure_keeps_archive_and_skips_nothing` | Telegram failure → 🟠 partial, retention still runs (archived), Resend works later. |
| `test_no_archive_and_no_telegram_keeps_zip_locally` | Stored nowhere → ❌, no retention, zip kept in `staging/unsent`. |
| `test_archive_only_source_streams_into_archive_and_keeps_n` | Archive-only zips never touch staging, aren't uploaded, and "keep N copies" works. |
| `test_archive_only_without_archive_fails_clearly` | Archive-only without an archive is a clear error. |
| `test_same_minute_rerun_never_deletes_the_current_copy` | Re-runs in the same minute (same file name) never make retention delete the current copy (real bug found by a test). |
| `test_collect_skips_backup_that_was_already_stored` | An unchanged newest file is ⏭ skipped; a new one is sent. |
| `test_collect_retries_upload_next_run_if_telegram_failed` | A file that reached the archive but not Telegram is sent again on the next run. |
| `test_overlapping_runs_never_upload_at_the_same_time` | Two runs at once: never two uploads at the same time, each run's files stay together and smallest-first. |
| `test_archive_only_source_does_not_delay_uploads` | A slow archive-only source doesn't hold back the run's uploads. |
| `test_source_already_running_is_skipped` | A source already running in another run is ⏭ in the second run. |
| `test_upload_waits_for_upload_from_time` | Without a time no waiting; with one, uploads wait until then. |
| `test_archive_only_mode_without_telegram` | No Telegram configured → zips go straight to the archive and count as ✅. |
| `test_copy_format_makes_a_plain_dated_folder` | "Store as: copy" → plain files (not encrypted), original dates kept, not uploaded, no `.partial` left. |
| `test_archive_write_is_retried` | A failed write into the archive (like the 2026-10-14 SMB credit errors) is retried; the run ends ✅ with a note. |
| `test_folder_copy_resumes_after_a_failure` | A folder copy that breaks half-way is retried and only copies the missing files. |
| `test_folder_copy_that_keeps_failing_leaves_nothing_behind` | After the last attempt fails, no `.partial` folder is left. |
| `test_unreadable_file_is_skipped_not_retried` | A locked / access-denied file is skipped with a warning; the rest is copied. |
| `test_archived_zip_is_unzipped_and_compared` | An archived zip is verified; nothing but the zip is left in the archive. |
| `test_zip_that_fails_the_check_is_deleted_and_made_again` | A zip whose check fails is deleted and made again; the run still ends ✅. |
| `test_run_waits_for_an_unreachable_archive` | An archive that's down at the start (power cut) is waited for, then the run works. |
| `test_archive_that_stays_down_fails_the_run_without_backing_up` | If it stays down, every source fails clearly and no backup is made on the servers. |
| `test_deleting_a_zip_that_is_already_gone` | Deleting it again returns "already gone" instead of crashing (a real bug this test found). |
| `test_service_start_leaves_a_running_shell_run_alone` | A service restart keeps a shell run's staging folder and "running" status (its folder is locked), cleans dead runs' folders. |
| `test_startup_removes_leftovers_of_interrupted_runs` | Old `.uploading`/`.partial` items are removed at start; finished zips and very recent leftovers are kept. |
