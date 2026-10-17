# Lessons learned — methodology log

What happened while building and rolling out Backuper at the first site (Oct 2026): how we worked, what
went wrong, how we found it, what we measured, and what we'd repeat. Written to be reused when planning
future projects. Decisions themselves are in [DECISIONS.md](DECISIONS.md).

---

## 1. How we worked

1. **Requirements first, then decisions with options.** The owner wrote the goals in one message. Before any
   code, every choice that changes the architecture was asked as a multiple-choice question with a
   recommended option and the trade-off (Telegram upload method, encryption, hosting method, SQL mode,
   MikroTik protocol, retention owner, Telegram layout, deployment). Answers took minutes and nothing had to
   be rebuilt later because of a wrong assumption.
2. **Probe the real environment before designing around it.** Port checks, SMB/NTLM probes (found the AD
   domain), SQL Browser (found a named instance), TLS certificate names, NTP offsets, file listings and sizes.
   Several "requirements" changed once the facts were known (see §4).
3. **Build in thin vertical slices, each tested.** Core modules → sources → engine → bot → installer, with
   unit tests and an **end-to-end test with fakes** before touching real systems.
4. **Simulate the UI.** The Telegram panel is driven by fake events in a test (no Telegram needed). This caught
   two bugs no unit test would have.
5. **Verify on real systems before claiming success** — real backups into the archive, open the zip with
   the real password, check the router has no leftovers, check the host's backup was deleted.
6. **Dry-run anything destructive.** Before deleting 559 old backups: list what is kept/deleted, check the
   order two independent ways (file date and name timestamp), assert every kept file is newer than every
   deleted one — then delete.
7. **Measure, don't guess.** Compression ratio, SMB/zip/upload speeds and listing times were measured; the
   numbers decided the design (compression auto, metadata listing, server-side copy).
8. **Secrets never through chat when avoidable.** Tokens via a file on the server or `backuper credential`
   (hidden prompt); stored only encrypted; never printed.
9. **Commit in meaningful steps with clear messages; document as part of the work** (one doc per code file,
   enforced by a test; changelog with dates; decisions; this log).

## 2. Timeline

| Date | Milestone |
|---|---|
| 2026-10-07 | Requirements and architecture decisions; v1.0 built (all source types, engine, bot, installer, tests). |
| 2026-10-10 | Real environment probed (AD domain, shares, HS vendor lock, slow link); v1.1 (archive-only sources, smarter SQL collection); first commits. |
| 2026-10-13 | v1.2–1.4: groups and upload queue, compression, archive-only mode, router/website/SQL sources verified end to end, plain copies, one-time runs, documentation. |
| 2026-10-13 | v1.5–1.6: troubleshooting logs, live status file and progress. |
| 2026-10-14 | First real scheduled runs: 11 of 17 failed (SMB credits). v1.7: one connection per transfer, retries/resume, restart from the bot. |

## 3. Mistakes we made and how we caught them

