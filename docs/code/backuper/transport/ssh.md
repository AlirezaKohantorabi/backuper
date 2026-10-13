# backuper/transport/ssh.py

**Purpose:** SSH connections (asyncssh) with **trust-on-first-use host-key pinning**: the first time the
bot connects to a host it stores the host's public key in the source's state; afterwards the server must
present exactly that key — checked *before* the password is sent. A changed key stops the backup with a
clear message ("device replaced? → Forget pinned SSH key").

**Used by:** [sources/mikrotik.py](../sources/mikrotik.md), [transport/remote.py](remote.md) (SFTP).

| Name | What it does / why |
|---|---|
| `log` | Logger. |
| `HostKeyChanged` | The pinned key doesn't match. |
| `_with_legacy` | Default algorithm list + extra legacy ones that the library supports. |
| `_algorithm_options` | Modern defaults plus legacy algorithms old RouterOS 6 / old hosting SSH servers need (`diffie-hellman-group14-sha1`, CBC ciphers, `hmac-sha1`, `ssh-rsa`). |
| `SSHTarget` | Host, port, username, password, pinned key, timeout. |
| `connect` | Connects (password auth only, no agent/keys), verifies the pinned key, returns the connection and the server's key line. |
| `run` | Runs a command; raises on a non-zero exit status with `password=…` masked in the message. |
