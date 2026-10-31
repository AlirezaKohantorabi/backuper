#!/usr/bin/env python3
"""
zk_restore.py - restore users and face templates from a zk_backup.py snapshot onto a ZKTeco device.

THIS TOOL WRITES TO THE DEVICE. Use it on a spare unit first.

SAFETY
  - Dry run by default: connects, reads, prints exactly what it would write, writes nothing.
  - Writing needs BOTH --write and --confirm-serial <the target's serial>, checked against the
    serial the device reports live, so a typo'd IP can't hit the wrong unit.
  - Refuses to write to the device the snapshot came from unless --allow-source-device.
  - Command whitelist: reads + user write + buffer upload + face write + refresh + freeze/unfreeze.
    Clear data, clear logs, delete user/face, restart, power off, set time, change settings all
    stay blocked, and that's self-tested before connecting. Attendance logs are never touched.
  - Snapshot checksums are verified before anything else.
  - Every write is verified by reading it back: users field by field, faces byte for byte.

FACE WRITE FORMAT
  The face-write command (CMD_USERFACE_WRQ = 151, from ZKTeco firmware 6.60's command table) has
  no public payload documentation. The first face is written with a few candidate layouts, each
  checked by reading the face back with command 150 (known to work). Expected read-back = the
  backup template with the device's own slot number stamped into each 2576-byte block (the only
  bytes the device changes). The first exact match wins; on the VF600 that's layout #1.

USAGE
  1. python zk_restore.py <spare-ip> <snapshot-dir>                          (dry run)
  2. python zk_restore.py <spare-ip> <snapshot-dir> --only-user <your id> --write --confirm-serial <S>
  3. Stand in front of the spare. Recognized?  Then restore everyone (drop --only-user).

Tested against pyzk 0.9.
"""
import argparse
import base64
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from struct import pack

try:
    from zk import ZK, const
    from zk.base import ZK_helper
except ImportError:
    sys.exit("pyzk is not installed. Run: pip install pyzk==0.9")

CMD_USERFACE_RRQ, CMD_USERFACE_WRQ, FACE_SLOT = 150, 151, 50

ALLOWED = {
    const.CMD_CONNECT, const.CMD_AUTH, const.CMD_EXIT, const.CMD_GET_VERSION, const.CMD_OPTIONS_RRQ,
    const.CMD_GET_FREE_SIZES, const.CMD_GET_TIME, 1503, 1504, const.CMD_FREE_DATA,     # reads
    CMD_USERFACE_RRQ,                                                                  # read face
    const.CMD_USER_WRQ,                       # write one user record
    const.CMD_PREPARE_DATA, const.CMD_DATA,   # upload a buffer (the face template)
    CMD_USERFACE_WRQ,                         # store the uploaded face for a user
    const.CMD_REFRESHDATA,                    # make the device reload its in-memory tables
    const.CMD_DISABLEDEVICE, const.CMD_ENABLEDEVICE,   # freeze while writing (protocol requirement)
}
BUFFERED_TABLES = {const.CMD_DB_RRQ, const.CMD_USERTEMP_RRQ, const.CMD_ATTLOG_RRQ}


class Blocked(RuntimeError):
    pass


class Stop(RuntimeError):
    pass


def arm_guard():
    original = getattr(ZK, "_ZK__send_command", None)
    if original is None:
        raise Stop("pyzk internals changed; refusing to run unguarded (pip install pyzk==0.9)")

    def guarded(self, command, command_string=b"", response_size=8):
        if command not in ALLOWED:
            raise Blocked(f"blocked command {command}")
        if command == 1503 and int.from_bytes(command_string[1:3], "little", signed=True) not in BUFFERED_TABLES:
            raise Blocked("blocked buffered request")
        nxt = self._ZK__reply_id + 1
        self._last_sent_id = nxt - const.USHRT_MAX if nxt >= const.USHRT_MAX else nxt
        return original(self, command, command_string, response_size)

    ZK._ZK__send_command = guarded
    probe = ZK("192.0.2.1", ommit_ping=True)    # never contacted
    probe.is_connect = True
    for name, call in {"clear_data": probe.clear_data, "clear_attendance": probe.clear_attendance,
                       "delete_user": lambda: probe.delete_user(uid=1),
                       "delete_user_template": lambda: probe.delete_user_template(uid=1, temp_id=0),
                       "restart": probe.restart, "poweroff": probe.poweroff,
                       "set_time": lambda: probe.set_time(datetime.now())}.items():
        try:
            call()
        except Blocked:
            continue
        except Exception as e:
            raise Stop(f"guard self-test: {name} raised {type(e).__name__} instead of being blocked")
        raise Stop(f"guard self-test: {name} was NOT blocked")


