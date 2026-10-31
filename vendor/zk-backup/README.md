# ZKTeco VF600 Backup & Restore

Oct 31, 2026 · @alireza

## Overview

This project backs up Harmonic Center's ZKTeco VF600 face-recognition attendance terminal over the network every night, and restores it onto another VF600 when needed. Both directions were proven on real hardware on 2026-10-31: a full backup of the production unit, a full restore onto the spare, and the spare recognizing a restored user's face.

It is three small Python tools built on the open-source `pyzk` library (pinned to 0.9):

- **`zk_backup.py`** reads users, attendance logs and face templates and stores them as verified, checksummed snapshots. It can never write to the device: every non-read command is blocked before a packet is built.
- **`zk_restore.py`** writes users and face templates from a snapshot onto a device. It is a deliberate write tool with interlocks: dry run by default, a typed serial-number confirmation, and refusal to write to the device a snapshot came from.
- **`install.sh`** installs the backup as a nightly systemd timer on a Linux host.

| Test | Device | Result |
| --- | --- | --- |
| Full backup | Production VF600, serial 6650154800185 | 237 users, 12,703 punches, 234 face templates; every count matched the device's own counters; 59 s |
| Template integrity | Backup snapshot | All 234 templates exactly 30,912 bytes, no duplicates, no empty templates, checksums OK |
| Single-user restore | Spare VF600, serial 6650154800076 | User record and face written; face identical to the backup except the device's own slot stamp |
| Full restore | Spare VF600 | 237 users and 234 faces written and verified by reading them back |
| Recognition | Spare VF600 | A restored user was recognized by face; enrollment photos were not needed |

## The device and the problem

The VF600's USB export does not recognize USB drives, so the only reliable way to get its data out is its network port, TCP/UDP 4370, the same one attendance software uses. A network backup is also schedulable and verifiable, and it can include face templates.

| Property | Production unit | Spare unit |
| --- | --- | --- |
| Model | ZKTeco VF600 (Paliz-branded) | ZKTeco VF600 |
| Serial | 6650154800185 | 6650154800076 |
| Firmware | Ver 6.60 Aug 20 2015 | Ver 6.60 Aug 20 2015 |
| Face algorithm | ZFace VX7.0 (reported as `7`) | ZFace VX7.0 |
| IP | 192.168.10.113 | 192.168.10.39 |
| Transport | TCP 4370 | TCP 4370 |
| Data on 2026-10-31 | 237 users, 12,703 punches, 234 faces | Before restore: 4 users, 3 faces, 393 punches |

What the device stores and what the backup covers:

- **Users**: ID (PIN), name, privilege, password, card number, group. Backed up and restorable.
- **Attendance log**: one record per punch. Backed up. The protocol has no command to write punches back, so logs are kept for payroll in `attendance_master.csv`, not restored to devices.
- **Face templates**: the measurements the device matches against. Backed up and restorable onto another VX7.0 device.
- **Enrollment photos**: JPEGs taken at enrollment. They exist on the device (a past USB export got them), but this firmware rejects the network photo download (reply 65535, "unknown command"). They are not needed for recognition: the spare recognized a user restored from templates only.

## How the ZK protocol works

The device speaks ZKTeco's binary protocol: small request packets, each answered by a reply that echoes the request's reply ID. Bulk data moves through a transfer buffer in chunks. Everything below was observed on the real units or read from the pyzk 0.9 source.

### Packets

Every packet starts with an 8-byte little-endian header, followed by the payload. Over TCP the packet is prefixed with another 8 bytes: two magic words `0x5050`, `0x7D82` and the packet length (u32).

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 2 | Command code |
| 2 | 2 | Checksum |
| 4 | 2 | Session ID (assigned by the device at connect) |
| 6 | 2 | Reply ID (client increments it per request; the device echoes it) |
| 8 | n | Payload |

A session opens with `CMD_CONNECT` (1000). If the device has a Comm Key set, it answers "unauthorized" and the client sends `CMD_AUTH` (1102) with the key scrambled using the session ID. That scrambling is obfuscation, not encryption. `CMD_EXIT` (1001) closes the session.

### Commands this project uses