| # | What went wrong | Root cause | Found by | Fix / lesson |
|---|---|---|---|---|
| 1 | Typing `//server/share` in a form was treated as a bot command | "starts with `/`" = command | simulated panel test | Only known commands count. **Lesson:** user input formats collide with command syntax. |
| 2 | The default daily schedule silently did nothing | default stored in display format (`03:00`) instead of cron | a warning in the test logs | Defaults must be in stored format; test defaults. |
| 3 | "Keep N copies" could delete the copy just written | two runs in the same minute → same file name; old history entry pointed at the new file | a test written for the feature | Never delete a path that is also in a kept entry. **Lesson:** time-based names collide. |
| 4 | The add-source wizard never asked for group / own time | the wizard listed the type's fields, not the common ones | simulated panel test | Use `all_fields()`. |
| 5 | Bot-made SQL backups were 5× bigger than the application's | no `COMPRESSION` | the owner noticed different sizes | Compression `auto` by edition. **Lesson:** compare with what the existing system produces. |
| 6 | Listing 6,912 files took 72 s | the SMB library's `stat()` does a round trip per entry | measuring | Use the listing's own metadata → 2 s. **Lesson:** read the library's source for hot paths. |
| 7 | Router identity shown as "c" | RouterOS wraps formatted output one char per line without a terminal | first real router test | Read values with `:put [... get ...]`. |
| 8 | RouterOS command errors looked like success | RouterOS prints errors with exit code 0 | anticipated while reading docs | Detect error text in the output. |
| 9 | cPanel FTP account couldn't see the backups | default FTP directory `public_html/<name>`; cPanel can't change it later | listing the FTP root | Recreate with the home folder. |
| 10 | Website backups grew 6 GB → 13.7 GB | an old backup sitting inside the account (`public_ftp/incoming`) is included in each new backup | scanning the largest files | Don't keep backups inside the account (or exclude them). |
| 11 | A patch failed / a test helper broke | library changes: Telethon 1.45 moved inline-button data and forum-topic functions; pyzipper needs its own `ZipInfo` class | probing the libraries before use | Probe new library versions with tiny scripts first. |
| 12 | Edits applied by scripts didn't match | earlier automatic lint fixes had changed the text the script expected | the script's own assertions | Patch scripts must assert their anchors (they did — nothing half-applied). |
| 13 | New functions were added without docs | normal development pace | `tests/test_docs.py` failed | The guard works: docs are part of "done". |
| 14 | Troubleshooting needed digging through mixed logs | log lines didn't say which run/source they belonged to | the owner's request | Context-tagged lines + one log file per run (v1.5). |
| 15 | **First real scheduled runs lost 11 of 17 backups** ("Request requires 1 credits but only 0 credits are available") | `smbclient` shares one connection per server across the whole process; several sources writing into the archive at once used up its SMB credits, and `smbprotocol` raises instead of waiting. A compound request failing half-way also leaked credits for good. | the owner, looking at the archive folder the next morning | One connection per transfer + wait-for-credits patch + retries/resume + completeness checks (v1.7). Reproduced first with a 6-thread stress test (5 failed in 3.5 s), then verified 0 errors. **Lesson:** earlier real tests ran 1–4 sources; test with the *production* concurrency (a whole group at once) before the first scheduled run, and read a library's concurrency model. |
| 16 | A short read could have produced a valid-looking but truncated zip | with 0 credits `smbclient` sends a 0-byte read, which looks like end-of-file | reading the library code while fixing #15 | Every copy checks bytes read = file size. **Lesson:** "EOF" from a network stream must be verified against the expected size. |
| 17 | Failures went unnoticed until someone opened the folder | archive-only mode has no Telegram report, and the status file only showed run totals | the owner | The status file lists every failed source with the reason (v1.7). **Lesson:** every mode needs a place where failures are visible without digging. |
| 18 | The service ran the old version after "restart done" | the restart happened minutes before the update was installed | the version in the startup log line | Restart button / `backuper restart` (v1.7). **Lesson:** after deploying, confirm the running version, not just that it runs. |
| 19 | `backuper status` → *command not found*; then the CLI looked for `data/` in the current folder | the command lived only in `.venv/bin`; relative `DATA_DIR` resolved against the current folder | the owner | `~/.local/bin` link; paths relative to the `.env` file. **Lesson:** run every documented command the way the user will (from their home folder). |
| 20 | The re-run of Hamrahan failed after 2 hours although it was 98 % done | the link to the 172.16.3.x site dropped for ~17 min; every retry restarted the 11 GB read from zero, and the library needed 10 min to notice the silence (+5 min of our credit wait on a dead connection) | the verification run's report | Reads resume at the same byte; 2-min dead-link detection; no waiting on dead connections (v1.7.1). **Lesson:** retries must be *resumable* for big transfers over slow/flaky links — design for the outage, not just the error. |
| 21 | A power cut on the file server failed every source of both morning runs within minutes | runs assumed the archive is there; the per-write retries (30 s, 2 min) are far shorter than a reboot | the owner, from the status file | Wait up to 3 h for the archive at run start; retry failed sources 1 h later up to 3× (v1.8). **Lesson:** plan for the *destination* being down, not only the sources. |
| 22 | Hamrahan System backups were silently missing for 3 days | the vendor moved its (now automatic) backups to a new folder; the bot saw no new file and reported ⏭ "nothing new"; the freshness limit (96 h, set when backups were irregular) hadn't been reached | reviewing the run history while investigating #21 | 30-hour limit now that the backups are daily → a missing day is an error. **Lesson:** "nothing new" from a daily source is a warning sign; keep freshness limits tight once a schedule is regular. |
| 23 | Deleting an already-missing archive file would have crashed | `except (FileNotFoundError, SMBError)` where `SMBError` is itself a tuple — Python only rejects that *when an exception happens* | writing `backuper verify` (first real error path) | `except SMBError`. **Lesson:** error paths need tests too; an `except` clause is only evaluated when something goes wrong. |
| 24 | A damaged AES zip wasn't recognised as an integrity failure | pyzipper raises its own `BadZipFile` class, not the standard library's | the new flipped-bit test | Catch both. **Lesson:** test the failure you are guarding against, not only the success. |

