# tests/test_control.py

Restart requests ([control.py](../backuper/control.md)).

| Test | Checks |
|---|---|
| `test_restart_now_when_idle` | Idle → restarts at once, clears the request, leaves a one-time notice for the chat that asked. |
| `test_restart_waits_for_running_backups` | A run in progress → waits (and marks the request `waiting`, which `backuper restart` reports), restarts when it ends. |
| `test_restart_now_does_not_wait` | `now` restarts even while a run is in progress. |