# ------------------------------------------------------------------------------ snapshot
def load_snapshot(path):
    sums = os.path.join(path, "SHA256SUMS")
    if not os.path.exists(sums):
        raise Stop(f"{path} has no SHA256SUMS - not a zk_backup snapshot folder")
    for line in open(sums, encoding="utf-8"):
        digest, name = line.rstrip("\n").split("  ", 1)
        h = hashlib.sha256(open(os.path.join(path, name), "rb").read()).hexdigest()
        if h != digest:
            raise Stop(f"snapshot file {name} is corrupted (checksum mismatch)")
    info = json.load(open(os.path.join(path, "info.json"), encoding="utf-8"))
    users = list(csv.DictReader(open(os.path.join(path, "users.csv"), encoding="utf-8-sig")))
    faces = {}
    fpath = os.path.join(path, "faces.json")
    if os.path.exists(fpath):
        for f in json.load(open(fpath, encoding="utf-8"))["faces"]:
            raw = base64.b64decode(f["template_b64"])
            if hashlib.sha256(raw).hexdigest() != f["sha256"]:
                raise Stop(f"face template for user {f['user_id']} is corrupted")
            faces[f["user_id"]] = raw
    return info, users, faces


def stored_name(u):
    # pyzk shows an empty device name as "NN-<user_id>"; restore it as empty, not as that text
    return "" if u["name"] == f"NN-{u['user_id']}" else u["name"]


# ------------------------------------------------------------------------------ device ops
def write_user(conn, u, slot, encoding):
    """One CMD_USER_WRQ in the 72-byte record format (same layout pyzk reads). Keeps the exact
    privilege, unlike pyzk's set_user which downgrades anything that isn't user/admin.
    `slot` is the device-internal uid: the existing one for an update, the next free one otherwise.
    (Identity is the user ID / PIN; this firmware assigns slots itself.)"""
    pkt = pack("<HB8s24s4sx7sx24s", slot, int(u["privilege"]),
               u["password"].encode(encoding)[:8], stored_name(u).encode(encoding)[:24],
               pack("<I", int(u["card"] or 0)), u["group_id"].encode()[:7], u["user_id"].encode()[:24])
    r = conn._ZK__send_command(const.CMD_USER_WRQ, pkt, 1024)
    if not r["status"]:
        raise Stop(f"device refused user {u['user_id']} (reply {r['code']})")


def read_face(conn, user_id):
    pid = user_id.encode()[:24]
    r = conn._ZK__send_command(CMD_USERFACE_RRQ, pid + pack(f"<{24 - len(pid)}xxB2x", FACE_SLOT), 1032)
    if r["code"] not in (const.CMD_DATA, const.CMD_PREPARE_DATA):
        return None
    return conn._ZK__recieve_chunk()


def drain(conn):
    """After a timeout: drop late replies and move past used reply ids (see zk_backup.py)."""
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


BLOCK = 2576   # VX7 face data = 12 blocks of 2576 bytes (seen on the real VF600, firmware 6.60)


def expected_face(template, slot):
    """What the device should store: it stamps the user's slot on THIS device into bytes 2-3 of
    every block (confirmed on the real spare: only those bytes changed, 220 -> 5). The high byte is
    inferred to be part of the same little-endian field."""
    if len(template) % BLOCK:
        return template
    b = bytearray(template)
    for off in range(0, len(b), BLOCK):
        b[off + 2:off + 4] = pack("<H", slot)
    return bytes(b)


def describe(want, got):
    if got is None:
        return "none"
    if len(got) != len(want):
        return f"{len(got)} bytes (expected {len(want)})"
    d = [i for i in range(len(want)) if want[i] != got[i]]
    return "IDENTICAL" if not d else f"{len(d)} bytes differ, first at offsets {d[:6]}"


def face_header(user_id, byte24, size16):
    pid = user_id.encode()[:24]
    return pid.ljust(24, b"\0") + bytes([byte24, FACE_SLOT]) + pack("<H", size16)


# Candidate layouts for CMD_USERFACE_WRQ. Each: (name, function(conn, user_id, template) -> reply code)
def _buffered(header_fn):
    def go(conn, uid, t):
        conn._send_with_buffer(t)                     # FREE_DATA, PREPARE_DATA(size), DATA chunks
        return conn._ZK__send_command(CMD_USERFACE_WRQ, header_fn(uid, t), 1024)["code"]
    return go


LAYOUTS = [
    ("buffer + header(pin,0,slot,size16)",  _buffered(lambda u, t: face_header(u, 0, len(t)))),
    ("buffer + header(pin,0,slot,0)",       _buffered(lambda u, t: face_header(u, 0, 0))),
    ("buffer + header(pin,1,slot,size16)",  _buffered(lambda u, t: face_header(u, 1, len(t)))),
    ("buffer + header(pin,0,slot,0)+size32", _buffered(lambda u, t: face_header(u, 0, 0) + pack("<I", len(t)))),
    ("inline header(pin,0,slot,size16)+template",
     lambda conn, u, t: conn._ZK__send_command(CMD_USERFACE_WRQ, face_header(u, 0, len(t)) + t, 1024)["code"]),
]