| Code | Name | Used by | Effect |
| --- | --- | --- | --- |
| 1000 / 1001 / 1102 | Connect / exit / auth | both | Session |
| 11 | Read option | both | Serial, model, firmware, face version |
| 50 | Read sizes | both | Counters: users, punches, faces, capacities |
| 201 | Read clock | backup | Device time, for drift detection |
| 1503 | Prepare buffer | both | Device snapshots a whole table into a transfer buffer |
| 1504 | Read buffer chunk | both | Copy a chunk of that snapshot |
| 1502 | Free buffer | both | Release the transfer buffer (deletes no records) |
| 150 | Read user face | both | One user's face template (slot 50) |
| 88 | Read user template | backup | Fallback face read |
| 10010 | Download user photo | backup | Enrollment JPEG; rejected by this firmware |
| 8 | Write user | restore | Create or update one user record |
| 1500 / 1501 | Prepare data / data | restore | Upload a buffer to the device |
| 151 | Write user face | restore | Store the uploaded buffer as a user's face |
| 1013 | Refresh data | restore | Device reloads its tables after writes |
| 1003 / 1002 | Disable / enable device | restore | Freeze the terminal during writes |

Codes 150 and 151 come from a command table extracted from ZKTeco firmware 6.60 (`CMD_USERFACE_RRQ`, `CMD_USERFACE_WRQ`). The openzk project recovered 150 independently from the official SDK.

### Reading a table

1. Client sends 1503 with an inner command naming the table: 9 = users, 13 = attendance, 7 = fingerprint templates.
2. The device copies the table into its transfer buffer and replies with the total size.
3. Client sends 1504 (offset, size) per chunk. The device answers `PREPARE_DATA` (size), then `DATA` packets, then `ACK_OK`.
4. Client sends 1502 to free the buffer.

Because the device reads from its own snapshot, the terminal never needs to be frozen for a backup. The catch: pyzk reads the record counter before the snapshot, so a punch landing in between makes it decode with the wrong record size (see Findings).

### Uploading

The client sends `PREPARE_DATA` (1500) with the total size, then the data in 1,024-byte `DATA` (1501) packets, each acknowledged. Then a command that consumes the buffer, such as 151, tells the device what the data is. A 30,912-byte face takes about 34 round trips this way, which is why a full restore takes several minutes.

### Record formats

User record, 72 bytes (`<HB8s24s4sx7sx24s`):

| Offset | Size | Field | Notes |
| --- | --- | --- | --- |
| 0 | 2 | uid | Internal slot. Assigned by the device on new users |
| 2 | 1 | privilege | 0 = user, 14 = admin |
| 3 | 8 | password | PIN, text |
| 11 | 24 | name | Text; empty is shown by pyzk as `NN-<user_id>` |
| 35 | 4 | card | u32 |
| 40 | 7 | group | Text |
| 48 | 24 | user\_id | The PIN people type; the real identity |

Attendance record, 16 bytes: user\_id (u32), timestamp (4 bytes), status (u8), punch (u8), 2 reserved, workcode (u32). The timestamp packs local time as seconds in a calendar with 31-day months:

```latex
t = \big((y \bmod 100)\cdot 12\cdot 31 + (m-1)\cdot 31 + (d-1)\big)\cdot 86400 + (h\cdot 60 + \mathit{min})\cdot 60 + s
```

### Face templates (ZFace VX7.0)

- **Read (150):** payload = user\_id padded to 24 bytes, one zero byte, slot `50`, two zero bytes.
- **Size:** 30,912 bytes per user = 12 blocks of 2,576 bytes, the sub-templates captured at enrollment.
- **Slot stamp:** bytes 2–3 of every block hold the user's internal slot (u16, little-endian). Saving on another device rewrites them: user 7547 was slot 220 (`dc`) on production and slot 5 (`05`) on the spare. The other 30,900 bytes were identical. The high byte being part of the field is inferred; both values seen were below 256.
- **Write (151):** upload the template as a buffer, then send 151 with user\_id padded to 24, `0`, `50`, size (u16). Other layouts were rejected with reply 2001.

## Files and architecture

Data flows one way on the nightly path, from the production device into the backup folder. Only the restore tool, run by a person, writes to a device.

```
Production VF600 ──reads──▶ zk_backup.py ────saves────▶ Backup folder
192.168.10.113:4370         nightly, read-only guard     snapshots · master log · last_run.json
                                                              │                    │
                                                     snapshot │                    │ status
                            ┌─────────────────────────────────┘                    ▼
                            ▼                                                Backuper bot
Spare or new VF600 ◀─writes── zk_restore.py                                  reads last_run.json,
same model, ZFace VX7.0       run by a person only                           copies snapshot off-site
```
*Data flow: backups run nightly and never write; only zk_restore.py, run by a person, writes to a device.*

The bot never talks to a device: it reads `last_run.json` and copies the snapshot the backup produced.

| File | Role | Writes to a device? |
| --- | --- | --- |
| `zk_backup.py` | Nightly backup of users, punches and faces into verified snapshots | Never; guarded and self-tested |
| `zk_restore.py` | Restores users and faces from a snapshot | Yes, behind interlocks; run by a person |
| `install.sh` | Installs the nightly systemd service and timer | No |
| `tests/run_tests.sh` | 25 regression scenarios | No; uses simulated devices |
| `tests/fake_zk.py` | Simulated device for backup tests | n/a |
| `tests/fake_spare.py` | Simulated writable device for restore tests | n/a |
| `README.md` | This document | n/a |

