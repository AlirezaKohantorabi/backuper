/* =============================================================================
   Backuper — SQL Server login for "SQL Server — run BACKUP + collect" sources.

   Run once on each SQL Server the bot should back up by itself (no SQL Agent
   job needed — also works on SQL Server Express). Needs sysadmin; if you get
   "Login failed … account is disabled", see 03-locked-out-of-sql.md.

   Set @Login below:
     * a Windows / domain account, e.g. N'HARMONIC\BackuperBot'  (leave @Password NULL)
       -> no "mixed mode" needed; in the bot use the same credential as for the shares
     * or a SQL login name, e.g. N'backuper' with a @Password
       -> the server must allow "SQL Server and Windows Authentication mode"

   Also needed: TCP 1433 (or the instance port) reachable from the backup server,
   the SQL Server SERVICE account can write to the backup folder, and that folder
   shared for the bot (read + delete). docs/windows/setup-backuper-access.ps1 does
   the share, the folder rights and the firewall.
   ============================================================================= */

USE [master];
SET NOCOUNT ON;

DECLARE @Login    sysname       = N'HARMONIC\BackuperBot';
DECLARE @Password nvarchar(128) = NULL;   -- only for a SQL login

-- 1) Login
IF SUSER_ID(@Login) IS NULL
BEGIN
    DECLARE @create nvarchar(max) =
        CASE WHEN @Password IS NULL
             THEN N'CREATE LOGIN ' + QUOTENAME(@Login) + N' FROM WINDOWS WITH DEFAULT_DATABASE = [master];'
             ELSE N'CREATE LOGIN ' + QUOTENAME(@Login) + N' WITH PASSWORD = N' + QUOTENAME(@Password, '''')
                  + N', CHECK_POLICY = ON, CHECK_EXPIRATION = OFF, DEFAULT_DATABASE = [master];' END;
    EXEC sys.sp_executesql @create;
    PRINT N'Created login ' + @Login;
END

-- 2) db_backupoperator in every online user database
--    (re-run after creating new databases)
DECLARE @db sysname, @sql nvarchar(max), @q nvarchar(300) = QUOTENAME(@Login);
DECLARE dbs CURSOR LOCAL FAST_FORWARD FOR
    SELECT name FROM sys.databases
    WHERE database_id > 4 AND state_desc = N'ONLINE' AND source_database_id IS NULL;
OPEN dbs; FETCH NEXT FROM dbs INTO @db;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'USE ' + QUOTENAME(@db) + N';
        IF USER_ID(@l) IS NULL EXEC (N''CREATE USER '' + @q + N'' FOR LOGIN '' + @q);
        EXEC sp_addrolemember N''db_backupoperator'', @l;';
    EXEC sys.sp_executesql @sql, N'@l sysname, @q nvarchar(300)', @l = @Login, @q = @q;
    PRINT N'  ' + @db + N': db_backupoperator granted';
    FETCH NEXT FROM dbs INTO @db;
END
CLOSE dbs; DEALLOCATE dbs;
GO
