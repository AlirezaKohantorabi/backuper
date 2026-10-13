# tests/test_logging.py

| Test | Proves |
|---|---|
| `test_run_log_has_tagged_steps_of_that_run_only` | A run's log file has every line tagged `[run#N Source]`, contains each step (start, collecting, zipping, archived, uploading, per-source result, the failure message, finished), contains nothing from outside the run, and the handler is removed afterwards. |
| `test_prune_run_logs` | Only the newest N run logs are kept. |