## zk\_backup.py

`zk_backup.py` (v2.1) turns one read of the device into a verified, checksummed snapshot plus an ever-growing archive of punches, and it cannot send a write command to the device.

### What one run does

1. Arms the read-only guard and self-tests it offline: 8 destructive calls must all be blocked, or it exits with code 4.
2. Takes the run lock (`.lock` in the output folder; treated as stale after 2 hours).
3. Picks the transport: TCP if port 4370 accepts a connection, otherwise UDP (`--udp` forces UDP).
4. Connects, reads model, serial, firmware, face version and the device clock. Drift over 120 s is a warning.
5. Reads users, attendance and fingerprint templates, each verified against the device's counters.
6. Records the device counters.
7. With `--faces` / `--photos`, reads face templates and enrollment photos in strict reply mode.
8. Disconnects. The device was never frozen.
9. Compares with the last good run (`state.json`): device swapped, punches dropped, users removed or added.
10. Writes the snapshot atomically, merges new punches into the master archive, saves state, prunes old snapshots, writes `last_run.json`.

### The read-only guard

Every pyzk function ends in one private method, `ZK.__send_command` (name-mangled to `_ZK__send_command`), which builds and sends a packet. The script replaces it with a wrapper that checks a whitelist first:

```python
def guarded(self, command, command_string=b"", response_size=8):
    if command not in READ_ONLY_COMMANDS:          # read commands only
        raise WriteBlocked(...)                     # before any packet is built
    if command == 1503 and inner_table not in BUFFERED_TABLES:
        raise WriteBlocked(...)                     # no smuggling through the buffer path
    ...
    return original(self, command, command_string, response_size)
```

- The whitelist is `READ_ONLY_COMMANDS` (codes 1000, 1102, 1001, 1100, 11, 50, 201, 1503, 1504, 1502, 88, 150, 10010). Clear data (14), clear log (15), delete user (18), set time (202), restart (1004), disable (1003) and everything else raise `WriteBlocked`.
- `self_test_guard()` calls `clear_attendance`, `clear_data`, `disable_device`, `delete_user`, `set_user`, `set_time`, `restart` and `poweroff` on a never-contacted object. All must raise `WriteBlocked`.
- If pyzk's internals change and `_ZK__send_command` is missing, the script refuses to run rather than run unguarded. pyzk is pinned to 0.9 for this reason.
- The wrapper also records the last reply ID sent (`_last_sent_id`) and, in strict mode, checks that each reply echoes the expected ID.

### Verified reads: `consistent_read()`

For each table it reads the device counter, reads the table, reads the counter again, and accepts only when before = after = rows decoded. Otherwise it discards the attempt and retries (default 3 attempts, 5 s then 10 s apart). This catches a punch landing mid-read, which in plain pyzk produces silently corrupted records (Findings). If no attempt is consistent, it exits with code 2 and saves nothing from that run.

### Faces and photos

- `raw_read()` sends one whitelisted read and collects the bulk reply using pyzk's own receive code. It checks the byte count against the size the device announced.
- `read_faces()` tries command 150 on up to the first 5 users. If nothing comes back, it tries command 88 the same way. Once one works, it reads every user. Each template is stored with its SHA-256.
- `read_photos()` requests `<user_id>.jpg` with command 10010 and keeps only real JPEGs. This firmware answers 65535, so photos stay empty.
- These reads run last, after users and logs are verified, in strict reply mode. A timeout or stale reply triggers `drain()`: it discards queued packets and moves the reply counter past every ID already used. A misbehaving firmware here can never affect the verified tables.
- The face count is compared with the device's face counter. A mismatch is a warning, not a failure.

### Output folder

```
<out>/
├── attendance_master.csv      every punch ever seen, deduplicated, sorted (never shrinks)
├── attendance_master.csv.prev previous version, kept on each merge
├── state.json                 last good run: serial, counts, user IDs
├── last_run.json              status of the latest run, for bots
├── backup.log                 rotating log, 1 MB x 5
└── snapshots/
    └── 2026-10-31_151651/     one folder per run; -2, -3 suffix if two runs share a second
        ├── info.json          device, firmware, counters, clock drift, timing
        ├── users.csv          uid, user_id, name, privilege, password, group_id, card
        ├── attendance.csv     user_id, timestamp, status, punch, uid
        ├── faces.json         with --faces: per user, base64 template + sha256
        ├── templates.json     only if the device holds fingerprints
        ├── photos/            with --photos, if the firmware serves them
        └── SHA256SUMS         check with: sha256sum -c SHA256SUMS
```

