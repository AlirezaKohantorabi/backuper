# backuper/sources/directadmin.py

**Purpose:** source type **"DirectAdmin account (full backup)"**: asks DirectAdmin to create a user
backup (`CMD_API_SITE_BACKUP`, `action=backup`, all items by default — domains, email, databases, FTP…),
waits for `~/backups/backup-…tar.gz` to appear and stop growing, downloads it over FTPS/SFTP and removes
it (or keeps the newest one on the host).

**Login:** DirectAdmin username + password (or a Login Key, if the host enables them). Use the **real
panel address** (e.g. `server210s.irwebspace.com:2223`) — a domain behind a CDN (ArvanCloud) doesn't
expose port 2222/2223 or FTP.

**Uses:** [hosting_common.py](hosting_common.md), `httpx`. DirectAdmin's legacy API answers
URL-encoded text (`error=0&…`), or an HTML page when the login is wrong.

| Name | What it does |
|---|---|
| `ALL_ITEMS` | Every backup item DirectAdmin knows (domain files, subdomains, email + email data, forwarders, autoresponders, vacation, lists, email settings, FTP, FTP settings, databases + data). |
| `DirectAdmin` | The source type (`type_id = "directadmin"`, category websites). |
| `DirectAdmin.describe` | `domain (DirectAdmin)`. |
| `DirectAdmin._client` | HTTPS client with the login. |
| `DirectAdmin._parse` | Parses the URL-encoded answer; readable errors for bad logins / HTML answers / API errors. |
| `DirectAdmin.test` | `CMD_API_SHOW_DOMAINS` + download login; mentions archives sitting on the host. |
| `DirectAdmin.collect` | Create backup → wait → download → delete/keep. |

**Lesson learned:** a backup file left inside the account (harmony.ir had a 6.7 GB one in
`public_ftp/incoming`) is included in every *new* backup — backups then grow each time (6 GB → 13.7 GB).
