import sqlite3

from backuper.sources import get_type


def test_credentials_and_secrets_are_encrypted_at_rest(store, cfg):
    store.save_credential("router", "bot", "p@ss-ROUTER")
    cls = get_type("mikrotik")
    sid = store.create_source("Core", "mikrotik", {"host": "10.0.0.1", "credential": "router",
                                                    "backup_password": "TOP-SECRET"}, cls.secret_keys())
    store.set_setting("zip_password", "ZIP-SECRET")

    raw = sqlite3.connect(cfg.db_path)
    dump = "\n".join(str(r) for t in ("credentials", "sources", "settings")
                     for r in raw.execute(f"SELECT * FROM {t}"))  # noqa: S608
    for plain in ("p@ss-ROUTER", "TOP-SECRET", "ZIP-SECRET"):
        assert plain not in dump

    assert store.get_credential_by_name("router").password == "p@ss-ROUTER"
    assert store.get_source(sid).config["backup_password"] == "TOP-SECRET"
    assert store.get_setting("zip_password") == "ZIP-SECRET"


def test_settings_defaults_and_credential_rename(store):
    assert store.get_setting("archive_keep_days") == 14
    store.save_credential("old", "u", "p")
    store.create_source("S", "smb_folder", {"path": r"\\h\s", "credential": "old"}, set())
    store.set_setting("archive_credential", "old")
    cid = store.get_credential_by_name("old").id
    store.save_credential("new", "u", None, cred_id=cid)
    from backuper.sources import credential_keys_by_type

    store.rename_credential_refs("old", "new", credential_keys_by_type())
    assert store.get_source_by_name("S").config["credential"] == "new"
    assert store.get_setting("archive_credential") == "new"
    assert store.get_credential(cid).password == "p"  # password kept when None