- A snapshot is written into `.tmp-<stamp>`, every file fsynced and checksummed, then renamed into place. A snapshot folder therefore exists complete or not at all.
- The master archive is identified by `(user_id, timestamp, status, punch)`. If the existing file has unexpected columns, the script refuses to touch it.
- Files are created owner-only (`umask 077`) because they hold PINs, card numbers and biometric templates.
- Retention (`--keep`, default 90) deletes old snapshot folders only. Their punches stay in the master archive.

### Options

| Option | Default | Meaning |
| --- | --- | --- |
| `ip` | required | Device IP |
| `--out` | `./zk-backups` | Backup folder, one per device |
| `--faces` | off | Read face templates. Use it for the VF600 |
| `--photos` | off | Read enrollment photos. Not served by this firmware |
| `--password` | 0 | Device Comm Key |
| `--udp` | auto | Force UDP |
| `--encoding` | UTF-8 | Name encoding; try `cp1256` if names look broken |
| `--timeout` | 20 | Seconds per packet |
| `--retries` | 3 | Attempts per table |
| `--keep` | 90 | Snapshots kept; 0 keeps all |

### Exit codes

| Code | Status in `last_run.json` | Meaning |
| --- | --- | --- |
| 0 | `ok` or `warning` | Backup saved. `warning` = check the warnings list |
| 1 | `read_error` | Could not connect or read |
| 2 | `verify_failed` | Data never read consistently, or a different device answered |
| 3 | (no file written) | Another run holds the lock |
| 4 | `guard_error` | Safety guard could not be armed |
| 5 | `internal_error` | Unexpected exception; traceback in the log |

## zk\_restore.py

`zk_restore.py` writes users and face templates from a snapshot onto a VF600 and proves every write by reading it back. On 2026-10-31 it restored 237 users and 234 faces onto the spare, which then recognized a restored user.

It is a deliberate write tool, so it is never scheduled and never run by a bot without a human.

### Interlocks

| Guard | What it prevents |
| --- | --- |
| Dry run by default | Accidental writes. Without `--write` it connects, reads and prints the plan only |
| `--confirm-serial` must equal the serial the device reports live | A mistyped IP hitting the wrong unit |
| Refuses the device the snapshot came from (unless `--allow-source-device`) | Overwriting production from its own backup by mistake |
| Write whitelist, self-tested at start | Clear data, clear log, delete user, delete face, restart, power off, set time, settings writes. Attendance logs are never touched |
| Snapshot `SHA256SUMS` and per-face SHA-256 checked first | Restoring corrupted data |
| Face algorithm must match the snapshot's | Templates that would be useless on the target |
| Never writes into a slot that holds another user ID | Replacing a different person |
| Device unfrozen in a `finally` block | A terminal left on "working" after an error |

The write whitelist is the read list plus 8 (write user), 1500 / 1501 (upload buffer), 151 (write face), 1013 (refresh), 1003 / 1002 (freeze / unfreeze).

### What a write run does

1. Verifies the snapshot, then keeps only the `--only-user` IDs if given.
2. Connects and reads the target's serial, firmware, face version, users and counters.
3. Plans by user ID: an ID already on the target is updated in its existing slot; a new ID gets the next free slot.
4. Checks the interlocks, then freezes the device (the protocol requires it for uploads).
5. Writes each user with command 8 in the 72-byte format. It keeps the exact privilege; pyzk's own `set_user()` would downgrade anything other than 0 or 14.
6. Refreshes, reads the users back and compares name, privilege, password, group and card. On any difference it prints the field and both values, and stops before faces.
7. Writes the first face, trying the candidate layouts in order. Each attempt is read back with command 150 and compared with the expected bytes. The first exact match is used for everyone else.
8. Writes the remaining faces with progress lines, refreshes, and verifies each one.
9. Unfreezes the device and prints the new totals.

### Why users are matched by user ID

On the real VF600, a new user written with slot 220 was stored in slot 5, the next free one. The firmware owns the internal slot; the user ID (PIN) is the real identity, and the face commands are keyed by it. So the tool updates existing IDs in place, lets new IDs take the next free slot, and does not compare slots after writing.

### Face write and the slot stamp

The expected read-back is not the backup template itself. `expected_face()` takes the backup template and writes the user's slot on the target into bytes 2–3 of every 2,576-byte block, exactly what the device does on save. The stored face must match that byte for byte.

| # | Payload of command 151 | Result on the VF600 |
| --- | --- | --- |
| 1 | Buffer, then header: user\_id\[24\], 0, 50, size (u16) | Accepted (2000), verified |
| 2 | Buffer, then header with size 0 | Rejected (2001) |
| 3 | Buffer, then header with flag byte 1 | Rejected (2001) |
| 4 | Buffer, then header with size as u32 | Rejected (2001) |
| 5 | Header and template in one packet | Rejected (2001) |