## 4. What we discovered about the environment (and how it changed the plan)

- **All Windows servers are in one AD domain** (found via SMB/NTLM) → one domain account for the bot.
- **Vendor-locked SQL Servers** (accounting software disables admin logins, error 18470) → collect the
  application's own backups until the vendor creates a login; documented the lock-out procedure.
- **The applications' backups are user-triggered** (made when someone closes the app) and on some servers had
  stopped weeks earlier (Holoo1) → the bot makes the backups itself where it can.
- **iCan** had 579 daily full backups (215 GB) and 6,912 diffs with no cleanup, and two databases with no
  backup at all → bot backs those up; 559 old fulls removed (189 GB freed).
- **SQL Server 2008 R2 Express** only speaks TLS 1.0 → `encryption=off` for that one server.
- **Named instance on a dynamic port** (Paliz) → connect by instance name via SQL Browser; "1433 closed"
  was never a problem.
- **The link to one subnet is slow** (~8.7 MB/s vs ~55 MB/s) — reading an 11 GB backup takes ~22 min.
- **Some application backups are already compressed** (99% after zip) → store, don't deflate.
- **Clocks:** the bot server was 62 min slow (time sync never completed — fixed with chrony); every Windows
  server was 9 min 11 s slow. Diagnosis on the DC: `w32tm /query /source` = *Local CMOS Clock*,
  `/query /configuration` showed `Type: NT5DS (Policy)` — a **Group Policy** forces "sync from the domain
  hierarchy", which on the top DC means "from nothing", so local `w32tm /config` changes were overridden —
  and the **Hyper-V time provider** (`VMICTimeProvider`) was enabled. `w32tm /stripchart` to time.windows.com
  worked (offset +551 s), so NTP itself wasn't blocked. **Lesson:** look for "(Policy)" in w32tm output before
  changing local settings. Our design choice (D17) made the backups independent of it anyway.
- **Routers** use custom SSH ports; RouterOS service *Available From* is separate from firewall address
  lists; tunnelled sites may see a different source IP.
- **Hosting:** FTP certificates are issued to the server's name, not the site; one website domain is behind a
  CDN (the panel and FTP are on the real server name). DirectAdmin writes a `.json` file next to each backup.
- **File-server folders** are big in file count: Edari 32,760 files / 21 GB, Finance 11,995 files / 13 GB
  (listed in 29 s and 14 s once listing used the directory metadata). Rights granted with `icacls` on the top
  folder are inherited by the children.
- **my.telegram.org** refused to create the API app ("ERROR") for an old account on a normal connection —
  the one external dependency we couldn't solve from our side → archive-only mode.

- **Routers' own clocks** were days/hours wrong (Factory 5 days behind, DC 16 hours): their NTP clients sat
  at "waiting" because the routers can't resolve DNS names → use NTP server IP addresses.
- **Router login "Permission denied"** on two routers was fixed by deleting and recreating the user and group
  with the same password (a typo or a character the RouterOS terminal treats specially, like `?`).
- **Long paths:** Windows Explorer stops at 260 characters, SMB doesn't — the bot copied a 302-character path
  without problems, but restoring such files by hand needs `robocopy` (or long paths enabled).
- **A folder can look smaller to the bot than to an admin:** Finance showed 13 GB to the bot vs 167 GB to the
  admin — with access-based enumeration, folders an account can't read are simply invisible (no error).
  Grant rights with `icacls … /T` so they reach sub-folders whose inheritance is disabled.
- **my.telegram.org "ERROR":** researched GitHub/forums (Oct 2026). No official cause or fix; Telegram's
  maintainers say they can't influence it. Reported to work for some: random letters/digits for app title and
  short name, re-submitting many times, mobile data instead of Wi-Fi, a phone browser, no ad-blockers/VPN.

## 5. Measurements (first site)

