#!/usr/bin/env python3
"""
zk_backup.py - read-only, verified, schedulable network backup for ZKTeco terminals.

HOW IT STAYS SAFE
  1. Read-only guard. Every pyzk function ends in one private method that builds and sends a
     protocol packet. This script wraps that method with a whitelist of *read* commands.
     Anything else (clear logs, delete user, set time, restart, freeze device, ...) raises
     WriteBlocked before a packet is even built. A self-test proves the guard is armed before
     the script touches the network; if pyzk's internals ever change, the script refuses to run.
  2. The device is never frozen (no disable_device). Reads go through the device's own transfer
     buffer (it snapshots the table, we copy the snapshot). Consistency is proven by reading the
     device's record counters before and after each table read; if a punch lands mid-read, that
     attempt is thrown away and retried.
  3. A snapshot folder only appears once every file in it is written, fsynced and checksummed.
  4. attendance_master.csv keeps every punch ever seen, even if something later wipes the device.

OUTPUT (in --out)
  attendance_master.csv     every punch ever seen, deduplicated, sorted
  snapshots/<timestamp>/    info.json users.csv attendance.csv [templates.json faces.json photos/] SHA256SUMS
  state.json                last good run's counters (to detect wipes, removed users, device swaps)
  last_run.json             status of the latest run (ok/warning/error, counts, warnings) for bots
  backup.log

EXIT CODES
  0 ok (check log for warnings)   1 connect/read error   2 data failed verification
  3 another run in progress       4 safety guard could not be armed   5 internal error (bug)

Tested against pyzk 0.9:  pip install pyzk==0.9
"""
import argparse
import base64
import csv
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import sys
import time
from datetime import datetime
from struct import pack, unpack

__version__ = "2.1"

try:
    from zk import ZK, const
except ImportError:
    sys.exit("pyzk is not installed. Run: pip install pyzk==0.9")

EXIT_OK, EXIT_READ, EXIT_VERIFY, EXIT_LOCKED, EXIT_GUARD, EXIT_INTERNAL = 0, 1, 2, 3, 4, 5
STATUS = {EXIT_OK: "ok", EXIT_READ: "read_error", EXIT_VERIFY: "verify_failed", EXIT_GUARD: "guard_error",
          EXIT_INTERNAL: "internal_error"}
RESULT = {}   # filled by run(); written to last_run.json for schedulers/bots