For this firmware always pass `--face-layout 1`, which skips the other attempts.

### Options

| Option | Meaning |
| --- | --- |
| `ip`, `snapshot` | Target device and snapshot folder |
| `--only-user ID` | Restore just this user; repeatable. Use it for a first test |
| `--write` | Actually write; default is a dry run |
| `--confirm-serial S` | Target's serial; must match the live device |
| `--allow-source-device` | Allow writing to the device the snapshot came from (real disaster recovery only) |
| `--face-layout N` | Use a known-good face layout (`1` on the VF600) |
| `--skip-faces` | Users only |
| `--password`, `--udp`, `--encoding`, `--timeout` | As in `zk_backup.py` |

Exit codes: 0 done (or dry run), 2 stopped by an interlock or a failed verification, 4 safety stop. An unexpected exception exits 1 with a traceback; the restore writes no `last_run.json`.

### Proven procedure

```bash
SNAP=~/zk-backup-2026-10-31/snapshots/2026-10-31_151651
# 1. dry run: target serial, what is on it, the plan
python zk_restore.py 192.168.10.39 $SNAP
# 2. one user first
python zk_restore.py 192.168.10.39 $SNAP --only-user 7547 --write --confirm-serial 6650154800076 --face-layout 1
# 3. stand in front of the device; recognized? then everyone:
python zk_restore.py 192.168.10.39 $SNAP --write --confirm-serial 6650154800076 --face-layout 1
```

A full restore uploads about 7.2 MB in 1 KB acknowledged packets and takes several minutes. The device shows "درحال کار" (working) until it finishes.

## Deployment

Run the backup nightly on an always-on Linux host in the device's network, for example a small VM on one of the ESXi hosts. `install.sh` sets it up in one command and runs a first backup immediately:

```bash
sudo ./install.sh 192.168.10.113 --faces        # add --password N if a Comm Key is set
```

| What | Where | Notes |
| --- | --- | --- |
| Script and venv | `/opt/zk-backup/` | venv with `pyzk==0.9` pinned |
| Backups | `/var/backups/zk/` | Mode 700, owned by `zkbackup` |
| Settings | `/etc/default/zk-backup` | `ZK_IP`, `ZK_OPTS`; root-only because it may hold the Comm Key |
| Service | `zk-backup.service` | `Type=oneshot`, runs as unprivileged `zkbackup` |
| Schedule | `zk-backup.timer` | Daily 02:17; `Persistent=true` runs a missed job at next boot |

