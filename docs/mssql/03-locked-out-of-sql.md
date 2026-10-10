# "Login failed … The account is disabled" (error 18470)

Some vendors of accounting/ERP software disable the Windows administrators' SQL Server
login after installing, so only their own `sa`-level login can manage the server.
Windows is fine — it's the **SQL Server login** that's disabled. To let the bot run
`BACKUP DATABASE`, someone with sysadmin rights has to create a backup-only login once.

## Option A — ask the vendor (recommended first)

Ask their support to run [01-create-backup-login.sql](01-create-backup-login.sql) (or to
give you a sysadmin login). Tell them the login only gets `db_backupoperator`: it can
make backups, it can't read or change data through SQL.

## Option B — do it yourself (Microsoft's "locked out" procedure)

Any local Windows administrator can become sysadmin by starting SQL Server in
single-user mode. It takes **a few minutes of downtime** (the application disconnects),
and it may matter for your support contract with the vendor — decide that first.

In **Command Prompt (Run as administrator)** on the SQL server:

```bat
:: 1. find the service name: MSSQLSERVER (default) or MSSQL$NAME (named instance)
sc query type= service state= all | findstr /i "SERVICE_NAME: MSSQL"

:: 2. stop SQL Agent and SQL Server, then start in single-user mode for sqlcmd only
net stop SQLSERVERAGENT
net stop MSSQLSERVER
net start MSSQLSERVER /m"SQLCMD"

:: 3. connect as Windows admin (named instance: -S .\NAME) and run the login script
::    (edit the password inside the .sql file first)
sqlcmd -S . -E -i 01-create-backup-login.sql

:: 4. back to normal
net stop MSSQLSERVER
net start MSSQLSERVER
net start SQLSERVERAGENT
```

If `sqlcmd` says *"Login failed"* in step 3, another program grabbed the single
connection first — stop the application's services, repeat from step 2.

### SQL logins need "mixed mode"

If the bot's test says *"Login failed for user 'backuper'"* although the password is
right, the server only accepts Windows logins. Either switch to *SQL Server and Windows
Authentication mode* (SSMS → server Properties → Security, then restart the service —
in single-user mode you can do it with
`EXEC xp_instance_regwrite N'HKEY_LOCAL_MACHINE', N'Software\Microsoft\MSSQLServer\MSSQLServer', N'LoginMode', REG_DWORD, 2`),
or use the Windows alternative at the bottom of `01-create-backup-login.sql`.
