# tests/test_mssql.py

| Name | Proves |
|---|---|
| `_f` | Helper: a fake remote file with a given age. |
| `test_infer_db_name` | Database names from maintenance-plan, bot and Ola Hallengren file names. |
| `test_group_by_pattern_newest_first` | One group per pattern, newest file first, readable label. |
| `test_split_by_database_and_subfolders` | Grouping by database name, falling back to the sub-folder. |
| `test_holoo_databases_kept_apart` | Holoo1 and Holoo2 are separate groups with their own newest file. |
| `test_group_by_folder_and_path_patterns` | Grouping by sub-folder (Arpa/ArpaEn) and patterns that match paths. |
| `test_labels_from_patterns` | Labels come from the pattern prefix, folder or database name. |
| `test_holoo_real_naming_with_patterns` | Holoo's real file names (`Holoo1_050717_20-29-40_…zip`) group correctly and `AutoHoloo*` files are left alone. |