The service is sandboxed: `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `NoNewPrivileges=yes`, and write access only to `/var/backups/zk`. A non-zero exit marks the unit failed, so `systemctl --failed` shows a failed backup.

Day-to-day commands:

```bash
systemctl list-timers zk-backup.timer          # next and last run
sudo systemctl start zk-backup.service         # run one now
journalctl -u zk-backup.service -n 50          # recent output
sudo cat /var/backups/zk/last_run.json         # status of the last run
sudoedit /etc/default/zk-backup                # change IP or options, no reload needed
```

To change the schedule, edit `OnCalendar=` in `/etc/systemd/system/zk-backup.timer`, then `sudo systemctl daemon-reload`. Re-running `install.sh` updates everything in place.

On Windows the same script runs under Task Scheduler:

```
schtasks /Create /TN "ZK Backup" /SC DAILY /ST 02:17 /RU SYSTEM /TR "C:\zk\venv\Scripts\python.exe C:\zk\zk_backup.py 192.168.10.113 --faces --out D:\Backups\zk"
```

On an Arch workstation (PEP 668 blocks system-wide pip), use a venv: `python -m venv ~/zk-venv && ~/zk-venv/bin/pip install pyzk==0.9`.

Keep a second copy of the backup folder elsewhere, for example in the Veeam jobs. One folder on one host is a single point of failure.

## Operations runbook

The daily check is one file: `last_run.json` should say `ok`. Anything else names its problem in `warnings` or `errors`, and the table below says what to do.

| Message (log and `last_run.json`) | Meaning | Action |
| --- | --- | --- |
| `device attendance count DROPPED X -> Y` | Something cleared punches on the device | Check whether the attendance software has "clear after download" on, or who used the menu. Earlier punches are safe in the master archive |
| `users REMOVED since last run: [...]` | User records were deleted on the device | Confirm it was intended; if not, restore those IDs from the last snapshot with `--only-user` |
| `DEVICE CHANGED: ... serial` (exit 2) | A different device answers at this IP | Replaced unit or IP conflict. Give each device its own `--out` folder |
| `device clock is +Ns off this host` | Punches are stamped with a wrong time | Correct the device clock |
| `attendance memory X/Y is over 90% full` | Device log nearly full | Archive and clear through the attendance software, after a successful backup |
| `faces: saved N ... counter says M` | Some faces were not read | Run again; if it repeats, compare `faces.json` with the device's user list |
| `users have no readable name - try --encoding cp1256` | Names decoded with the wrong encoding | Add `--encoding cp1256` to `ZK_OPTS` |
| `VERIFY FAILED ... no consistent read` (exit 2) | Punches kept landing during the read, or the link is flaky | Run outside working hours; run again |
| `READ FAILED` (exit 1) | Device off, unreachable, wrong Comm Key, or another program holding the session | Check power, ping, `--password`, and close the attendance software's connection |
| `SAFETY` (exit 4) | pyzk changed and the guard cannot be armed | Reinstall `pyzk==0.9` in the venv |
| `INTERNAL ERROR` (exit 5) | A bug in `zk_backup.py` | The traceback is in `backup.log` |

If a VF600 dies:

1. Get a replacement VF600 whose face algorithm is ZFace VX7.0. The restore dry run prints firmware and face version, and refuses a different face algorithm.
2. Give it an IP and keep it away from the attendance software until the restore is done.
3. Pick the newest snapshot and check it: `sha256sum -c SHA256SUMS` inside the folder.
4. Dry run, then one user with `--write --confirm-serial <new serial> --face-layout 1`, then the face test, then everyone. Use `--allow-source-device` only when it is the same repaired unit with the same serial.
5. Set the device clock and reconnect the attendance software. Punch history stays in `attendance_master.csv`; devices cannot take punches back.
6. Point the nightly backup at the new device with a new `--out` folder, because the old folder's `state.json` remembers the old serial.

Once a month, run a restore dry run, or a one-user restore, against the spare with the latest snapshot. It proves the backups are still restorable, not just present.

## Testing

`tests/run_tests.sh` runs 25 scenarios against two simulated devices in about 25 seconds, with no hardware. Run it after any change to either tool:

```bash
PY=~/zk-venv/bin/python ./tests/run_tests.sh      # any python with pyzk==0.9
```

The simulators speak the real UDP protocol and log every command code they receive. Any command outside the tool's whitelist is logged as `VIOLATION`, and every scenario fails if one appears.

- **`tests/fake_zk.py`** is a read-side device with 5 users (Persian and Latin names) and 200 punches. Options simulate firmware behaviour: `punch_before_buffer`, `punch_after_buffer`, `records=N`, `serial=S`, `faces150`, `faces88`, `vx7` (12 × 2,576-byte templates with the slot at bytes 2–3), `silent150`, `late150`, `photos`.
- **`tests/fake_spare.py`** is a writable device. It reproduces what the real VF600 does: `assign_uid` (new users get the next free slot), `stamp` (the slot is written into every face block on save), `accept=N` (which face layout is accepted; the real one is `0`, the tool's #1), `corrupt0` ("OK" but stores bad data), `zero_group`, `preload_pin_clash`, `serial=S`.

| Area | Scenarios |
| --- | --- |
| Backup basics | Normal run, `last_run.json` status, device unreachable |
| Consistency | Punch before the snapshot (pyzk misparse caught and retried), punch after |
| History checks | Device wiped between runs (warning, master kept), different device at the same IP (master untouched), run lock |
| Faces | Command 150, fallback 88, firmware ignores the command, late stale reply, session recovery afterwards |
| Guard | Destructive calls on a live connection, in normal and strict mode, blocked before the wire |
| Restore interlocks | Dry run sends no writes, wrong serial, source device |
| Restore data | User already in another slot, slot stamp predicted, full restore verified, no false read-back failures |
| Restore failures | No face layout verifies, device changes a field (exact diff printed) |

The simulators prove the tools' logic and safety. They cannot prove that another firmware behaves like this VF600; only a real device can, which is why the spare was used.

## Findings and gotchas

The most dangerous finding: plain pyzk can save silently corrupted attendance data, which a naive backup script would never notice. These are the behaviours discovered while building and testing, and what the code does about each.

| Finding | Effect | How the code handles it |
| --- | --- | --- |
| pyzk reads the record counter, then the device snapshots the log. A punch in between makes pyzk compute the record size from a stale count (e.g. 16 × 201 / 200) | It decodes the log with the wrong record size: 80 garbage records instead of 200, no error | `consistent_read()` requires counter before = after = rows, else retries |
| After a timeout, pyzk does not advance its reply counter, so the next request reuses the old reply ID | A late answer to the old request is accepted as the new one | `drain()` discards queued packets and skips past every used ID; strict mode checks each echo |
| The openzk client calls itself read-only, but writes `SDKBuild=1` on connect and restores it on exit | Not read-only in the strict sense | Not used; this firmware serves every read without that flag |
| The VF600 assigns the internal slot itself and ignores the slot sent for a new user | Writing user 7547 with slot 220 stored it in slot 5 | Restore matches users by user ID |
| Face templates contain the user's slot in bytes 2–3 of each 2,576-byte block; the device rewrites them on save | A restored face is never byte-identical to the backup | `expected_face()` predicts the stamp; the check stays exact |
| Enrollment photos exist on the device, but this firmware answers the photo download with 65535 | Photos are not in the network backup | Not needed: a restored user was recognized from templates only |
| pyzk shows an empty name as `NN-<user_id>` | A naive restore would write that text as the name | Restore writes an empty name back |
| pyzk's `set_user()` changes any privilege other than 0 and 14 to 0 | Roles could be lost on restore | Restore packs the 72-byte record itself |
| Snapshot folders were named to the second | Two runs in the same second crashed on rename | A `-2`, `-3` suffix; any unexpected crash is recorded as `internal_error` |
| Uploads go in 1 KB packets, each acknowledged | A full restore takes several minutes | Progress lines with a time estimate |
| The spare's clock was 282 s fast | Its punches would be stamped wrong | Backup warns on drift over 120 s |
| The VF600's USB port did not read the sticks tried | No offline export | This project; ZKTeco units generally need FAT32 on an MBR partition table |

Two practical notes for whoever runs the tools: on Arch, pip must run inside a venv (PEP 668), and shell variables such as `SNAP` exist only in the terminal where they were set.

## Integration guide for the backuper bot

Integrate by running `zk_backup.py` as a subprocess on a schedule and reading the `last_run.json` it writes. Treat the script as a black box: do not import its internals or reimplement the protocol, because its safety depends on the guard and the pinned pyzk version.

### Contract

| Item | Value |
| --- | --- |
| Command | `<venv>/bin/python zk_backup.py <ip> --out <folder> --faces [--password N]` |
| Requirements | Python 3 with `pyzk==0.9` in its own venv; TCP or UDP reach to the device on port 4370 |
| Duration | About 60 s for 237 users with faces; set a subprocess timeout of 20 minutes |
| Result | Exit code (see zk\_backup.py) and `<folder>/last_run.json` |
| One folder per device | Required. A different serial in the same folder is refused (exit 2) |
| Concurrency | A second run while one is active exits 3 and writes no `last_run.json`. Not a failure; do not alert |
| Storage | Roughly 10 MB per snapshot with faces (about 9.6 MB of base64 face data). `--keep 90` uses about 0.9 GB; lower it if space matters |

### `last_run.json`

| Field | Type | Meaning |
| --- | --- | --- |
| `status` | string | `ok`, `warning`, `read_error`, `verify_failed`, `guard_error`, `internal_error` |
| `exit_code` | int | Same as the process exit code |
| `device_ip`, `started`, `finished`, `tool_version` | string | Always present |
| `serial`, `device_name`, `firmware` | string | Success only |
| `snapshot` | string | Absolute path of the new snapshot folder. Success only |
| `users`, `punches_on_device`, `faces_saved`, `faces_on_device` | int | Counts from this run. Success only |
| `new_punches`, `master_total_punches` | int | Punches added to the master archive, and its total. Success only |
| `clock_drift_seconds` | int | Device clock minus host clock. Success only |
| `read_seconds` | float | Time spent on the device. Success only |
| `warnings`, `errors` | list of strings | Every WARNING and ERROR logged during the run |

```json
{
  "status": "warning",
  "exit_code": 0,
  "device_ip": "192.168.10.113",
  "serial": "6650154800185",
  "snapshot": "/var/backups/zk/snapshots/2026-11-01_021703",
  "users": 237,
  "punches_on_device": 12791,
  "new_punches": 88,
  "faces_saved": 234,
  "faces_on_device": 234,
  "warnings": ["device clock is +140s off this host - punches are stamped with the device clock"],
  "errors": []
}
```

(Illustrative values; fields abbreviated.)

### Suggested bot behaviour

- After each run, read `last_run.json`. Report `ok` as a one-line summary (users, new punches, faces) and anything else with its `warnings` and `errors` verbatim.
- Copy the new `snapshot` folder and `attendance_master.csv` off the host. Encrypt them first if they leave the building or go to any cloud or messaging service: they contain PINs, card numbers and face templates. At the destination, `sha256sum -c SHA256SUMS` confirms a snapshot arrived intact.
- Treat exit 3 as "skipped", not failed. Alert when the same device fails two runs in a row.
- The bot may run a restore **dry run** to show what a restore would do. Writing restores stays a human action.

### Invariants: do not change

1. Keep `pyzk==0.9` pinned. The guard wraps pyzk's private `_ZK__send_command`, and the script refuses to run if it changes.
2. Never add a write command to `READ_ONLY_COMMANDS`, never bypass `arm_read_only_guard()` or `self_test_guard()`, and never call `disable_device()` in the backup.
3. Never schedule or automate `zk_restore.py --write`. Its serial confirmation exists so a human decides.
4. Never edit snapshots or `attendance_master.csv` in place, and never delete the master archive. Retention removes snapshot folders only.
5. Extend `last_run.json` by adding fields, never renaming or removing them.
6. After any change to either tool, `tests/run_tests.sh` must pass all 25 scenarios.

### Handing this to Claude Code

Put the kit (both tools, `install.sh`, `tests/`, this document as `README.md`) in the bot's repository, then give it a task like:

> Read README.md, especially "Integration guide for the backuper bot". Add the ZKTeco VF600 as a backup target: run zk\_backup.py with --faces on our schedule, read last\_run.json, report the result like our other targets, and copy the snapshot off-site encrypted. Follow the invariants. Run tests/run\_tests.sh before and after your changes.

## Security notes

The bigger risk is not this project's scripts but the device's protocol itself: port 4370 has no real authentication, so anyone who can reach it can read everything, add an admin, or wipe the device with one pyzk call.

- **Set a Comm Key** on each device and pass it with `--password`. It is obfuscated in transit, not encrypted, so it stops casual tools rather than a determined attacker.
- **Restrict who can reach port 4370** to the backup host and the attendance server. A router firewall only filters routed traffic, and the VF600 currently shares the 192.168.10.x subnet with clients, so put the attendance devices in their own VLAN first. Then, on the MikroTik, for example:

```
/ip firewall address-list add list=zk-allowed address=<backup host>
/ip firewall address-list add list=zk-allowed address=<attendance server>
/ip firewall filter add chain=forward dst-address=<device IP> src-address-list=zk-allowed action=accept
/ip firewall filter add chain=forward dst-address=<device IP> action=drop
```

- **Treat backups as sensitive personal data.** They hold PINs, card numbers and face templates, which are biometric identifiers even though they are not images. The tools create files owner-only and the installer makes the folder mode 700. Encrypt any copy that leaves the host, and keep retention no longer than needed.
- **The Comm Key lives in `/etc/default/zk-backup`**, root-only, read by systemd before it drops to the `zkbackup` user.
- **`zk_restore.py` can create users, including admins, on any device it can reach.** Keep it on an administrator's machine, not on a shared server, and never wire it into automation.

## Limitations and future work

Everything a disaster recovery needs is covered and proven; what remains are edges that this firmware or this project has not exercised.

| Limitation | Detail | Possible next step |
| --- | --- | --- |
| Enrollment photos not backed up | Command 10010 is rejected by firmware 6.60 | Try the face-photo commands 0x2749–0x274B (`GET_USERFACEPHOTO_COUNT/NAMES/BYNAME`) on the spare; payloads are not documented |
| Face write layout proven on one firmware | Layout #1 verified on VF600 firmware 6.60, ZFace VX7.0 | Other firmwares go through the 5-layout probe; if none verifies, capture the vendor software's upload with Wireshark |
| Slot field width | Bytes 2–3 assumed to be one u16; only values below 256 were seen | A restore onto a device with more than 255 users would confirm it; the read-back check would catch a wrong guess |
| 72-byte user format only | The restore refuses a target that already holds users in the 28-byte format | Add the 28-byte packer if an older model appears |
| Punches cannot go back to a device | The protocol has no write command for the attendance log | None needed; payroll uses the master archive |
| Restore has no machine-readable result | It prints to the terminal and uses exit codes | Add a `last_restore.json` if a bot ever needs to report a restore |
| Restore tested on UDP only in simulation | The real units answered on TCP | Test on a UDP-only device before relying on it there |
| pyzk 0.9 is pinned | Newer pyzk versions may rename the private method the guard wraps | Re-verify the guard and the tests before upgrading |

### References

- [pyzk](https://github.com/fananimi/pyzk), the open-source ZK protocol library both tools use
- [openzk](https://github.com/kengggg/openzk), a client whose opcode table, recovered from ZKTeco's SDK, supplied face read command 150 and photo command 10010
- [ZKTeco firmware 6.60 command table](https://gist.github.com/zhangyoufu/7bf4f58d7602ada48055bd6e8a6c28e4), the source of `CMD_USERFACE_WRQ` (151) and the face-photo commands
- [zk-protocol](https://github.com/adrobinoga/zk-protocol), community documentation of packets, buffered reads and uploads
