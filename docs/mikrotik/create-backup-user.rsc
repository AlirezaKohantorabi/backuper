# =============================================================================
# Backuper — MikroTik user for the bot (RouterOS v6.43+ / v7)
# Paste into the router terminal (WinBox → New Terminal) after replacing:
#   CHANGE-ME        -> a long random password (store it in the bot as a credential)
#   192.168.11.11    -> the IP of the server running Backuper
# =============================================================================

# Group: ssh+ftp (= SFTP download), read/write/policy/test (export + backup),
# sensitive (secrets in the export, download of .backup files)
/user group add name=backuper policy=ssh,ftp,read,write,policy,test,sensitive comment="Backuper bot"

# User that may only log in from the backup server
/user add name=backuper group=backuper password="CHANGE-ME" address=192.168.11.11/32 comment="Backuper bot"

# SSH service must be enabled (any port; set the same port in the bot). If its
# "Available From" list is set, the backup server must be in it:
#   /ip service print where name=ssh
#   /ip service set ssh address=<existing list>,192.168.11.11/32
/ip service enable ssh

# If the input chain drops by default, allow the backup server to reach SSH
# (use your SSH port; place-before=0 puts the rule at the top — drop that part if the list is empty):
# /ip firewall filter add chain=input action=accept protocol=tcp dst-port=22 src-address=192.168.11.11 comment="Backuper bot SSH" place-before=0

# Optional: take the time from the backup server (it serves NTP to the LAN). RouterOS 7:
# /system ntp client set enabled=yes servers=192.168.11.11
# RouterOS 6:
# /system ntp client set enabled=yes primary-ntp=192.168.11.11
