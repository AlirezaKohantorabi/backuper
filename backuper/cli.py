"""Command line entry point: `backuper <command>`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

from backuper import __version__, jalali
from backuper.config import AppConfig, ConfigError, load_config
from backuper.crypto import SecretBox
from backuper.db import Store
from backuper.fields import FieldError, parse_values
from backuper.settings import SETTINGS_BY_KEY

log = logging.getLogger("backuper")


def setup_logging(cfg: AppConfig) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(cfg.log_level)
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    fileh = RotatingFileHandler(cfg.log_dir / "backuper.log", maxBytes=10 * 1024 * 1024, backupCount=5,
                                encoding="utf-8")
    fileh.setFormatter(fmt)
    root.handlers[:] = [stream, fileh]
    for noisy in ("telethon", "smbprotocol", "spnego", "asyncssh", "httpx", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def open_store(cfg: AppConfig) -> Store:
    return Store(cfg.db_path, SecretBox(cfg.secret_key))


# -- commands -----------------------------------------------------------------------

def cmd_gen_key(_args) -> int:
    print(SecretBox.generate_key())
    return 0


def cmd_serve(args) -> int:
    cfg = load_config(args.env, require_telegram=False)
    setup_logging(cfg)
    store = open_store(cfg)
    log.info("Backuper %s starting (data: %s)", __version__, cfg.data_dir)
    try:
        if cfg.bot_token and cfg.api_id and cfg.api_hash:
            from backuper.bot.app import BotApp

            asyncio.run(BotApp(cfg, store).run())
        else:
            asyncio.run(_serve_archive_only(cfg, store))
    except KeyboardInterrupt:
        pass
    return 0


async def _serve_archive_only(cfg: AppConfig, store: Store) -> None:
    """No Telegram configured yet: still run the schedules and fill the archive."""
    from backuper.engine import Engine
    from backuper.scheduler import Scheduler

    log.warning("BOT_TOKEN/API_ID/API_HASH not all set — running ARCHIVE-ONLY: schedules run and zips go to "
                "the archive, but nothing is uploaded and there is no admin panel. Fill .env and restart to "
                "enable Telegram.")
    engine = Engine(cfg, store, None)
    engine.startup_cleanup()
    scheduler = Scheduler(engine, store, cfg.timezone)
    scheduler.start()
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()


def cmd_run(args) -> int:
    """One-off run from the shell (uses the same Telegram bot to upload)."""
    from backuper.bot.app import make_client
    from backuper.destinations.telegram import TelegramSender
    from backuper.engine import Engine

    cfg = load_config(args.env)
    setup_logging(cfg)
    store = open_store(cfg)
    ids = None
    if args.source:
        ids = []
        for name in args.source:
            src = store.get_source_by_name(name)
            if not src:
                print(f"no such source: {name}", file=sys.stderr)
                return 2
            ids.append(src.id)

    async def main() -> int:
        client = make_client(cfg)
        await client.start(bot_token=cfg.bot_token)
        try:
            sender = TelegramSender(client, store, lambda: set(cfg.owner_ids) | store.admin_ids())
            engine = Engine(cfg, store, sender)
            run_id = await engine.run(ids, trigger="cli")
            print(store.get_run(run_id)["summary"])
            return 0 if store.get_run(run_id)["status"] in ("ok", "warning") else 1
        finally:
            await client.disconnect()

    return asyncio.run(main())


def cmd_test(args) -> int:
    from backuper.sources import RunContext, get_type

    cfg = load_config(args.env, require_telegram=False)
    setup_logging(cfg)
    store = open_store(cfg)
    names = args.source or [s.name for s in store.list_sources()]
    rc = 0
    for name in names:
        src = store.get_source_by_name(name)
        if not src:
            print(f"✗ {name}: no such source")
            rc = 1
            continue
        workdir = cfg.staging_dir / f"test-{src.id}"
        try:
            ctx = RunContext(store, src, workdir, jalali.now_jalali(cfg.timezone))
            result = asyncio.run(asyncio.wait_for(get_type(src.type)(src, ctx).test(), 180))
            print(f"✓ {name}: {result}")
        except Exception as e:  # noqa: BLE001
            print(f"✗ {name}: {e.__class__.__name__}: {e}")
            rc = 1
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return rc


def cmd_check(args) -> int:
    from backuper.destinations.archive import Archive

    cfg = load_config(args.env, require_telegram=False)
    setup_logging(cfg)
    store = open_store(cfg)
    ok = True
    print(f"Data dir:   {cfg.data_dir}")
    print(f"Staging:    {cfg.staging_dir} ({shutil.disk_usage(cfg.staging_dir).free / 2**30:.1f} GB free)")
    print(f"Timezone:   {cfg.timezone.key} — today is {jalali.pretty(jalali.now_jalali(cfg.timezone))}")
    print(f"Owners:     {', '.join(map(str, sorted(cfg.owner_ids))) or '⚠️ none'}")
    print(f"Telegram:   {'configured' if cfg.bot_token and cfg.api_id and cfg.api_hash else '⚠️ BOT_TOKEN/API_ID/API_HASH missing'}")
    print(f"Sources:    {len(store.list_sources())}, credentials: {len(store.list_credentials())}")
    print(f"Zip pass:   {'set' if store.get_setting('zip_password') else '⚠️ not set (zips unencrypted)'}")
    try:
        print("Archive:    " + asyncio.run(Archive(store).test()))
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"Archive:    ✗ {e}")
    return 0 if ok else 1


def cmd_import(args) -> int:
    from backuper.sources import get_type

    cfg = load_config(args.env, require_telegram=False)
    store = open_store(cfg)
    doc = yaml.safe_load(Path(args.file).read_text(encoding="utf-8")) or {}
    errors: list[str] = []

    settings = doc.get("settings") or {}
    for key, raw in settings.items():
        f = SETTINGS_BY_KEY.get(key)
        if f is None:
            errors.append(f"settings.{key}: unknown setting")
            continue
        try:
            value = f.parse(raw)
            if not args.dry_run:
                store.set_setting(key, value)
        except FieldError as e:
            errors.append(f"settings.{key}: {e}")

    from backuper.fields import TIME, Field, parse_schedule

    group_names = {g.name.lower() for g in store.list_groups()}
    for g in doc.get("groups") or []:
        try:
            if not g.get("name") or not g.get("schedule"):
                raise FieldError("needs 'name' and 'schedule'")
            schedule = parse_schedule(str(g["schedule"]))
            upload_at = Field("upload_at", "upload_at", TIME, required=False, default="").parse(g.get("upload_at"))
            group_names.add(g["name"].lower())
            if not args.dry_run:
                store.save_group(g["name"], schedule, enabled=bool(g.get("enabled", True)), upload_at=upload_at)
        except FieldError as e:
            errors.append(f"groups.{g.get('name', '?')}: {e}")

    cred_names = {c.name.lower() for c in store.list_credentials()}
    for c in doc.get("credentials") or []:
        if not c.get("name"):
            errors.append("credential without a name")
            continue
        cred_names.add(c["name"].lower())
        existing = store.get_credential_by_name(c["name"])
        password = c.get("password")
        if not args.dry_run:
            store.save_credential(c["name"], c.get("username", ""),
                                  password if password else (None if existing else ""), c.get("note", ""))

    for s in doc.get("sources") or []:
        name, type_id = s.get("name"), s.get("type")
        label = f"sources.{name or '?'}"
        try:
            if not name or not type_id:
                raise FieldError("needs 'name' and 'type'")
            cls = get_type(type_id)
            raw = {k: v for k, v in s.items() if k not in ("name", "type", "enabled")}
            existing = store.get_source_by_name(name)
            if existing and existing.type == type_id:
                values = {**existing.config, **parse_values(cls.all_fields(), raw, partial=True)}
            else:
                values = parse_values(cls.all_fields(), raw)
            for k in cls.credential_keys():
                if values.get(k) and values[k].lower() not in cred_names:
                    raise FieldError(f"{k}: credential '{values[k]}' doesn't exist")
            if values.get("group") and values["group"].lower() not in group_names:
                raise FieldError(f"group '{values['group']}' doesn't exist (add it under groups:)")
            if args.dry_run:
                continue
            enabled = bool(s.get("enabled", True))
            if existing:
                store.update_source(existing.id, config=values, secret_keys=cls.secret_keys(), enabled=enabled,
                                    type_id=type_id)
            else:
                store.create_source(name, type_id, values, cls.secret_keys(), enabled=enabled)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label}: {e}")

    for e in errors:
        print(f"✗ {e}", file=sys.stderr)
    print(("Validated" if args.dry_run else "Imported") +
          f" {len(settings)} setting(s), {len(doc.get('groups') or [])} group(s), "
          f"{len(doc.get('credentials') or [])} credential(s), "
          f"{len(doc.get('sources') or [])} source(s) with {len(errors)} error(s).")
    if not args.dry_run:
        print("Restart the service (sudo systemctl restart backuper) to load new schedules.")
    return 1 if errors else 0


def cmd_export(args) -> int:
    from backuper.sources import REGISTRY

    cfg = load_config(args.env, require_telegram=False)
    store = open_store(cfg)
    doc = {
        "settings": {k: v for k, v in store.all_settings().items()
                     if not SETTINGS_BY_KEY[k].secret and k != "chat_id"},
        "groups": [{"name": g.name, "schedule": g.schedule, "upload_at": g.upload_at, "enabled": g.enabled}
                   for g in store.list_groups()],
        "credentials": [{"name": c.name, "username": c.username, "password": "", "note": c.note}
                        for c in store.list_credentials()],
        "sources": [],
    }
    for s in store.list_sources():
        cls = REGISTRY.get(s.type)
        secrets = cls.secret_keys() if cls else set()
        doc["sources"].append({"name": s.name, "type": s.type, "enabled": s.enabled,
                               **{k: ("" if k in secrets else v) for k, v in s.config.items()}})
    text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120)
    if args.file:
        Path(args.file).write_text(text, encoding="utf-8")
        print(f"Wrote {args.file} (passwords are not exported)")
    else:
        print(text)
    return 0


def cmd_credential(args) -> int:
    """Create/update a credential from the terminal (password typed hidden, never echoed)."""
    import getpass

    cfg = load_config(args.env, require_telegram=False)
    store = open_store(cfg)
    existing = store.get_credential_by_name(args.name)
    username = args.username or (existing.username if existing else "") or input("Username: ").strip()
    password = getpass.getpass(f"Password for {username} (input hidden): ")
    if not password and not existing:
        print("Empty password — nothing saved.", file=sys.stderr)
        return 1
    store.save_credential(args.name, username, password or None, existing.note if existing else "")
    print(f"Saved credential '{args.name}' ({username}).")
    return 0


def cmd_set(args) -> int:
    """Change a setting from the terminal; secret settings are prompted hidden."""
    import getpass

    cfg = load_config(args.env, require_telegram=False)
    store = open_store(cfg)
    f = SETTINGS_BY_KEY.get(args.key)
    if f is None:
        print(f"Unknown setting. Choose from: {', '.join(SETTINGS_BY_KEY)}", file=sys.stderr)
        return 2
    raw = args.value if args.value is not None else (
        getpass.getpass(f"{f.label} (input hidden): ") if f.secret else input(f"{f.label}: "))
    try:
        store.set_setting(f.key, f.parse(raw))
    except FieldError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(f"{f.label} = {f.display(store.get_setting(f.key))}")
    return 0


def cmd_list(args) -> int:
    from backuper.sources import REGISTRY

    cfg = load_config(args.env, require_telegram=False)
    store = open_store(cfg)
    for s in store.list_sources():
        cls = REGISTRY.get(s.type)
        print(f"{'✓' if s.enabled else '·'} {s.name:<24} {s.type:<15} {cls.describe(s.config) if cls else '?'}")
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="backuper", description="Backup collector with a Telegram admin panel")
    p.add_argument("--env", help="path to the .env file (default: ./.env)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the bot + scheduler (what the service runs)")
    sub.add_parser("gen-key", help="print a new SECRET_KEY")
    r = sub.add_parser("run", help="run backups once now")
    r.add_argument("source", nargs="*", help="source names (default: all enabled)")
    t = sub.add_parser("test", help="test source connectivity")
    t.add_argument("source", nargs="*")
    sub.add_parser("check", help="check configuration and archive access")
    sub.add_parser("list", help="list sources")
    i = sub.add_parser("import", help="import settings/credentials/sources from YAML")
    i.add_argument("file")
    i.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("credential", help="create/update a credential (password prompted, hidden)")
    c.add_argument("name")
    c.add_argument("--username", "-u")
    st = sub.add_parser("set", help="change a setting (secrets prompted, hidden)")
    st.add_argument("key")
    st.add_argument("value", nargs="?")
    e = sub.add_parser("export", help="export configuration to YAML (without passwords)")
    e.add_argument("file", nargs="?")
    args = p.parse_args(argv)

    handlers = {"serve": cmd_serve, "gen-key": cmd_gen_key, "run": cmd_run, "test": cmd_test, "check": cmd_check,
                "list": cmd_list, "import": cmd_import, "export": cmd_export, "credential": cmd_credential,
                "set": cmd_set, None: cmd_serve}
    try:
        sys.exit(handlers[args.cmd](args))
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(2)