def connect(args):
    use_udp = args.udp or ZK_helper(args.ip, args.port).test_tcp() != 0
    zk = ZK(args.ip, port=args.port, timeout=args.timeout, password=args.password,
            force_udp=use_udp, ommit_ping=True, encoding=args.encoding)
    print(f"connecting to {args.ip}:{args.port} over {'UDP' if use_udp else 'TCP'}")
    return zk.connect()


# ------------------------------------------------------------------------------ main
def main():
    p = argparse.ArgumentParser(description="Restore users + faces from a zk_backup snapshot. WRITES TO THE DEVICE.")
    p.add_argument("ip", help="target device IP")
    p.add_argument("snapshot", help="snapshot folder, e.g. .../snapshots/2026-10-31_151651")
    p.add_argument("--only-user", action="append", default=[], metavar="USER_ID",
                   help="restore just this user (repeatable). Start with yourself.")
    p.add_argument("--write", action="store_true", help="actually write (default is a dry run)")
    p.add_argument("--confirm-serial", default="", help="target's serial number; must match the live device")
    p.add_argument("--allow-source-device", action="store_true",
                   help="allow writing back to the device the snapshot came from (disaster recovery only)")
    p.add_argument("--skip-faces", action="store_true", help="restore user records only")
    p.add_argument("--face-layout", type=int, choices=range(1, len(LAYOUTS) + 1), metavar="N",
                   help="use the face-write layout a previous run verified (skips probing)")
    p.add_argument("--port", type=int, default=4370)
    p.add_argument("--password", type=int, default=0, help="target's Comm Key")
    p.add_argument("--udp", action="store_true")
    p.add_argument("--encoding", default="UTF-8")
    p.add_argument("--timeout", type=int, default=20)
    args = p.parse_args()

    try:
        arm_guard()
        print("guard armed: clear/delete/restart/set-time blocked (self-tested); attendance logs untouched")
        info, users, faces = load_snapshot(args.snapshot)
        print(f"snapshot OK (checksums verified): {len(users)} users, {len(faces)} faces, "
              f"from {info['device_name']} serial {info['serial']}, firmware {info['firmware']}")
        if args.only_user:
            wanted = set(args.only_user)
            users = [u for u in users if u["user_id"] in wanted]
            missing = wanted - {u["user_id"] for u in users}
            if missing:
                raise Stop(f"not in snapshot: {sorted(missing)} (user_id column of users.csv)")
        selected_faces = {u["user_id"]: faces[u["user_id"]] for u in users if u["user_id"] in faces}
        if args.skip_faces:
            selected_faces = {}
        return run(args, info, users, selected_faces)
    except Stop as e:
        print(f"STOPPED: {e}")
        return 2
    except Blocked as e:
        print(f"SAFETY STOP: {e}")
        return 4