class Collector(logging.Handler):
    """Keeps every WARNING/ERROR of this run so last_run.json can list them."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.items = []

    def emit(self, record):
        self.items.append({"level": record.levelname, "message": record.getMessage()})


def write_last_run(out, code, args, started, collector):
    """One JSON file a bot can read after each run: status, counts, snapshot path, warnings, errors."""
    warnings = [i["message"] for i in collector.items if i["level"] == "WARNING"]
    errors = [i["message"] for i in collector.items if i["level"] != "WARNING"]
    doc = {"status": "warning" if code == EXIT_OK and warnings else STATUS.get(code, "unknown"),
           "exit_code": code, "device_ip": args.ip, "started": started,
           "finished": datetime.now().isoformat(timespec="seconds"), "tool_version": __version__,
           **RESULT, "warnings": warnings, "errors": errors}
    path = os.path.join(out, "last_run.json")
    write_durable(path + ".tmp", json.dumps(doc, indent=2, ensure_ascii=False))
    os.replace(path + ".tmp", path)

# ------------------------------------------------------------------------------ read-only guard
# ZK protocol command codes (see zk/const.py). Only these are allowed to leave this machine.
READ_ONLY_COMMANDS = {
    const.CMD_CONNECT:        "open session",
    const.CMD_AUTH:           "send Comm Key",
    const.CMD_EXIT:           "close session",
    const.CMD_GET_VERSION:    "read firmware version",
    const.CMD_OPTIONS_RRQ:    "read one setting (serial, model, ...)",
    const.CMD_GET_FREE_SIZES: "read record counters",
    const.CMD_GET_TIME:       "read device clock",
    1503:                     "ask device to snapshot a table into its transfer buffer",
    1504:                     "read a chunk of that buffer",
    const.CMD_FREE_DATA:      "release the transfer buffer (frees RAM, deletes no records)",
    88:                       "read one stored template, slot 50 = face (--faces fallback)",
    150:                      "GetUserFace: read one user's face template (--faces)",
    10010:                    "DownloadUserPhoto: read one user's enrollment JPEG (--photos)",
}
# 150 (0x0096) and 10010 (0x271A) come from openzk's disassembly of ZKTeco's official SDK
# (github.com/kengggg/openzk). Not verified on every firmware: unsupported ones just answer an error.
# 1503 carries an inner command naming which table to snapshot. Only read tables are allowed.
BUFFERED_TABLES = {const.CMD_DB_RRQ, const.CMD_USERTEMP_RRQ, const.CMD_ATTLOG_RRQ}


class WriteBlocked(RuntimeError):
    pass


def arm_read_only_guard():
    original = getattr(ZK, "_ZK__send_command", None)
    if original is None:
        raise RuntimeError("pyzk internals changed (no _ZK__send_command); refusing to run "
                           "unguarded. Install the tested version: pip install pyzk==0.9")
    if getattr(original, "_guarded", False):
        return

    def guarded(self, command, command_string=b"", response_size=8):
        if command not in READ_ONLY_COMMANDS:
            raise WriteBlocked(f"blocked protocol command {command}: not on the read-only whitelist")
        if command == 1503:
            inner = unpack("<h", command_string[1:3])[0] if len(command_string) >= 3 else None
            if inner not in BUFFERED_TABLES:
                raise WriteBlocked(f"blocked buffered request for command {inner}")
        # pyzk stamps each request with reply_id + 1 and the device echoes it back.
        expected = self._ZK__reply_id + 1
        if expected >= const.USHRT_MAX:
            expected -= const.USHRT_MAX
        self._last_sent_id = expected
        if not getattr(self, "_strict_replies", False):
            return original(self, command, command_string, response_size)
        # Strict mode (face/photo reads): a different id means we're reading a stale answer to an
        # earlier, timed-out command, so stop instead of trusting it.
        result = original(self, command, command_string, response_size)
        got = self._ZK__header[3]
        if got != expected:
            raise ReplyMismatch(f"reply id {got} != expected {expected} (stale reply)")
        return result

    guarded._guarded = True
    ZK._ZK__send_command = guarded


class ReplyMismatch(RuntimeError):
    pass


def self_test_guard():
    """Prove destructive calls are blocked. Nothing is sent: the guard fires before the socket."""
    probe = ZK("192.0.2.1", ommit_ping=True)  # TEST-NET address, never contacted
    probe.is_connect = True                   # make pyzk believe it's connected so it reaches send
    destructive = {
        "clear_attendance": probe.clear_attendance,
        "clear_data": probe.clear_data,
        "disable_device": probe.disable_device,
        "delete_user": lambda: probe.delete_user(uid=1),
        "set_user": lambda: probe.set_user(uid=1, name="x", user_id="1"),
        "set_time": lambda: probe.set_time(datetime.now()),
        "restart": probe.restart,
        "poweroff": probe.poweroff,
    }
    for name, call in destructive.items():
        try:
            call()
        except WriteBlocked:
            continue
        except Exception as e:
            raise RuntimeError(f"guard self-test: {name}() raised {type(e).__name__} instead of being blocked")
        raise RuntimeError(f"guard self-test: {name}() was NOT blocked")


# ------------------------------------------------------------------------------ helpers
class Locked(RuntimeError):
    pass


class VerifyError(RuntimeError):
    pass


class RunLock:
    """Stops two runs (e.g. a manual one and the timer) from overlapping. Works on Linux and Windows."""

    def __init__(self, path, stale_after=2 * 3600):
        self.path, self.stale_after, self.held = path, stale_after, False

    def __enter__(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() - os.path.getmtime(self.path) < self.stale_after:
                raise Locked(self.path)
            os.remove(self.path)  # left behind by a crashed run
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}\n".encode())
        os.close(fd)
        self.held = True
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass


def write_durable(path, text):
    with open(path, "w", encoding="utf-8-sig" if path.endswith(".csv") else "utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def csv_text(header, rows):
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


USER_COLS = ["uid", "user_id", "name", "privilege", "password", "group_id", "card"]
ATT_COLS = ["user_id", "timestamp", "status", "punch", "uid"]
ATT_KEY = ("user_id", "timestamp", "status", "punch")   # identity of one punch
SNAP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}(-\d+)?$")


def user_row(u):
    return [u.uid, u.user_id, u.name, u.privilege, u.password, u.group_id, u.card]


def att_row(a):
    return [a.user_id, a.timestamp.strftime("%Y-%m-%d %H:%M:%S"), a.status, a.punch, a.uid]


# ------------------------------------------------------------------------------ reading
def consistent_read(conn, label, reader, counter, retries, log):
    """Read a table and prove it's complete: device counter before == after == rows read."""
    for attempt in range(1, retries + 1):
        try:
            conn.read_sizes()
            before = getattr(conn, counter)
            items = reader()
            conn.read_sizes()
            after = getattr(conn, counter)
        except WriteBlocked:
            raise
        except Exception as e:
            log.warning(f"{label}: attempt {attempt}/{retries} failed: {type(e).__name__}: {e}")
        else:
            if before == after == len(items):
                log.info(f"{label}: {len(items)} read, matches the device counter")
                return items
            log.warning(f"{label}: attempt {attempt}/{retries} inconsistent (device said {before}, "
                        f"read {len(items)}, device now says {after}) - probably a punch mid-read")
        if attempt < retries:
            time.sleep(5 * attempt)
    raise VerifyError(f"{label}: no consistent read after {retries} attempts")


