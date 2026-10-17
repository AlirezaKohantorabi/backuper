# Decision log

Why the system is built the way it is. Each entry: the decision, the alternatives we considered, why we
chose this one, and what it costs. Dates are when the decision was made. Newest decisions at the bottom.
Who decided: **O** = the project owner (requirements, choices offered as options), **E** = engineering
recommendation accepted by the owner.

---

### D1 — One Python service with asyncio (2026-10-07, E)
- **Alternatives:** several scripts + cron; Node.js; Go.
- **Why:** one process holds the bot, the scheduler and the runs, so the bot can show live progress and
  runs can coordinate (one upload queue). Python has mature libraries for every protocol needed (SMB, SSH,
  TDS, FTP, Telegram MTProto, AES zip). Blocking libraries run in threads.
- **Cost:** pure-Python SMB is CPU-bound around ~50 MB/s per stream — fine for this workload.

### D2 — Telegram over MTProto (Telethon), not the plain Bot API (2026-10-07, O)
- **Alternatives:** Bot API with 49 MB parts; self-hosted Bot API server (needs api_id too).
- **Why:** Bot API uploads are capped at 50 MB; a single SQL backup here is 11 GB (≈ 230 parts/day).
  MTProto allows 2000 MiB per file (6 parts). One client also runs the admin panel.
- **Cost:** needs `api_id`/`api_hash` from my.telegram.org — which turned out to be the hardest part of the
  whole setup (see LESSONS). Mitigated by D14 (archive-only mode).

### D3 — AES-256 zips (2026-10-07, O)
- **Alternatives:** plain zips; 7z; GPG.
- **Why:** backups contain full databases and router configs with passwords and go to a Telegram group.
  AES zip opens with 7-Zip/WinRAR (everyone has them); pyzipper streams, so no temp copies. A `.tar.gz`
  from a hosting panel is zipped again only as an encrypted envelope (stored, +28 bytes).
- **Cost:** Windows Explorer can't open AES zips; the zip password must be kept outside Telegram.

### D4 — SQLite + encrypted secrets, not config files (2026-10-07, E)
- **Why:** the bot edits configuration live; SQLite is transactional and needs no server. Secrets are
  encrypted with Fernet using `SECRET_KEY` from `.env`. A YAML import/export exists for bulk setup and
  reuse.

### D5 — Schema-driven forms (`Field`) (2026-10-07, E)
- **Why:** the owner wanted to manage everything from the bot and to reuse the project elsewhere. Declaring
  fields once drives the bot's forms, validation, display and the YAML importer — a new source type needs no
  UI code.

### D6 — Pull, never push, for hosting panels (2026-10-07, O)
- **Alternatives:** the host pushes backups to an FTP server on our side; WordPress plugin.
- **Why:** pushing needs an internet-reachable FTP server port-forwarded into the LAN. The bot asks the panel
  API to create a backup, downloads it over FTPS and deletes it on the host — nothing inbound.

### D7 — MikroTik over SSH/SFTP, not the REST API (2026-10-07, O)
- **Why:** REST can create the export and backup but cannot download the binary `.backup`; SSH does both with
  one service. The router user is limited to the needed policies and to the bot server's IP.

