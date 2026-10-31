#!/usr/bin/env python3
"""Fake ZKTeco terminal speaking the UDP ZK protocol, for testing zk_backup.py.
Logs every command code it receives to an audit file.
usage: fake_zk.py PORT AUDITFILE [records=N] [serial=S] [punch_after_buffer] [punch_before_buffer] [faces]
"""
import socket, struct, sys
from datetime import datetime, timedelta

port, audit = int(sys.argv[1]), sys.argv[2]
opts = dict(a.split("=", 1) if "=" in a else (a, True) for a in sys.argv[3:])
N_REC = int(opts.get("records", 200))
SERIAL = opts.get("serial", "6650154800185")

ACK_OK, ACK_ERROR, PREPARE_DATA, DATA = 2000, 2001, 1500, 1501
users = [(1, "علی رضایی", "101"), (2, "Sara Ahmadi", "102"), (3, "محمد کریمی", "103"),
         (4, "Reza Tehrani", "104"), (5, "زهرا موسوی", "105")]
base = datetime(2026, 10, 1, 7, 30)
att = [(users[i % 5][2], base + timedelta(minutes=37 * i), 1, i % 2) for i in range(N_REC)]
state = {"buffer": b"", "punched": False}

def enc_time(t):
    return ((t.year % 100) * 12 * 31 + (t.month - 1) * 31 + t.day - 1) * 86400 + (t.hour * 60 + t.minute) * 60 + t.second

def user_blob():
    recs = b"".join(struct.pack("<HB8s24sIx7sx24s", uid, 0, b"", name.encode(), 0, b"1", uid_s.encode())
                    for uid, name, uid_s in users)
    return struct.pack("I", len(recs)) + recs

def att_blob():
    recs = b"".join(struct.pack("<I4sBB2sI", int(u), struct.pack("<I", enc_time(t)), s, p, b"\0\0", 0)
                    for u, t, s, p in att)
    return struct.pack("I", len(recs)) + recs

def punch():
    att.append(("102", datetime(2026, 10, 31, 8, 1), 1, 0))

def sizes():
    f = [0] * 20
    f[4], f[6], f[8], f[14], f[15], f[16] = len(users), 0, len(att), 3000, 3000, 100000
    return struct.pack("20i", *f) + struct.pack("3i", 5, 0, 1200)

opt_vals = {b"~SerialNumber": SERIAL, b"~DeviceName": "VF600", b"~Platform": "ZEM600_TFT",
            b"ZKFaceVersion": "7", b"~ZKFPVersion": ""}

FACE_USERS = {"101", "102", "103", "104"}     # 105 has no face enrolled

def face_of(uid_s):
    if "vx7" in opts:      # 12 blocks x 2576, device slot (uid) in bytes 2-3 of each block
        slot = int(uid_s) - 100
        out = b""
        for k in range(12):
            blk = bytearray((i * 7 + k * 13 + int(uid_s)) % 256 for i in range(2576))
            blk[2:4] = struct.pack("<H", slot)
            out += bytes(blk)
        return out
    return (b"VX7:" + uid_s.encode()) * 600     # 4.2 KB, forces multi-packet transfer

def send_bulk(send, blob):
    send(PREPARE_DATA, struct.pack("<I", len(blob)))
    for i in range(0, len(blob), 1024):
        send(DATA, blob[i:i + 1024])
    send(ACK_OK)

_sizes = sizes
def sizes():
    s = bytearray(_sizes())
    if "faces150" in opts or "faces88" in opts:
        s[80:84] = struct.pack("i", len(FACE_USERS))
    return bytes(s)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", port))
log = open(audit, "a")
while True:
    pkt, addr = sock.recvfrom(65535)
    cmd, _, sess, reply = struct.unpack("<4H", pkt[:8])
    payload = pkt[8:]
    log.write(f"{cmd}\n"); log.flush()
    send = lambda c, d=b"": sock.sendto(struct.pack("<4H", c, 0, 4321, reply) + d, addr)
    if state.get("late"):                 # a delayed answer to an earlier command finally arrives
        sock.sendto(state.pop("late"), addr)
    if cmd == 1000: send(ACK_OK)
    elif cmd == 1001: send(ACK_OK); break
    elif cmd == 1100: send(ACK_OK, b"Ver 8.0.0 Build 359\0")
    elif cmd == 11:
        key = payload.split(b"\0")[0]
        send(ACK_OK, key + b"=" + opt_vals.get(key, "").encode() + b"\0")
    elif cmd == 50: send(ACK_OK, sizes())
    elif cmd == 201: send(ACK_OK, struct.pack("<I", enc_time(datetime.now())))
    elif cmd == 1503:
        inner = struct.unpack("<h", payload[1:3])[0]
        if inner == 9:                      # users: small, sent directly
            send(DATA, user_blob())
        elif inner == 13:                   # attendance: big, chunked path
            if "punch_before_buffer" in opts and not state["punched"]:
                punch(); state["punched"] = True
            state["buffer"] = att_blob()
            if "punch_after_buffer" in opts and not state["punched"]:
                punch(); state["punched"] = True
            send(ACK_OK, b"\0" + struct.pack("<I", len(state["buffer"])))
        else:
            send(ACK_ERROR)
    elif cmd == 1504:
        start, size = struct.unpack("<ii", payload[:8])
        chunk = state["buffer"][start:start + size]
        send(PREPARE_DATA, struct.pack("<I", len(chunk)))
        for i in range(0, len(chunk), 1024):
            send(DATA, chunk[i:i + 1024])
        send(ACK_OK)
    elif cmd == 1502: send(ACK_OK)
    elif cmd == 150:        # GetUserFace: payload = user_id padded to 24 + pad + slot + 2 pad
        uid_s, slot = payload[:24].split(b"\0")[0].decode(), payload[25]
        if "late150" in opts:
            state["late"] = struct.pack("<4H", ACK_ERROR, 0, 4321, reply)   # answer arrives later
        elif "silent150" in opts:
            pass
        elif "faces150" in opts and slot == 50 and uid_s in FACE_USERS:
            send_bulk(send, face_of(uid_s))
        elif "faces150" in opts:
            send(ACK_ERROR)
        else:
            send(0xFFFF)                                  # unknown command on this firmware
    elif cmd == 88:
        uid, slot = struct.unpack("<hb", payload[:3])
        uid_s = str(100 + uid)
        if "faces88" in opts and slot == 50 and uid_s in FACE_USERS:
            send_bulk(send, face_of(uid_s))
        else:
            send(ACK_ERROR)
    elif cmd == 10010:      # DownloadUserPhoto: payload = "<user_id>.jpg\0"
        uid_s = payload.split(b".jpg")[0].decode()
        if "photos" in opts and uid_s in ("101", "103"):
            send_bulk(send, b"\xff\xd8\xff\xe0" + bytes(range(256)) * 12 + uid_s.encode() + b"\xff\xd9")
        elif "photos" in opts:
            send(0x1379)
        else:
            send(0xFFFF)
    else:
        log.write(f"VIOLATION {cmd}\n"); log.flush()
        send(ACK_OK)   # a real device would happily obey - that's what we're testing for
