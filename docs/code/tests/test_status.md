# tests/test_status.py

| Test | Proves |
|---|---|
| `test_progress_text_has_percent_size_speed_and_time_left` | Progress shows percent, size done/total, speed and time left; only the elapsed time when the size is unknown. |
| `test_status_file_while_running_and_idle` | While a run is active the status file shows it with per-source progress (plain text, no HTML) and the same file is written into the archive; when idle it says "Idle"; it isn't rewritten when nothing changed. |
