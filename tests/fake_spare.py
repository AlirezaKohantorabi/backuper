#!/usr/bin/env python3
"""Fake writable ZK device for testing zk_restore.py.
usage: fake_spare.py PORT AUDIT [serial=S] [accept=N|none] [corrupt0] [preload_pin_clash]"""
import socket, struct, sys
from datetime import datetime

port, audit = int(sys.argv[1]), sys.argv[2]
opts = dict(a.split("=", 1) if "=" in a else (a, True) for a in sys.argv[3:])
SERIAL, ACCEPT = opts.get("serial", "6650154800999"), opts.get("accept", "2")
ACK_OK, ACK_ERROR, PREPARE_DATA, DATA = 2000, 2001, 1500, 1501
users = {}      # uid -> 72-byte record
faces = {}      # pin -> template
up = {"buf": b""}
if "preload_pin_clash" in opts:
    users[9] = struct.pack("<HB8s24s4sx7sx24s", 9, 0, b"", b"Someone else", b"\0" * 4, b"1", b"101")

def enc_time(t):
    return ((t.year % 100) * 12 * 31 + (t.month - 1) * 31 + t.day - 1) * 86400 + (t.hour * 60 + t.minute) * 60 + t.second

def sizes():
    f = [0] * 20
    f[4], f[8], f[15], f[16] = len(users), 0, 3000, 100000
    return struct.pack("20i", *f) + struct.pack("3i", len(faces), 0, 1200)

def send_bulk(send, blob):
    send(PREPARE_DATA, struct.pack("<I", len(blob)))
    for i in range(0, len(blob), 1024):
        send(DATA, blob[i:i + 1024])
    send(ACK_OK)

def layout_of(p):
    if len(p) == 32: return "3"
    if len(p) > 32: return "4"
    b24, size16 = p[24], struct.unpack("<H", p[26:28])[0]
    if b24 == 1 and size16: return "2"
    return "0" if size16 else "1"

opt_vals = {b"~SerialNumber": SERIAL, b"~DeviceName": "VF600", b"~Platform": "ZEM600_TFT",
            b"ZKFaceVersion": "7", b"~ZKFPVersion": ""}
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", port))
log = open(audit, "a")
while True:
    pkt, addr = sock.recvfrom(65535)
    cmd, _, sess, reply = struct.unpack("<4H", pkt[:8])
    p = pkt[8:]
    log.write(f"{cmd}\n"); log.flush()
    send = lambda c, d=b"": sock.sendto(struct.pack("<4H", c, 0, 4321, reply) + d, addr)
    if cmd == 1000: send(ACK_OK)
    elif cmd == 1001: send(ACK_OK)          # stays up across sessions
    elif cmd == 1100: send(ACK_OK, b"Ver 8.0.0 Build 359\0")
    elif cmd == 11:
        k = p.split(b"\0")[0]; send(ACK_OK, k + b"=" + opt_vals.get(k, "").encode() + b"\0")
    elif cmd == 50: send(ACK_OK, sizes())
    elif cmd == 201: send(ACK_OK, struct.pack("<I", enc_time(datetime.now())))
    elif cmd == 1503:
        inner = struct.unpack("<h", p[1:3])[0]
        if inner == 9:
            recs = b"".join(users[k] for k in sorted(users)); send(DATA, struct.pack("I", len(recs)) + recs)
        else:
            send(DATA, struct.pack("I", 0))
    elif cmd == 8:                       # write user (72-byte record)
        rec = bytearray(p[:72])
        if "zero_group" in opts: rec[40:47] = b"\0" * 7      # firmware that ignores group
        if "assign_uid" in opts:                              # like the real VF600 firmware
            pin = bytes(rec[48:72]).split(b"\0")[0]
            same = [k for k, r in users.items() if r[48:72].split(b"\0")[0] == pin]
            slot = same[0] if same else max(users, default=0) + 1
            rec[0:2] = struct.pack("<H", slot)
        else:
            slot = struct.unpack("<H", p[:2])[0]
        users[slot] = bytes(rec); send(ACK_OK)
    elif cmd in (1002, 1003, 1013): send(ACK_OK)
    elif cmd == 1502: up["buf"] = b""; send(ACK_OK)
    elif cmd == 1500: up["buf"] = b""; send(ACK_OK)
    elif cmd == 1501: up["buf"] += p; send(ACK_OK)
    elif cmd == 151:
        pin, lay = p[:24].split(b"\0")[0].decode(), layout_of(p)
        tmpl = p[28:] if lay == "4" else up["buf"]
        if lay == ACCEPT:
            if "stamp" in opts and len(tmpl) % 2576 == 0:
                slot = [k for k, r in users.items() if r[48:72].split(b"\0")[0].decode() == pin][0]
                t = bytearray(tmpl)
                for off in range(0, len(t), 2576):
                    t[off + 2:off + 4] = struct.pack("<H", slot)
                tmpl = bytes(t)
            faces[pin] = tmpl; send(ACK_OK)
        elif lay == "0" and "corrupt0" in opts:
            faces[pin] = tmpl[:-10]; send(ACK_OK)          # says OK, stores garbage
        else:
            send(ACK_ERROR)
    elif cmd == 150:
        pin = p[:24].split(b"\0")[0].decode()
        if pin in faces: send_bulk(send, faces[pin])
        else: send(ACK_ERROR)
    else:
        log.write(f"VIOLATION {cmd}\n"); log.flush(); send(ACK_OK)
