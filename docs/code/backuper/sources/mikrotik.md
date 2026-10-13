# backuper/sources/mikrotik.py

**Purpose:** source type **"MikroTik router"**: over SSH it runs `/export show-sensitive` (`.rsc`, the
readable configuration including passwords) and `/system backup save` (`.backup`, the binary backup),
downloads both over SFTP, deletes them from the router, and returns one artifact with the two files plus
`router-info.txt` (identity, model, version, uptime…).

**Why SSH/SFTP and not the REST API?** REST can create the files but cannot download a binary
`.backup`; SSH does both with one service. The router user only needs `ssh,ftp,read,write,policy,test,sensitive`
and is restricted to the bot server's IP ([docs/mikrotik/create-backup-user.rsc](../../../mikrotik/create-backup-user.rsc)).

**Uses:** [transport/ssh.py](../transport/ssh.md) (host-key pinning), [base.py](base.md).

## Functions and methods

| Name | What it does / why |
|---|---|
| `_ROS_ERROR` | RouterOS prints command errors as normal output with exit code 0; this pattern recognises them. |
| `ros` | Runs a command and raises if RouterOS printed an error; passwords are masked in the error text. |
| `ros_values` | Reads values with `:put ("key=" . [/… get …])`. **Why:** without a terminal, RouterOS wraps formatted `print` output one character per line (the identity `core.ru.harmonic.local` came back as "c", "o", "r"…). Each getter runs in `:do {} on-error={}` so a missing property (RouterOS 6) is just empty. |
| `ros_quote` | Quotes a value for the RouterOS CLI (`"`, `\`, `$` escaped). |
| `MikroTik` | The source type (`type_id = "mikrotik"`, category network). |
| `MikroTik.describe` | `host · export + backup`. |
| `MikroTik._target` | SSH target with the pinned host key from the source state. |
| `MikroTik._connect` | Connects; pins the host key on first use. |
| `MikroTik._info` | identity, version, board, architecture, uptime, CPU, memory. |
| `MikroTik.test` | Login + SFTP check, router clock/time zone and NTP status. |
| `MikroTik.collect` | Export (v7: `show-sensitive`; v6 shows secrets by default) and backup (`dont-encrypt=yes`, or the backup password if set; falls back for RouterOS < 6.43), download, delete from router. |
| `MikroTik._wait_for_files` | Waits until both files exist and stopped growing (some boards write under `flash/`). |
