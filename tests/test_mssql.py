import time

from backuper.sources.mssql_common import group_files, infer_db_name, label_for
from backuper.transport.smb import RemoteFile


def test_infer_db_name():
    assert infer_db_name("Sales_backup_2026_10_07_010001_1234567.bak") == "Sales"
    assert infer_db_name("Hamrahan_1405-07-15_0300.bak") == "Hamrahan"
    assert infer_db_name("SRV_Cash_FULL_20261007_010000.bak") == "SRV_Cash"
    assert infer_db_name("Project 20261007.bak") == "Project"
    assert infer_db_name("nodate.bak") is None


def _f(name: str, age_h: float, rel: str | None = None) -> RemoteFile:
    return RemoteFile(path="\\\\h\\s\\" + (rel or name), rel=rel or name, size=100, mtime=time.time() - age_h * 3600)


def test_group_by_pattern_newest_first():
    files = [_f("Cash_backup_2026_10_05_010000_1.bak", 48), _f("Cash_backup_2026_10_06_010000_1.bak", 24),
             _f("Project_backup_2026_10_06_010000_1.bak", 24), _f("notes.txt", 1)]
    groups = group_files(files, ["Cash_*.bak", "Project_*.bak"], "pattern")
    assert set(groups) == {"Cash_*.bak", "Project_*.bak"}
    assert groups["Cash_*.bak"][0].name.endswith("10_06_010000_1.bak")
    assert label_for("Cash_*.bak", groups["Cash_*.bak"][0]) == "Cash"


def test_split_by_database_and_subfolders():
    files = [_f("Cash_backup_2026_10_06_010000_1.bak", 24), _f("Project_backup_2026_10_06_010000_1.bak", 24),
             _f("weird.bak", 2, rel="Arpa\\weird.bak")]
    groups = group_files(files, ["*.bak"], "database")
    assert set(groups) == {"Cash", "Project", "Arpa"}


def test_holoo_databases_kept_apart():
    files = [_f(f"Holoo{n}_1405-07-{d:02d}.bak", 24 * (20 - d)) for n in (1, 2) for d in range(10, 18)]
    groups = group_files(files, ["*.bak"], "database")
    assert set(groups) == {"Holoo1", "Holoo2"} and all(len(v) == 8 for v in groups.values())
    assert groups["Holoo1"][0].name == "Holoo1_1405-07-17.bak"  # newest first


def test_group_by_folder_and_path_patterns():
    files = [_f("a.bak", 30, rel="Arpa\\a.bak"), _f("b.bak", 5, rel="Arpa\\b.bak"),
             _f("c.bak", 5, rel="ArpaEn\\c.bak"), _f("x.txt", 1, rel="ArpaEn\\x.txt")]
    groups = group_files(files, ["*.bak"], "folder")
    assert set(groups) == {"Arpa", "ArpaEn"} and groups["Arpa"][0].name == "b.bak"
    only_en = group_files(files, ["ArpaEn/*.bak"], "pattern")
    assert [f.name for f in only_en["ArpaEn/*.bak"]] == ["c.bak"]