### D8 — SQL: both "collect" and "run BACKUP"; prefer the bot making backups (2026-10-07 / 2026-10-13, O)
- **Why:** some servers already back up automatically (keep using them); others depend on a person
  closing an application or had no backup at all. "Run BACKUP" makes it automatic and maintainable:
  `COPY_ONLY` (doesn't break existing backup chains), `CHECKSUM`, and **compression automatically where
  the edition supports it** (a 5.3 GB backup became 0.98 GB, 3× faster).
- **Cost:** needs a SQL login with `db_backupoperator` per server (vendor-locked servers need the vendor).

### D9 — The app deletes old backups, but only after safe storage (2026-10-07, O)
- **Alternatives:** each server's job cleans up itself.
- **Why:** retention is then editable from the bot. Safety: never before the new backup is in the archive
  (or Telegram); never the newest file; per database/group; the archive only deletes Jalali-named folders.
- **Cost:** the bot account needs Modify (delete) on those folders.

### D10 — Smallest file first, one upload at a time (2026-10-07, O; queue 2026-10-13)
- **Why:** a huge file must not delay everything else. Since groups can overlap, one global upload queue
  guarantees two files are never uploaded at once, without anyone spacing times out manually.

### D11 — Groups instead of one global schedule; upload-from; one-time runs (2026-10-13, O)
- **Why:** the owner wanted to group sources ("start at 8:00, let the machine decide the order") and still
  run single sources at their own times, plus one-off runs for exceptional days. Manual and one-time runs
  upload immediately.

### D12 — systemd + uv venv, not Docker (2026-10-07, O)
- **Why:** no Docker on the server and Docker Hub access isn't guaranteed; `install.sh` needs no root except
  for the service file; uv works without `python3-venv`/`pip`.

### D13 — A least-privilege account for the bot (2026-10-10, E)
- **Alternatives:** the Administrator account / a domain admin.
- **Why:** the bot server holds every credential; if it were compromised, a domain admin password would hand
  over the whole domain. A normal domain user with rights on specific folders limits the damage.
  Discovered that all servers are in one domain → one domain account instead of many local accounts.

### D14 — Archive-only mode when Telegram isn't configured (2026-10-13, E)
- **Why:** my.telegram.org kept refusing to create the app; backups shouldn't wait for that. The same service
  runs the schedules and fills the archive; adding the api_id later turns uploads on.

### D15 — Company data outside the repository (2026-10-07, O)
- **Why:** the repo must be reusable at another company. `.env`, `data/` and `local/company.yaml` are
  git-ignored; examples are generic. (Some docs mention the first site's hosts as examples — the repo is private.)

### D16 — Plain dated copies for big file-server folders, copied server-side (2026-10-13, O)
- **Why:** Edari/Finance are too big for Telegram and should be browsable; the owner wanted plain files with
  the date in the name. Source and archive are on the same server, so SMB server-side copy keeps the data
  off the network.
- **Cost:** full copy every run (no incremental mode yet); not encrypted (internal server).

### D17 — Schedule by the bot server's clock (2026-10-13, O)
- **Alternatives:** make the bot server the organisation's NTP source.
- **Why:** the owner didn't want the bot server to become infrastructure for the whole network. The bot's
  clock is NTP-synchronised and all scheduling uses it; servers being a few minutes off doesn't matter for
  backups (freshness limits are in hours).

### D18 — Keep newest backup on the host: an option per hosting source (2026-10-13, O)
- **Why:** an extra location for small sites; off for big ones (quota).

### D19 — Documentation as part of the code (2026-10-13, O)
- **Why:** another developer must understand the project in a day, and our experience should feed future
  projects. One document per code file, enforced by a test; changelog with dates and tags; this decision log;
  a lessons log.

### D20 — One SMB connection per transfer (2026-10-14, E)
- **Alternatives:** keep `smbclient`'s shared connection per server and serialise all archive writes; retry only.
- **Why:** the first real scheduled runs lost 11 of 17 backups to *"Request requires 1 credits but only 0
  credits are available"*: several sources wrote into the archive at once over one shared connection, ran out
  of SMB credits, and `smbprotocol` fails instantly instead of waiting. Separate connections remove the
  contention (measured: 10 parallel writers, no errors, ~100 MB/s); a patch makes a shared connection wait for
  credits and never send a 0-byte read (a fake end-of-file); every copy checks that all bytes arrived; archive
  writes are retried 3× and folder copies resume.
- **Cost:** a TCP connection + NTLM login per transfer (~50 ms); a small patch of library internals, guarded
  by a test that fails if a library upgrade moves them.

### D21 — Restart = the service exits and systemd starts it again (2026-10-14, O)
- **Alternatives:** a sudoers rule for `systemctl restart backuper`.
- **Why:** the owner wanted to restart from the bot. The unit has `Restart=always` and `NoNewPrivileges=true`
  (sudo can't work from inside the service anyway). A request file in `data/` is written by the bot or
  `backuper restart`; the service exits when no backup is running (or at once on "now"). No root rule needed.

### D22 — Folder copies with 4 parallel workers (2026-10-14, E)
- **Why:** Finance turned out to be 167 GB in 184,495 files; one file at a time ran at ~1,250 files/min
  (≈ 2.5 h). Small-file copying is round-trip bound, so 4 workers (each its own connection) cut it several-fold.

### D23 — Resume reads after a dropped connection (2026-10-14, E)
- **Alternatives:** copy big source files to local staging first (resumable by offset), then zip; only retry.
- **Why:** the link to the other site drops for many minutes during working hours; restarting an 11 GB read
  costs an hour each time. Continuing at the same byte (file unchanged) needs no extra disk and keeps the
  zip streaming straight into the archive.

### D24 — Verify every zip by unzipping it again (2026-10-17, O)
- **Owner's request:** hash the files before zipping and compare after unzipping.
- **How:** the "before" hash is taken from the same bytes that are zipped (a second read of an 11 GB file over
  the slow site link would cost another 35 minutes and could read a file that changed meanwhile). The check reads
  the zip back from the archive — so it also proves what's on the archive disk. Cost: one extra LAN read per zip
  (~10 GB/day, a few minutes). 1.8 also kept `<zip>.sha256` next to each zip; the owner wanted them gone once
  the check passes (1.9) — later checks rely on the zip's own AES authentication / CRC (7-Zip *Test*,
  `backuper verify`).
- **Not for plain folder copies** (Edari/Finance) — owner: not needed.

### D25 — Wait for the archive, then retry failed sources later (2026-10-17, E)
- **Why:** a power cut on the file server failed every source of both morning runs within minutes, and nothing
  tried again. Waiting (up to 3 h, checking every minute) covers reboots and short outages without making
  backups on the servers that can't be stored; automatic retries (1 h later, up to 3×) cover everything else
  that fails for a while (site link outages). Both are cheap and visible in the status file.

### D26 — The bot backs itself up (2026-10-17, O)
- **Why:** without its database, `.env` (the key to the stored passwords) and the git-ignored company config,
  rebuilding the bot server means re-entering every source and password. A daily zip in the archive, with
  restore steps inside. Only risk: the zip password is needed to open it and is stored inside it — the owner
  keeps a copy elsewhere.

### D27 — Manual-only bot backups of vendor databases, next to the vendor's (2026-10-17, O)
- **Why:** the owner created a SQL login on the Hamrahan System servers "in case we need a manual/one-time
  backup with the bot". Three sources without group or time (`…-Manual`), `COPY_ONLY` (the vendor's backup chain
  is untouched), written into the vendor's AutoBackup folders under our own name pattern, which the daily
  collect sources' patterns (`<db>_backup_*.bak`) don't match. `backuper once` replaces the bot's ⏱ menu.

