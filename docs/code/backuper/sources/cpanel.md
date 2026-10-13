# backuper/sources/cpanel.py

**Purpose:** source type **"cPanel account (full backup)"**: asks cPanel to generate a full account
backup (home directory, databases, email, configuration) into the home folder, waits until cPanel reports
it complete, downloads it and removes it from the host (or keeps the newest one there).

**Steps:** list existing archives over FTP → UAPI `Backup::fullbackup_to_homedir` → poll API2
`Backups::listfullbackups` every 30 s until the new file is `complete` (or, if that API is unavailable,
watch the file until it stops growing) → **new** FTP session (servers drop idle control connections during
long backups) → download → delete/keep.

**Login:** cPanel username + **API token** (cPanel → Security → Manage API Tokens), header
`Authorization: cpanel user:TOKEN`; or the account password with *Login type* `password`. The download
login is a cPanel FTP account whose directory is the home folder.

**Uses:** [hosting_common.py](hosting_common.md), `httpx`.

| Name | What it does |
|---|---|
| `CPanel` | The source type (`type_id = "cpanel"`, category websites). |
| `CPanel.describe` | `host (cPanel)`. |
| `CPanel._client` | HTTPS client with token or password auth. |
| `CPanel._uapi` | Calls a UAPI function; readable errors for bad logins and API errors. |
| `CPanel._list_full_backups` | API2 list with each backup's status; `None` if the API isn't available. |
| `CPanel.test` | API call + download login; mentions archives sitting on the host. |
| `CPanel.collect` | The steps above. |
| `CPanel._wait_via_api` | Polls `listfullbackups` until the new backup is complete. |
