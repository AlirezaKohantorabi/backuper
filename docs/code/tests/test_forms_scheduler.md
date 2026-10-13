# tests/test_forms_scheduler.py

| Test | Proves |
|---|---|
| `test_form_skips_hidden_fields` | A form skips fields whose `show_if` doesn't match (e.g. "verify TLS" only for FTPS) and the prompt renders. |
| `test_scheduler_plan_groups_and_own_times` | Enabled sources of an enabled group run at the group's time (group names case-insensitive); ungrouped sources run at their own time; disabled sources/groups and manual-only sources aren't scheduled. |
| `test_legacy_global_schedule_becomes_a_group` | A v1 database with a global schedule is migrated into a group "Daily" with the ungrouped sources in it. |
| `test_one_off_runs_are_scheduled_or_marked_missed` | Future and slightly-late one-time runs get a job; one missed by hours is marked "missed"; they appear in the upcoming list. |