def read_device_info(conn, log):
    def safe(fn):
        try:
            return fn()
        except WriteBlocked:
            raise
        except Exception as e:
            return f"unavailable ({e})"

    info = {
        "device_name": safe(conn.get_device_name),
        "serial": safe(conn.get_serialnumber),
        "firmware": safe(conn.get_firmware_version),
        "platform": safe(conn.get_platform),
        "face_version": safe(conn.get_face_version),
        "fp_version": safe(conn.get_fp_version),
    }
    try:
        host_now, dev_now = datetime.now(), conn.get_time()
        drift = round((dev_now - host_now).total_seconds())
        info.update(device_clock=str(dev_now), host_clock=str(host_now.replace(microsecond=0)),
                    clock_drift_seconds=drift)
        if abs(drift) > 120:
            log.warning(f"device clock is {drift:+d}s off this host - punches are stamped with the device clock")
    except WriteBlocked:
        raise
    except Exception as e:
        info["device_clock"] = f"unavailable ({e})"
    return info


FACE_SLOT = 50          # SDK convention: a user's face data lives in template slot 50
REPLY_NO_PHOTO = 0x1379  # device: "this user has no photo"


def raw_read(conn, command, payload):
    """Send one whitelisted read and collect a bulk reply (PREPARE_DATA -> DATA... -> ACK_OK).
    Uses pyzk's own receive code; checks the byte count against the size the device announced.
    Returns (reply_code, bytes or None)."""
    code = conn._ZK__send_command(command, payload, 1032)["code"]
    if code not in (const.CMD_DATA, const.CMD_PREPARE_DATA):
        return code, None
    declared = unpack("<I", conn._ZK__data[:4])[0] if code == const.CMD_PREPARE_DATA else None
    data = conn._ZK__recieve_chunk()
    if declared is not None and (data is None or len(data) != declared):
        raise VerifyError(f"command {command}: device announced {declared} bytes, received "
                          f"{0 if data is None else len(data)}")
    return code, data


