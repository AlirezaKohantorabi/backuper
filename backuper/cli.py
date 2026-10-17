"""Command line entry point: `backuper <command>`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import shutil
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

from backuper import __version__, control, jalali, logctx
from backuper.config import AppConfig, ConfigError, load_config
from backuper.crypto import SecretBox
from backuper.db import Store
from backuper.fields import FieldError, parse_values
from backuper.settings import SETTINGS_BY_KEY
from backuper.util import human_size

log = logging.getLogger("backuper")


def setup_logging(cfg: AppConfig, console_level: str | None = None) -> None:
    """Console (journald) + rotating file at LOG_LEVEL; every line tagged [run#N source].
    The root logger lets DEBUG through so each run's own log file (see logctx) gets the details."""
    logctx.install()
    fmt = logging.Formatter(logctx.FORMAT)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.setLevel(console_level or cfg.log_level)
    fileh = RotatingFileHandler(cfg.log_dir / "backuper.log", maxBytes=10 * 1024 * 1024, backupCount=5,
                                encoding="utf-8")
    fileh.setFormatter(fmt)
    fileh.setLevel(cfg.log_level)
    root.handlers[:] = [stream, fileh]
    for noisy in ("telethon", "smbprotocol", "spnego", "asyncssh", "httpx", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Windows resets the TCP connection when we close it: "socket aborted by peer … treating as EOF" is normal.
    logging.getLogger("smbprotocol.transport").setLevel(logging.ERROR)


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
    from backuper.status import StatusWriter

    engine = Engine(cfg, store, None)
    engine.startup_cleanup()
    control.clear_request(cfg.data_dir)
    scheduler = Scheduler(engine, store, cfg.timezone)
    scheduler.start()
    status = asyncio.create_task(StatusWriter(cfg, store, engine, scheduler).run())
    leftovers = asyncio.create_task(engine.clean_archive_leftovers())
    try:
        await control.wait_for_restart(cfg.data_dir, engine)  # returning ends the process; systemd restarts it
    finally:
        status.cancel()
        leftovers.cancel()
        scheduler.shutdown()


def cmd_run(args) -> int:
    """One-off run from the shell (uploads with the bot if Telegram is configured, else archive-only)."""
    from backuper.engine import Engine

    cfg = load_config(args.env, require_telegram=False)
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
        client = None
        sender = None
        if cfg.bot_token and cfg.api_id and cfg.api_hash:
            from backuper.bot.app import make_client
            from backuper.destinations.telegram import TelegramSender

            client = make_client(cfg)
            await client.start(bot_token=cfg.bot_token)
            sender = TelegramSender(client, store, lambda: set(cfg.owner_ids) | store.admin_ids())
        else:
            print("Telegram not configured: archive-only run", file=sys.stderr)
        try:
            run_id = await Engine(cfg, store, sender).run(ids, trigger="cli")
            print(store.get_run(run_id)["summary"])
            return 0 if store.get_run(run_id)["status"] in ("ok", "warning") else 1
        finally:
            if client:
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
        ctx = RunContext(store, src, workdir, jalali.now_jalali(cfg.timezone), app=cfg)
        try:
            result = asyncio.run(asyncio.wait_for(get_type(src.type)(src, ctx).test(), 180))
            print(f"✓ {name}: {result}")
        except Exception as e:  # noqa: BLE001
            print(f"✗ {name}: {e.__class__.__name__}: {e}")
            rc = 1
        finally:
            ctx.close()
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
        print("Restart the service (backuper restart) to load new schedules.")
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


def cmd_log(args) -> int:
    """Print a run's detailed log (default: the newest run)."""
    cfg = load_config(args.env, require_telegram=False)
    run_id = args.run
    if run_id is None:
        runs = open_store(cfg).list_runs(1)
        if not runs:
            print("No runs yet.", file=sys.stderr)
            return 1
        run_id = runs[0]["id"]
    path = logctx.run_log_path(cfg.log_dir, run_id)
    if not path.exists():
        print(f"No log for run #{run_id} ({path})", file=sys.stderr)
        return 1
    lines = path.read_text(encoding="utf-8").splitlines()
    print("\n".join(lines[-args.tail:] if args.tail else lines))
    return 0


def cmd_status(args) -> int:
    """Print the live status file the service writes (what's running right now, last and next runs)."""
    cfg = load_config(args.env, require_telegram=False)
    path = cfg.data_dir / "status.txt"
    if not path.exists():
        print("No status yet — is the service running? (sudo systemctl status backuper)", file=sys.stderr)
        return 1
    age = time.time() - path.stat().st_mtime
    print(path.read_text(encoding="utf-8"))
    if age > 15 * 60:
        print(f"⚠️ This file is {int(age // 60)} minutes old — the service may not be running.", file=sys.stderr)
    return 0


CLOUD_CREDENTIAL = "google-drive"


def cmd_cloud(args) -> int:
    """Cloud copy: `login` (Google Drive sign-in), `test`, `sync` (upload what's missing now), `ls`."""
    import getpass
    import json

    from backuper.destinations.archive import Archive
    from backuper.destinations.cloud import Cloud, CloudError, drive_authorize_command

    cfg = load_config(args.env, require_telegram=False)
    setup_logging(cfg, console_level="WARNING" if args.action != "sync" else None)
    store = open_store(cfg)
    cloud = Cloud(store, Archive(store))

    if args.action == "login":
        print("Google Drive sign-in for the cloud copy.\n")
        client_id = input("Your own Google OAuth client ID (Enter = rclone's shared one): ").strip()
        client_secret = getpass.getpass("Its client secret (input hidden): ").strip() if client_id else ""
        print("\nOn a PC with a web browser:\n"
              "  1. download rclone from https://rclone.org/downloads/ (Windows: unzip, open a terminal there)\n"
              f"  2. run:   {drive_authorize_command(client_id, client_secret)}\n"
              "  3. sign in with the company's Google account and allow access\n"
              "  4. copy everything rclone prints between the ---> and <--- lines\n")
        raw = getpass.getpass("Paste it here (input hidden): ").strip()
        try:
            token = json.loads(raw)
            if not token.get("refresh_token"):
                raise ValueError("no refresh_token")
        except (ValueError, AttributeError) as e:
            print(f"That isn't the token rclone prints ({e}). Nothing saved.", file=sys.stderr)
            return 1
        blob = {"token": token, **({"client_id": client_id, "client_secret": client_secret} if client_id else {})}
        store.save_credential(CLOUD_CREDENTIAL, "Google Drive", json.dumps(blob),
                              "rclone token for the cloud copy (backuper cloud login)")
        store.set_setting("cloud_credential", CLOUD_CREDENTIAL)
        print(f"Saved (credential '{CLOUD_CREDENTIAL}', encrypted). Testing…")
        args.action = "test"

    async def main() -> int:
        try:
            if args.action == "test":
                print(await cloud.test(kind="drive" if cloud.kind == "off" else None))
                if cloud.kind == "off":
                    print("Uploads are still OFF. Switch them on when the storage is ready:  backuper set cloud_type drive")
            elif args.action == "sync":
                if not cloud.enabled:
                    print("The cloud copy is off (backuper set cloud_type drive).", file=sys.stderr)
                    return 1
                excluded = {s.name for s in store.list_sources() if not s.config.get("cloud", True)}
                res = await cloud.sync(excluded)
                print(res.summary())
                for rel, err in res.failed.items():
                    print(f"   ✗ {rel}: {err}")
                return 1 if res.failed else 0
            elif args.action == "ls":
                present = await cloud._list_cloud(await asyncio.to_thread(cloud._env, cloud.kind if cloud.enabled else "drive"))  # noqa: SLF001
                by_day: dict[str, list[int]] = {}
                for rel, (size, _) in present.items():
                    by_day.setdefault(rel.split("/", 1)[0], []).append(size)
                for day in sorted(by_day):
                    print(f"{day}: {len(by_day[day])} file(s), {human_size(sum(by_day[day]))}")
                print(f"{sum(len(v) for v in by_day.values())} file(s), {human_size(sum(sum(v) for v in by_day.values()))} in the cloud.")
            return 0
        except CloudError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1

    return asyncio.run(main())


def cmd_once(args) -> int:
    """Schedule, list or cancel one-time runs (picked up by the running service within 30 s)."""
    cfg = load_config(args.env, require_telegram=False)
    store = open_store(cfg)
    tz = cfg.timezone
    if args.cancel:
        o = store.get_one_off(args.cancel)
        if o is None or o.status != "pending":
            print(f"No pending one-time run #{args.cancel}.", file=sys.stderr)
            return 1
        store.set_one_off(o.id, "cancelled")
        print(f"Cancelled #{o.id}: {o.label}")
        return 0
    if args.list or not args.when:
        pending = store.list_one_offs()
        for o in pending:
            print(f"#{o.id}  {jalali.pretty(jalali.to_jalali(o.run_at.astimezone(tz)))}  {o.label}")
        print(f"{len(pending)} pending one-time run(s).")
        return 0
    if not args.names:
        print("Name the sources or groups to run, e.g.: backuper once 22:00 Hamrahan-Manual", file=sys.stderr)
        return 2
    try:
        when = jalali.parse_when(args.when, tz)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    groups = {g.name.lower(): g for g in store.list_groups()}
    ids: list[int] = []
    for name in args.names:
        src = store.get_source_by_name(name)
        if src is not None:
            ids.append(src.id)
        elif name.lower() in groups:
            ids += [s.id for s in store.list_sources(enabled_only=True)
                    if (s.config.get("group") or "").lower() == name.lower()]
        else:
            print(f"No source or group named {name!r} (see: backuper list).", file=sys.stderr)
            return 2
    ids = list(dict.fromkeys(ids))
    oid = store.add_one_off(", ".join(args.names), ids, when, None)
    print(f"⏱ #{oid} at {jalali.pretty(jalali.to_jalali(when))}: {', '.join(args.names)} ({len(ids)} source(s)). "
          "The service picks it up within 30 seconds; cancel with: backuper once --cancel " + str(oid))
    return 0


def cmd_verify(args) -> int:
    """Unzip archived zips again (in memory) and check them with the zip's own AES authentication / CRC."""
    from backuper.destinations.archive import Archive
    from backuper.util import human_duration, short_error

    cfg = load_config(args.env, require_telegram=False)
    setup_logging(cfg, console_level="WARNING")
    store = open_store(cfg)
    archive = Archive(store)
    password = store.get_setting("zip_password") or ""

    async def main() -> int:
        folders = await archive.list_date_folders()
        todo = folders if args.all else [args.folder] if args.folder else folders[-1:]
        ok = bad = 0
        for folder in todo:
            print(f"{folder}:")
            for name in args.name or await archive.list_zips(folder):
                t0 = time.time()
                try:
                    n = await archive.verify(f"{folder}/{name}", None, password=password)
                    ok += 1
                    print(f"   ✓  {name}: {n} file(s) unzip correctly ({human_duration(time.time() - t0)})")
                except Exception as e:  # noqa: BLE001
                    bad += 1
                    print(f"   ✗  {name}: {short_error(e)}")
        print(f"{ok} zip(s) OK, {bad} damaged.")
        return 1 if bad else 0

    return asyncio.run(main())


def cmd_restart(args) -> int:
    """Ask the running service to restart (when no backup is running, or --now)."""
    cfg = load_config(args.env, require_telegram=False)
    control.request_restart(cfg.data_dir, now=args.now, by=f"{getpass.getuser()} (command line)")
    print("Restart requested" + (" — now." if args.now else " — as soon as no backup is running."))
    deadline = time.time() + 3 * control.POLL_SECONDS
    while time.time() < deadline:
        time.sleep(1)
        req = control.pending_request(cfg.data_dir)
        if req is None:
            print("✓ The service is restarting — it's back in about 20 seconds (check: backuper status).")
            return 0
        if req.get("waiting"):
            print("⏳ Backups are running — the service restarts when they finish. "
                  "`backuper restart --now` stops them instead.")
            return 0
    control.clear_request(cfg.data_dir)
    print("✗ The service didn't react — it doesn't seem to be running. Start it with: "
          "sudo systemctl start backuper", file=sys.stderr)
    return 1


def cmd_list(args) -> int:
    from backuper.sources import REGISTRY

    cfg = load_config(args.env, require_telegram=False)
    store = open_store(cfg)
    for s in store.list_sources():
        cls = REGISTRY.get(s.type)
        print(f"{'✓' if s.enabled else '·'} {s.name:<24} {s.type:<15} {cls.describe(s.config) if cls else '?'}")
    return 0


def default_env() -> str | None:
    """$BACKUPER_ENV, else ./.env if present, else the .env of the installation this program belongs to
    (so `backuper status` works from any folder)."""
    if os.environ.get("BACKUPER_ENV"):
        return os.environ["BACKUPER_ENV"]
    if (Path.cwd() / ".env").exists():
        return str(Path.cwd() / ".env")
    app = Path(__file__).resolve().parents[1] / ".env"
    return str(app) if app.exists() else None


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="backuper", description="Backup collector with a Telegram admin panel")
    p.add_argument("--env", help="path to the .env file (default: $BACKUPER_ENV, ./.env, or the one next to the "
                                 "program)")
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
    sub.add_parser("status", help="what's running right now (live progress), last and next runs")
    lg = sub.add_parser("log", help="show a run's detailed log (default: newest run)")
    lg.add_argument("run", nargs="?", type=int)
    lg.add_argument("--tail", type=int, default=0, help="only the last N lines")
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
    cl = sub.add_parser("cloud", help="cloud copy: login (Google Drive), test, sync (upload now), ls")
    cl.add_argument("action", choices=("login", "test", "sync", "ls"))
    on = sub.add_parser("once", help="run sources/groups once at a given time (or --list / --cancel ID)")
    on.add_argument("when", nargs="?", help="14:30 · '1405/07/25 22:00' · '2026-10-17 22:00' · +30m · +2h")
    on.add_argument("names", nargs="*", help="source or group names")
    on.add_argument("--list", action="store_true", help="show pending one-time runs")
    on.add_argument("--cancel", type=int, metavar="ID", help="cancel a pending one-time run")
    vf = sub.add_parser("verify", help="unzip archived zips again and check them (default: newest date folder)")
    vf.add_argument("folder", nargs="?", help="date folder, e.g. 1405-07-25")
    vf.add_argument("name", nargs="*", help="only these zips")
    vf.add_argument("--all", action="store_true", help="every date folder in the archive")
    rs = sub.add_parser("restart", help="restart the service (after running backups finish, or --now)")
    rs.add_argument("--now", action="store_true", help="don't wait — stop running backups")
    args = p.parse_args(argv)
    args.env = args.env or default_env()

    handlers = {"serve": cmd_serve, "gen-key": cmd_gen_key, "run": cmd_run, "test": cmd_test, "check": cmd_check,
                "list": cmd_list, "import": cmd_import, "export": cmd_export, "credential": cmd_credential,
                "set": cmd_set, "log": cmd_log, "status": cmd_status, "restart": cmd_restart, "verify": cmd_verify,
                "once": cmd_once, "cloud": cmd_cloud, None: cmd_serve}
    try:
        sys.exit(handlers[args.cmd](args))
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(2)
