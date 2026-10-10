/* =============================================================================
   Backuper — SQL login for "SQL Server — run BACKUP + collect" sources.

   Run on each SQL Server that the bot should back up by itself (no SQL Agent
   job needed — also works on SQL Server Express).

   Requirements:
     * SQL Server must allow SQL logins: Server Properties → Security →
       "SQL Server and Windows Authentication mode" (restart the service after).
       Alternatively use a Windows/domain account (DOMAIN\user) in the bot.
     * TCP/1433 (or the instance's port) reachable from the backup server.
     * The SQL Server SERVICE account needs write access to the backup folder
       (e.g. D:\SQLBackup) — BACKUP is written by the service, not by the login.
     * Share that folder (read + delete for the bot's Windows account) so the
       bot can collect the .bak and apply "keep last N".
   ============================================================================= */

USE [master];
GO

-- 1) Login (change the password!)
IF SUSER_ID(N'backuper') IS NULL
    CREATE LOGIN [backuper] WITH PASSWORD = N'CHANGE-ME-to-a-long-random-password',
        CHECK_POLICY = ON, CHECK_EXPIRATION = OFF, DEFAULT_DATABASE = [master];
GO

-- 2) db_backupoperator in every online user database
--    (re-run after creating new databases, or limit the list to the ones you need)
DECLARE @sql nvarchar(max) = N'';
SELECT @sql += N'USE ' + QUOTENAME(name) + N';
IF USER_ID(N''backuper'') IS NULL CREATE USER [backuper] FOR LOGIN [backuper];
EXEC sp_addrolemember N''db_backupoperator'', N''backuper'';
'
FROM sys.databases
WHERE database_id > 4 AND state_desc = N'ONLINE' AND source_database_id IS NULL;
EXEC sys.sp_executesql @sql;
GO

-- 3) Check: one row per user database, "ok" where backuper is a backup operator
DECLARE @chk nvarchar(max) = N'';
SELECT @chk += N'SELECT N' + QUOTENAME(name, '''') + N' AS [database], CASE WHEN EXISTS (SELECT 1 FROM '
    + QUOTENAME(name) + N'.sys.database_role_members rm JOIN ' + QUOTENAME(name)
    + N'.sys.database_principals r ON r.principal_id = rm.role_principal_id JOIN ' + QUOTENAME(name)
    + N'.sys.database_principals m ON m.principal_id = rm.member_principal_id '
    + N'WHERE r.name = N''db_backupoperator'' AND m.name = N''backuper'') THEN ''ok'' ELSE ''MISSING'' END AS backuper'
    + N' UNION ALL '
FROM sys.databases
WHERE database_id > 4 AND state_desc = N'ONLINE' AND source_database_id IS NULL;
IF @chk <> N'' BEGIN
    SET @chk = LEFT(@chk, LEN(@chk) - LEN(N' UNION ALL'));
    EXEC sys.sp_executesql @chk;
END
GO

/* -----------------------------------------------------------------------------
   Alternative: no SQL login, use the Windows user the bot already uses for SMB
   (created by docs/windows/setup-backuper-access.ps1). Works when the server only
   allows Windows authentication. In the bot, set the SQL login credential's
   username to  COMPUTERNAME\backuper  (same password as the Windows user).

   CREATE LOGIN [COMPUTERNAME\backuper] FROM WINDOWS;
   -- then step 2 above with [COMPUTERNAME\backuper] instead of [backuper]
   ----------------------------------------------------------------------------- */