def drain(conn):
    """After a timeout or stale reply: discard anything queued, and move the reply-id counter past
    every id already sent. (On a timeout pyzk doesn't advance it, so the next request would reuse
    the old id and a late answer to the old request would look valid.)"""
    sock = conn._ZK__sock
    old = sock.gettimeout()
    sock.settimeout(0.5)
    try:
        while sock.recv(65535):
            pass
    except Exception:
        pass
    finally:
        sock.settimeout(old)
    conn._ZK__reply_id = getattr(conn, "_last_sent_id", conn._ZK__reply_id)


def read_faces(conn, users, log):
    """Face templates. Tries the SDK's GetUserFace (150); falls back to template read 88. Read-only."""
    def by_150(u):
        pid = str(u.user_id).encode()[:24]
        return raw_read(conn, 150, pid + pack(f"<{24 - len(pid)}xxB2x", FACE_SLOT))

    def by_88(u):
        return raw_read(conn, 88, pack("<hb", u.uid, FACE_SLOT))

    def attempt(fn, u):
        try:
            return fn(u)[1]
        except WriteBlocked:
            raise
        except VerifyError as e:
            log.warning(f"faces: user {u.user_id}: {e}; skipped")
        except Exception as e:      # timeout = firmware ignores this command
            log.info(f"faces: user {u.user_id}: {type(e).__name__}: {e}")
            drain(conn)
            raise
        return None

    faces = {}
    for name, fn in (("GetUserFace(150)", by_150), ("UserTemplate(88)", by_88)):
        try:
            for u in users[:5]:                       # probe: does this firmware answer at all?
                data = attempt(fn, u)
                if data:
                    faces[u.user_id] = data
                    break
        except Exception:
            continue
        if not faces:
            log.info(f"faces: {name} returned nothing for the first {min(5, len(users))} users")
            continue
        log.info(f"faces: {name} works on this firmware, reading all users")
        for u in users:
            if u.user_id in faces:
                continue
            try:
                data = attempt(fn, u)
            except Exception:
                log.warning("faces: device stopped answering mid-way; saving what was read")
                break
            if data:
                faces[u.user_id] = data
        by_id = {u.user_id: u for u in users}
        return [{"uid": by_id[k].uid, "user_id": k, "method": name, "slot": FACE_SLOT, "size": len(v),
                 "sha256": hashlib.sha256(v).hexdigest(), "template_b64": base64.b64encode(v).decode()}
                for k, v in faces.items()], name
    log.info("faces: this firmware doesn't serve face templates over the network protocol")
    return [], None


def read_photos(conn, users, log):
    """Enrollment photos (JPEG) via DownloadUserPhoto (10010). Read-only."""
    photos = {}
    for u in users:
        try:
            code, data = raw_read(conn, 10010, f"{u.user_id}.jpg\x00".encode())
        except WriteBlocked:
            raise
        except VerifyError as e:
            log.warning(f"photos: user {u.user_id}: {e}; skipped")
            continue
        except Exception as e:
            log.info(f"photos: {type(e).__name__}: {e}; stopping photo reads")
            drain(conn)
            break
        if data and data[:3] == b"\xff\xd8\xff":
            photos[u.user_id] = data
        elif data:
            log.warning(f"photos: user {u.user_id} returned {len(data)} bytes that aren't a JPEG; skipped")
        elif code not in (REPLY_NO_PHOTO, const.CMD_ACK_OK) and not photos:
            log.info(f"photos: device answered code {code}: not supported on this firmware")
            break
    log.info(f"photos: {len(photos)} of {len(users)} users have a stored photo")
    return photos


