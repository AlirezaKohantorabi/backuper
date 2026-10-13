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
  server was 9 min 11 s slow (the DC isn't synced to a real source); one router's NTP client stuck "waiting".
- **Routers** use custom SSH ports; RouterOS service *Available From* is separate from firewall address
  lists; tunnelled sites may see a different source IP.
- **Hosting:** FTP certificates are issued to the server's name, not the site; one website domain is behind a
  CDN (the panel and FTP are on the real server name).
- **my.telegram.org** refused to create the API app ("ERROR") for an old account on a normal connection —
  the one external dependency we couldn't solve from our side → archive-only mode.

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

## Open items at the time of writing (2026-10-13)

- Telegram api_id (my.telegram.org "ERROR").
- Factory and DC routers refuse the bot's login (allowed address vs the IP the router sees).
- Edari/Finance read rights for the bot account.
- Hamrahan System SQL logins (vendor).
- Domain controller time source.
