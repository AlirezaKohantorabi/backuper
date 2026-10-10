"""Runtime settings editable from the bot (stored in the database)."""

from __future__ import annotations

from backuper.fields import BOOL, CHOICE, CREDENTIAL, INT, SCHEDULE, SECRET, TEXT, Field

FOLDER_FORMATS = {"1405-07-15": "%Y-%m-%d", "1405_07_15": "%Y_%m_%d", "1405.07.15": "%Y.%m.%d"}

SETTINGS_FIELDS: list[Field] = [
    Field("schedule", "Daily schedule", SCHEDULE, default="0 3 * * *",
          help="When the main run starts (Tehran time). One or more times like 03:00, 15:30 "
               "or cron lines separated by ';'. Sources with their own schedule are excluded.",
          example="03:00"),
    Field("archive_path", "Archive location", TEXT, required=False, default="",
          help=r"Where zips are kept, one sub-folder per Jalali date. A network path like "
               r"\\192.168.10.35\Backuper or a local folder on this server. Empty = Telegram only.",
          example=r"\\192.168.10.35\Backuper"),
    Field("archive_credential", "Archive credential", CREDENTIAL, required=False, default="",
          help="Credential used to write to the archive share."),
    Field("archive_keep_days", "Archive days to keep", INT, default=14, min=1, max=3650,
          help="How many daily folders to keep in the archive. Older date folders are deleted."),
    Field("folder_format", "Date folder format", CHOICE, default="1405-07-15",
          choices=tuple(FOLDER_FORMATS)),
    Field("zip_password", "Zip password", SECRET, required=False, default="",
          help="AES-256 password for every zip. Open with 7-Zip or WinRAR. Empty = no encryption (not recommended)."),
    Field("compression_level", "Compression level", INT, default=6, min=0, max=9,
          help="0 = store only, 1 = fastest, 9 = smallest. 6 is a good balance."),
    Field("chat_id", "Telegram group", TEXT, required=False, default="",
          help="Set automatically when an admin sends /bind inside the group."),
    Field("topic_mode", "Topic layout", CHOICE, default="category", choices=("category", "source", "none"),
          help="category: Databases/Network/Websites/File Servers/Reports topics. "
               "source: one topic per source. none: post everything in the main chat."),
    Field("split_mb", "Telegram part size (MB)", INT, default=1950, min=10, max=2000,
          help="Zips bigger than this are sent as .zip.001, .zip.002 … parts (7-Zip joins them)."),
    Field("upload_retries", "Upload retries", INT, default=3, min=1, max=10),
    Field("concurrency", "Parallel sources", INT, default=3, min=1, max=16,
          help="How many sources are collected and zipped at the same time."),
    Field("notify_admins", "DM admins on failure", BOOL, default=True),
    Field("report_on_success", "Post report when all OK", BOOL, default=True),
]

SETTINGS_BY_KEY = {f.key: f for f in SETTINGS_FIELDS}
