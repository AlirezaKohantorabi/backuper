# Changelog

What changed in each version of the kit, with the date. Git tags `vX.Y` point to each version.
The tools' own `__version__` (in `zk_backup.py`) and `tool_version` in `last_run.json` match these numbers.

---

## [2.1] — 2026-10-31

First version in its own repository.

- **`zk_backup.py` 2.1:** read-only backup of users, attendance log and face templates into checksummed
  snapshots, the ever-growing `attendance_master.csv`, and `last_run.json` for other programs.
- **`zk_restore.py`:** writes a snapshot onto a device, for a person only: dry run by default, typed serial
  confirmation, refuses the device the snapshot came from.
- **`install.sh`:** nightly systemd timer on a Linux host.
- **Proven on hardware (2026-10-31):** full backup of the production VF600 (237 users, 234 faces, 59 s), full
  restore onto the spare, and a restored user recognised by face. 25 regression scenarios in `tests/` pass.
- **Used by** the Backuper service from its version 1.11.0 (source type `zkteco`), which runs `zk_backup.py`
  as a separate program as the README's *Integration guide* describes.