def run(args, info, users, faces):
    conn = connect(args)
    frozen = False
    try:
        serial = conn.get_serialnumber()
        firmware, face_ver = conn.get_firmware_version(), conn.get_face_version()
        target_users = conn.get_users()
        conn.read_sizes()
        print(f"target: {conn.get_device_name()} serial {serial}, firmware {firmware}, face algo {face_ver}; "
              f"holds {conn.users} users, {conn.faces} faces, {conn.records} punches")
        print("user record format on target: " + (f"{int(conn.user_packet_size)}-byte" if target_users
              else "unknown (target is empty; the read-back after writing verifies it)"))

        problems = []
        if str(firmware) != str(info["firmware"]):
            problems.append(f"firmware differs (snapshot {info['firmware']}, target {firmware})")
        if faces and str(face_ver) != str(info["face_version"]):
            raise Stop(f"face algorithm differs (snapshot {info['face_version']}, target {face_ver}): "
                       f"templates would be useless on this device")
        if target_users and int(conn.user_packet_size) != 72:
            raise Stop("target uses the 28-byte user format; this tool writes the 72-byte format")
        # Identity is the user ID (PIN). The internal slot (uid) belongs to the device: this firmware
        # ignores the slot number on a new user and assigns the next free one. So: update matching
        # PINs in place, give new PINs the next free slot, never write into a slot that holds anyone else.
        by_pin = {t.user_id: t for t in target_users}
        next_slot = max((t.uid for t in target_users), default=0) + 1
        slot_for, updates, new = {}, [], []
        for u in users:
            t = by_pin.get(u["user_id"])
            if t:
                slot_for[u["user_id"]] = t.uid
                updates.append((u, t))
            else:
                slot_for[u["user_id"]] = next_slot
                next_slot += 1
                new.append(u)
        if next_slot - 1 > 65535 or (conn.users_cap and next_slot - 1 > conn.users_cap):
            raise Stop(f"not enough user slots on the target (would need slot {next_slot - 1})")
        print(f"plan: write {len(users)} users ({len(updates)} already on target by user ID, updated in place; "
              f"{len(new)} new), {len(faces)} faces")
        for u, t in updates[:10]:
            if t.name != u["name"]:
                print(f"  user {u['user_id']}: name on target '{t.name}' -> '{u['name']}'")
        for pr in problems:
            print(f"WARNING: {pr}")

        if not args.write:
            print("DRY RUN: nothing was written. Re-run with --write --confirm-serial <serial> to restore.")
            return 0
        if args.confirm_serial != serial:
            raise Stop(f"--confirm-serial '{args.confirm_serial}' does not match this device's serial '{serial}'")
        if serial == str(info["serial"]) and not args.allow_source_device:
            raise Stop("this is the device the snapshot came from; use a spare (or --allow-source-device "
                       "for real disaster recovery)")

        conn.disable_device()
        frozen = True

        # 1. users
        for i, u in enumerate(users, 1):
            write_user(conn, u, slot_for[u["user_id"]], args.encoding)
            if i % 50 == 0 and i < len(users):
                print(f"  users written: {i}/{len(users)}", flush=True)
        conn.refresh_data()
        back = {x.user_id: x for x in conn.get_users()}
        bad = []
        for u in users:
            x = back.get(u["user_id"])
            if x is None:
                bad.append(u["user_id"])
                print(f"  {u['user_id']}: not found on the device after writing")
                continue
            got = {"name": x.name, "privilege": str(x.privilege),          # slot (uid) not compared:
                   "password": x.password, "group_id": x.group_id, "card": str(x.card)}  # device-owned
            diffs = [(f, u[f], got[f]) for f in got if got[f] != u[f]]
            if diffs:
                bad.append(u["user_id"])
                if len(bad) <= 10:
                    for f, want, have in diffs:   # repr() shows hidden spaces / odd characters
                        print(f"  {u['user_id']}: {f}: snapshot {want!r} -> device {have!r}")
        if bad:
            raise Stop(f"user read-back mismatch for {len(bad)} user(s) (details above); stopping before faces")
        print(f"users: {len(users)} written and verified by read-back")

        # 2. faces
        if faces:
            order = list(faces)
            first = order[0]
            chosen = None
            candidates = list(enumerate(LAYOUTS, 1))
            if args.face_layout:
                candidates = [candidates[args.face_layout - 1]]
            for n, (name, fn) in candidates:
                try:
                    code = fn(conn, first, faces[first])
                except Blocked:
                    raise
                except Exception as e:
                    print(f"  face layout #{n} '{name}': error {type(e).__name__}: {e}")
                    drain(conn)
                    continue
                conn.refresh_data()
                want = expected_face(faces[first], back[first].uid)
                got = read_face(conn, first)
                ok = got == want
                print(f"  face layout #{n} '{name}': reply {code}, read-back "
                      f"{describe(want, got)}")
                if ok:
                    chosen = (n, name, fn)
                    break
            if not chosen:
                raise Stop("no candidate layout verified. Users were restored; faces were not. "
                           "Send me the lines above - next step is capturing the vendor software's upload.")
            print(f"faces: layout #{chosen[0]} verified on user {first} (next time: --face-layout {chosen[0]})")
            verified = 1
            started = time.monotonic()
            for i, uid in enumerate(order[1:], 2):
                chosen[2](conn, uid, faces[uid])
                if i % 10 == 0 or i == len(order):     # ~34 round trips per face: show progress
                    rate = (i - 1) / max(time.monotonic() - started, 0.001)
                    print(f"  faces written: {i}/{len(order)} (~{(len(order) - i) / rate:.0f}s left)", flush=True)
            conn.refresh_data()
            for i, uid in enumerate(order[1:], 2):
                if read_face(conn, uid) == expected_face(faces[uid], back[uid].uid):
                    verified += 1
                else:
                    print(f"  face for {uid}: read-back differs")
                if i % 25 == 0 or i == len(order):
                    print(f"  faces checked: {i}/{len(order)}", flush=True)
            print(f"faces: {verified}/{len(faces)} written and verified byte-for-byte (device slot stamp included)")
        conn.read_sizes()
        print(f"target now holds {conn.users} users, {conn.faces} faces. "
              f"Now the real test: have a restored person stand in front of it.")
        return 0
    finally:
        if frozen:
            try:
                conn.enable_device()
            except Exception:
                print("WARNING: could not unfreeze the device; restart it from its power button")
        try:
            conn.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
