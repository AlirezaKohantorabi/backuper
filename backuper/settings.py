"""Runtime settings editable from the bot (stored in the database)."""

from __future__ import annotations

from backuper.fields import BOOL, CHOICE, CREDENTIAL, INT, SECRET, TEXT, Field

FOLDER_FORMATS = {"1405-07-15": "%Y-%m-%d", "1405_07_15": "%Y_%m_%d", "1405.07.15": "%Y.%m.%d"}

SETTINGS_FIELDS: list[Field] = [
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
    Field("cloud_type", "Cloud copy", CHOICE, default="off", choices=("off", "drive", "local"),
          help="Off-site copy of the newest zips (replaces Telegram): 'drive' = Google Drive (sign in with "
               "`backuper cloud login`), 'local' = a folder on this server such as a mounted USB disk. "
               "Plain folder copies (Edari/Finance) are never sent."),
    Field("cloud_credential", "Cloud sign-in", CREDENTIAL, required=False, default="",
          help="Credential holding the Google Drive sign-in (made by `backuper cloud login`)."),
    Field("cloud_folder", "Cloud folder", TEXT, required=False, default="Backuper",
          help="Drive: the folder name (created by the bot). Local: an absolute path, e.g. /mnt/usb/Backuper."),
    Field("cloud_keep_days", "Cloud days to keep", INT, default=14, min=1, max=3650,
          help="How many of the newest date folders are kept in the cloud; older ones are deleted there."),
    Field("retry_failed_after", "Retry failed sources after (min)", INT, default=60, min=0, max=24 * 60,
          help="A scheduled run's failed sources are tried again this many minutes later (power cut, network "
               "outage, server reboot…). 0 = off."),
    Field("retry_failed_times", "Retries of failed sources", INT, default=3, min=0, max=10,
          help="How many times a failed source is retried after one scheduled run."),
    Field("notify_admins", "DM admins on failure", BOOL, default=True),
    Field("report_on_success", "Post report when all OK", BOOL, default=True),
]

SETTINGS_BY_KEY = {f.key: f for f in SETTINGS_FIELDS}
