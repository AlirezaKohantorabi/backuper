/* =============================================================================
   Backuper — SQL Agent job for "SQL Server — collect .bak from share" sources.

   Creates a daily job that writes one FULL backup per database into a folder:
       <BackupDir>\<Database>_backup_YYYY_MM_DD_HHMMSS.bak
   The bot picks up the newest file per database and deletes old ones beyond
   "keep last N", so this job does NOT clean up by itself.

   Edit the four variables below, then run it once in SSMS on the server.
   (SQL Server Express has no SQL Agent — use 01-create-backup-login.sql and
   the bot's "run BACKUP + collect" source type instead.)
   ============================================================================= */

USE [msdb];
GO

DECLARE @BackupDir   nvarchar(260) = N'D:\SQLBackup';  -- local folder on THIS server (share it for the bot)
DECLARE @Databases   nvarchar(max) = N'*';             -- '*' = all user databases, or 'Cash,Project'
DECLARE @StartTime   int           = 013000;           -- HHMMSS, run daily at 01:30 (before the bot's run)
DECLARE @UseCompression bit        = 0;                -- 1 only on Standard/Enterprise (not Express/Web)

DECLARE @JobName sysname = N'Backuper - daily full backup';
DECLARE @step nvarchar(max) = N'
SET NOCOUNT ON;
DECLARE @dir nvarchar(260) = N''' + REPLACE(@BackupDir, N'''', N'''''') + N''';
DECLARE @list nvarchar(max) = N''' + REPLACE(@Databases, N'''', N'''''') + N''';
DECLARE @stamp nvarchar(20) = REPLACE(REPLACE(REPLACE(CONVERT(nvarchar(19), GETDATE(), 120), N''-'', N''_''), N'':'', N''''), N'' '', N''_'');
DECLARE @db sysname, @file nvarchar(400), @errors int = 0;
DECLARE c CURSOR LOCAL FAST_FORWARD FOR
    SELECT name FROM sys.databases
    WHERE database_id > 4 AND state_desc = N''ONLINE'' AND source_database_id IS NULL
      AND (@list = N''*'' OR CHARINDEX(N'','' + name + N'','', N'','' + REPLACE(@list, N'' '', N'''') + N'','') > 0);
OPEN c; FETCH NEXT FROM c INTO @db;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @file = @dir + N''\'' + @db + N''_backup_'' + @stamp + N''.bak'';
    BEGIN TRY
        ' + CASE WHEN @UseCompression = 1
                 THEN N'BACKUP DATABASE @db TO DISK = @file WITH INIT, CHECKSUM, COMPRESSION;'
                 ELSE N'BACKUP DATABASE @db TO DISK = @file WITH INIT, CHECKSUM;' END + N'
    END TRY
    BEGIN CATCH
        SET @errors += 1;
        PRINT N''Backup of '' + @db + N'' failed: '' + ERROR_MESSAGE();
    END CATCH
    FETCH NEXT FROM c INTO @db;
END
CLOSE c; DEALLOCATE c;
IF @errors > 0 RAISERROR(N''%d database backup(s) failed'', 16, 1, @errors);
';

IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobs WHERE name = @JobName)
    EXEC msdb.dbo.sp_delete_job @job_name = @JobName, @delete_unused_schedule = 1;

EXEC msdb.dbo.sp_add_job      @job_name = @JobName, @enabled = 1,
                              @description = N'Full backups collected by Backuper (Telegram bot).';
EXEC msdb.dbo.sp_add_jobstep  @job_name = @JobName, @step_name = N'Backup databases', @subsystem = N'TSQL',
                              @database_name = N'master', @command = @step, @retry_attempts = 1, @retry_interval = 10;
EXEC msdb.dbo.sp_add_schedule @schedule_name = N'Backuper daily', @freq_type = 4, @freq_interval = 1,
                              @active_start_time = @StartTime;
EXEC msdb.dbo.sp_attach_schedule @job_name = @JobName, @schedule_name = N'Backuper daily';
EXEC msdb.dbo.sp_add_jobserver @job_name = @JobName, @server_name = N'(local)';
GO

-- Run it once now to test:
-- EXEC msdb.dbo.sp_start_job N'Backuper - daily full backup';