# ------------------------------------------------------------------------------ storage
def load_state(out):
    try:
        with open(os.path.join(out, "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(out, state):
    path = os.path.join(out, "state.json")
    write_durable(path + ".tmp", json.dumps(state, indent=2, ensure_ascii=False))
    os.replace(path + ".tmp", path)


def write_snapshot(out, stamp, info, users, logs, fingers, faces, photos):
    root = os.path.join(out, "snapshots")
    tmp, final = os.path.join(root, f".tmp-{stamp}"), os.path.join(root, stamp)
    os.makedirs(tmp)
    write_durable(os.path.join(tmp, "info.json"), json.dumps(info, indent=2, ensure_ascii=False))
    write_durable(os.path.join(tmp, "users.csv"), csv_text(USER_COLS, [user_row(u) for u in users]))
    write_durable(os.path.join(tmp, "attendance.csv"), csv_text(ATT_COLS, [att_row(a) for a in logs]))
    if fingers:
        write_durable(os.path.join(tmp, "templates.json"), json.dumps(
            [{"uid": f.uid, "fid": f.fid, "valid": f.valid,
              "template_b64": base64.b64encode(f.template).decode()} for f in fingers], indent=1))
    if faces:
        write_durable(os.path.join(tmp, "faces.json"), json.dumps(
            {"note": "raw face templates (slot 50); restoring needs the vendor SDK/software", "faces": faces},
            indent=1))
    if photos:
        os.makedirs(os.path.join(tmp, "photos"))
        for user_id, jpeg in photos.items():
            name = re.sub(r"[^0-9A-Za-z_-]", "_", str(user_id)) + ".jpg"
            with open(os.path.join(tmp, "photos", name), "wb") as f:
                f.write(jpeg)
                f.flush()
                os.fsync(f.fileno())
    files = sorted(os.path.relpath(os.path.join(d, n), tmp).replace(os.sep, "/")
                   for d, _, names in os.walk(tmp) for n in names)
    sums = [f"{sha256(os.path.join(tmp, n))}  {n}\n" for n in files]
    write_durable(os.path.join(tmp, "SHA256SUMS"), "".join(sums))
    n = 2
    while os.path.exists(final):          # two runs in the same second: never collide
        final = os.path.join(root, f"{stamp}-{n}")
        n += 1
    os.rename(tmp, final)   # the snapshot appears all at once, or not at all
    return final


def merge_master(out, logs):
    """Add new punches to the cumulative archive. Refuses to touch it if it can't be parsed."""
    path = os.path.join(out, "attendance_master.csv")
    rows = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != ATT_COLS:
                raise VerifyError(f"{path} has unexpected columns {reader.fieldnames}; not modifying it")
            for r in reader:
                rows[tuple(r[k] for k in ATT_KEY)] = [r[c] for c in ATT_COLS]
    before = len(rows)
    for a in logs:
        r = [str(v) for v in att_row(a)]
        rows.setdefault((r[0], r[1], r[2], r[3]), r)
    ordered = sorted(rows.values(), key=lambda r: (r[1], r[0]))
    if os.path.exists(path):
        shutil.copy2(path, path + ".prev")
    write_durable(path + ".tmp", csv_text(ATT_COLS, ordered))
    os.replace(path + ".tmp", path)
    return len(rows) - before, len(rows)


def prune(out, keep, log):
    root = os.path.join(out, "snapshots")
    for d in os.listdir(root):   # leftovers from a run killed mid-write
        p = os.path.join(root, d)
        if d.startswith(".tmp-") and time.time() - os.path.getmtime(p) > 86400:
            shutil.rmtree(p, ignore_errors=True)
    if keep <= 0:
        return
    snaps = sorted(d for d in os.listdir(root) if SNAP_RE.match(d))
    for d in snaps[:-keep]:
        shutil.rmtree(os.path.join(root, d))
        log.info(f"retention: removed old snapshot {d} (its punches are kept in attendance_master.csv)")


# ------------------------------------------------------------------------------ main
def run(args, out, log):
    started = time.monotonic()
    use_udp = args.udp
    if not use_udp:
        from zk.base import ZK_helper
        use_udp = ZK_helper(args.ip, args.port).test_tcp() != 0   # TCP port closed -> device speaks UDP
    zk = ZK(args.ip, port=args.port, timeout=args.timeout, password=args.password,
            force_udp=use_udp, ommit_ping=True, encoding=args.encoding)

    conn = None
    try:
        log.info(f"connecting to {args.ip}:{args.port} over {'UDP' if use_udp else 'TCP'}")
        conn = zk.connect()
        info = read_device_info(conn, log)
        log.info(f"device: {info['device_name']} serial {info['serial']} firmware {info['firmware']}")
        users = consistent_read(conn, "users", conn.get_users, "users", args.retries, log)
        logs = consistent_read(conn, "attendance", conn.get_attendance, "records", args.retries, log)
        fingers = consistent_read(conn, "fingerprint templates", conn.get_templates, "fingers", args.retries, log)
        conn.read_sizes()
        info["counters"] = {"users": conn.users, "users_cap": conn.users_cap, "records": conn.records,
                            "records_cap": conn.rec_cap, "fingers": conn.fingers, "faces": conn.faces,
                            "faces_cap": conn.faces_cap, "cards": conn.cards}
        # Optional extras run LAST, in strict reply mode, each fully self-contained: whatever happens
        # here can't touch the already-verified users/logs above.
        faces, face_method, photos = [], None, {}
        conn._strict_replies = True
        try:
            if args.faces:
                faces, face_method = read_faces(conn, users, log)
            if args.photos:
                photos = read_photos(conn, users, log)
        except WriteBlocked:
            raise
        except Exception as e:
            log.warning(f"optional face/photo reads stopped: {type(e).__name__}: {e}")
        finally:
            conn._strict_replies = False
    except VerifyError as e:
        log.error(f"VERIFY FAILED: {e}. Nothing was saved from this run.")
        return EXIT_VERIFY
    except WriteBlocked as e:
        log.error(f"SAFETY STOP: {e}. Nothing was sent to the device.")
        return EXIT_GUARD
    except Exception as e:
        log.error(f"READ FAILED: {type(e).__name__}: {e}")
        log.error("check: IP reachable? Comm Key (--password)? Is the attendance software connected "
                  "to the device right now? Try --udp or a longer --timeout.")
        return EXIT_READ
    finally:
        if conn is not None and conn.is_connect:
            try:
                conn.disconnect()
            except Exception:
                pass

    info.update(backed_up_at=datetime.now().isoformat(timespec="seconds"), tool_version=__version__,
                read_seconds=round(time.monotonic() - started, 1))
    c = info["counters"]
    if c["records_cap"] and c["records"] / c["records_cap"] > 0.9:
        log.warning(f"attendance memory {c['records']}/{c['records_cap']} is over 90% full")
    info.update(faces_method=face_method, faces_saved=len(faces), photos_saved=len(photos))
    if args.faces and faces:
        if len(faces) == c["faces"]:
            log.info(f"faces: {len(faces)} templates saved, matches the device face counter")
        else:
            log.warning(f"faces: saved {len(faces)} templates but the device counter says {c['faces']}")
    elif c["faces"]:
        log.info(f"device holds {c['faces']} face templates; NOT in this backup "
                 f"({'firmware refused the reads' if args.faces else 'run with --faces'})")
    unnamed = sum(1 for u in users if u.name.startswith("NN-"))
    if users and unnamed > len(users) // 2:
        log.warning(f"{unnamed}/{len(users)} users have no readable name - try --encoding cp1256")

    # --- compare with the last good run
    state = load_state(out)
    serial = str(info["serial"])
    if serial.startswith("unavailable"):
        log.warning("could not read the device serial this run; skipping the device-swap check")
        serial = state.get("serial", "")
    elif state.get("serial") and state["serial"] != serial:
        snap = write_snapshot(out, datetime.now().strftime("%Y-%m-%d_%H%M%S"), info, users, logs, fingers, faces, photos)
        log.error(f"DEVICE CHANGED: this IP now answers with serial {serial}, last time {state['serial']}. "
                  f"Snapshot saved to {snap}, master archive NOT touched. Use a separate --out per device.")
        return EXIT_VERIFY
    if state:
        if len(logs) < state.get("records", 0):
            log.warning(f"device attendance count DROPPED {state['records']} -> {len(logs)}: something "
                        f"cleared logs on the device (often attendance software set to 'clear after "
                        f"download'). Earlier punches are safe in attendance_master.csv.")
        old_ids, new_ids = set(state.get("user_ids", [])), {u.user_id for u in users}
        if old_ids - new_ids:
            log.warning(f"users REMOVED since last run: {sorted(old_ids - new_ids)}")
        if new_ids - old_ids:
            log.info(f"users added since last run: {sorted(new_ids - old_ids)}")

    # --- save
    snap = write_snapshot(out, datetime.now().strftime("%Y-%m-%d_%H%M%S"), info, users, logs, fingers, faces, photos)
    try:
        added, total = merge_master(out, logs)
    except VerifyError as e:
        log.error(f"{e}. Snapshot saved to {snap}.")
        return EXIT_VERIFY
    save_state(out, {"serial": serial, "last_run": info["backed_up_at"], "users": len(users),
                     "records": len(logs), "user_ids": sorted(u.user_id for u in users)})
    prune(out, args.keep, log)
    RESULT.update(serial=serial, device_name=info["device_name"], firmware=info["firmware"],
                  snapshot=snap, users=len(users), punches_on_device=len(logs), new_punches=added,
                  master_total_punches=total, faces_saved=len(faces), faces_on_device=c["faces"],
                  clock_drift_seconds=info.get("clock_drift_seconds"), read_seconds=info["read_seconds"])
    log.info(f"OK: {len(users)} users, {len(logs)} punches on device, {added} new -> master now "
             f"{total} punches. Snapshot: {os.path.basename(snap)} ({info['read_seconds']}s)")
    return EXIT_OK


def main():
    p = argparse.ArgumentParser(description="Read-only, verified backup of a ZKTeco terminal over the network.")
    p.add_argument("ip", help="device IP (device menu: Comm. > Network)")
    p.add_argument("--out", default="zk-backups", help="backup folder, one per device (default ./zk-backups)")
    p.add_argument("--port", type=int, default=4370)
    p.add_argument("--password", type=int, default=0, help="device Comm Key (factory default 0)")
    p.add_argument("--udp", action="store_true", help="force UDP (otherwise auto: TCP if open, else UDP)")
    p.add_argument("--encoding", default="UTF-8", help="user-name encoding; try cp1256 if names are garbled")
    p.add_argument("--timeout", type=int, default=20, help="per-packet timeout in seconds")
    p.add_argument("--retries", type=int, default=3, help="attempts per table if a punch lands mid-read")
    p.add_argument("--keep", type=int, default=90, help="snapshots to keep (0 = keep all); master keeps everything")
    p.add_argument("--faces", action="store_true", help="also read face templates (firmware permitting)")
    p.add_argument("--photos", action="store_true", help="also read enrollment photos as JPEG (firmware permitting)")
    args = p.parse_args()

    os.umask(0o077)   # backups contain PINs and card numbers: owner-only files
    out = os.path.abspath(args.out)
    os.makedirs(os.path.join(out, "snapshots"), exist_ok=True)

    log = logging.getLogger("zk_backup")
    log.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(os.path.join(out, "backup.log"), maxBytes=1_000_000,
                                              backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    log.addHandler(fh)
    log.addHandler(sh)
    collector = Collector()
    log.addHandler(collector)
    started = datetime.now().isoformat(timespec="seconds")

    try:
        arm_read_only_guard()
        self_test_guard()
    except Exception as e:
        log.error(f"SAFETY: {e}")
        write_last_run(out, EXIT_GUARD, args, started, collector)
        return EXIT_GUARD
    log.info(f"zk_backup {__version__}: read-only guard armed and self-tested")

    try:
        with RunLock(os.path.join(out, ".lock")):
            try:
                code = run(args, out, log)
            except Exception:
                log.exception("INTERNAL ERROR (bug in zk_backup.py, please report the traceback)")
                code = EXIT_INTERNAL
            write_last_run(out, code, args, started, collector)
            return code
    except Locked:
        # no last_run.json here: the run holding the lock will write its own
        log.error("another backup run is in progress (lock file present); exiting")
        return EXIT_LOCKED


if __name__ == "__main__":
    sys.exit(main())
