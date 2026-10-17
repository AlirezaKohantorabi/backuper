# tests/test_self_backup.py

The bot backing itself up ([sources/self_backup.py](../backuper/sources/self_backup.md)).

| Test | Checks |
|---|---|
| `test_bot_backs_up_itself` | The zip holds `.env`, `RESTORE.txt`, `data/backuper.db`, `local/company.yaml`; the database copy opens and contains the sources; it's verified; the temporary copy is removed. |
| `test_self_backup_test_lists_what_it_takes` | 🧪 Test lists the database and `.env`. |