| What | Result |
|---|---|
| AES zip of compressible data (CPU) | ~130 MB/s |
| zlib on incompressible data | ~28 MB/s (→ store instead) |
| SMB read, LAN | ~54 MB/s; slow subnet ~8.7 MB/s |
| AES zip from SMB, one stream | ~22–28 MB/s; three in parallel ~24 MB/s each |
| Internet upload | ~5.9 MB/s (≈ 35–60 min for 11 GB) |
| SQL backup with/without compression | 5.3 GB → 0.98 GB; run 4.5 min → 1.5 min |
| SMB listing 6,912 files | 72 s → 2 s (metadata from the listing) |
| Server-side copy within the file server | 85 MB in 0.1 s |
| Full test run (router + 4 SQL servers, 8 databases) | 1 min 30 s |
| SMB: 6 threads writing over **one** connection (before 1.7) | 5 of 6 failed within 3.5 s (credits) |
| SMB: 10 threads × 384 MB, one connection each (1.7) | 0 errors, 3.8 GB in 39 s (~100 MB/s) |
| Listing Finance after the rights fix | 184,495 files, 167.5 GB in 209 s (was 11,759 files / 13.3 GB visible) |
| Folder copy, one file at a time (same server) | ~1,250 files/min, ~1 GB/min per copy |
| Link to 172.16.3.x in working hours | outage 12:19–12:36; 1.6–4.6 MB/s while shared; listing iCan's Upload folder took 29 min (7 min at 07:00) |
| Resume test: connection cut at 300 MB of a 932 MB file | reconnected after 10 s, continued at the same byte, identical SHA-256 |
| Folder copy, 4 workers (1.7) — Edari | 30,324 files, 21.0 GB in 7 min 49 s (~3,900 files/min, 45.7 MB/s), 0 skipped |

## 6. What worked well

- Asking architecture questions with recommended options up front.
- Fakes for Telegram and sources → the whole flow testable in seconds.
- Safety rules in the engine (no deletion before safe storage, date-folder-only pruning, `.partial` copies).
- Measuring before optimising.
- Least-privilege access plus one-liners the owner could paste.

## 7. What we'd do differently next time

- Ask for the full list of what already exists (existing backup jobs, their folders, retention, vendors' locks)
  *before* designing source types.
- Get the Telegram API credentials on day 1 — it was the longest blocker.
- Check every system's clock on day 1.
- Ask which folders are huge (file counts, not just sizes) early: listing cost depends on the number of folders.

## Permissions

Per-server PowerShell one-liners used at the first site (run as administrator; `$bot` = the bot's domain
account). They grant **share + NTFS** rights; the SQL line lets the SQL Server service write bot-made backups.

```powershell
# Read + delete in an existing share (SQL backup folder)
$bot = 'HARMONIC\BackuperBot'; $share = 'Holoo.Bak'
Grant-SmbShareAccess -Name $share -AccountName $bot -AccessRight Change -Force | Out-Null
icacls (Get-SmbShare -Name $share).Path /grant "${bot}:(OI)(CI)M"

# Folder the bot runs BACKUP into (named instance MSSQLSERVER1 → service MSSQL$MSSQLSERVER1)
icacls $path /grant 'NT SERVICE\MSSQL$MSSQLSERVER1:(OI)(CI)M'

# Read-only share (file-server data)
Grant-SmbShareAccess -Name 'Edari' -AccountName $bot -AccessRight Read -Force | Out-Null
icacls (Get-SmbShare -Name 'Edari').Path /grant "${bot}:(OI)(CI)RX"
```

- **Finance really is 167 GB** (184,495 files): after `icacls D:\Finance /grant …:(OI)(CI)RX /T` the bot
  sees everything. Folders with inheritance turned off had hidden most of it.
- **A dead file can look freshly modified:** two leftovers of the failed run showed a modified time equal to the
  moment the old process exited (Windows closes orphaned handles then), and the file server's clock is 9 minutes
  slow — so the 15-minute "maybe still being written" guard kept them until the next start.
- **The HS (Hamrahan System) backup folders on .250/.251 give *Everyone: Full control* (NTFS):**
  the bot reached them with its plain domain account and no setup on those servers. Worth restricting.

- **2026-10-17 power cut:** the file server (archive + Edari/Finance) was off from before 07:00 until ~08:40
  ("No route to host"); the bot server stayed up. Nothing was lost, but the day's backups failed until re-run.
- **Hamrahan System's vendor** now makes automatic daily backups into `D:\AutoBackup\<db>` on .250/.251 and
  didn't agree to a SQL login for us; the bot collects from there and manages the space (keep newest N).

## Open items at the time of writing (2026-10-17)

- Telegram: cancelled by the owner for now (api_id impossible); archive-only. Cloud copy: options suggested.
- `D:\AutoBackup` shares on .250/.251 (owner).
- Domain controller time source: Group Policy + Hyper-V time provider (see §4).
