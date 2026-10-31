#!/usr/bin/env bash
# Regression tests for zk_backup.py and zk_restore.py against simulated ZKTeco devices.
# No real device needed. Run from anywhere:   ./tests/run_tests.sh
# Uses $PY (default python3), which must have pyzk==0.9 installed, e.g.  PY=~/zk-venv/bin/python ./tests/run_tests.sh
# Every scenario also checks the fake device's audit log: a "VIOLATION" there means a command outside
# the tool's whitelist reached the device, which fails the whole run.
set -u
PY=${PY:-python3}
T=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$T")
W=$(mktemp -d)
trap 'pkill -f "fake_(zk|spare).py 1437" 2>/dev/null; rm -rf "$W"' EXIT
pass=0; fail=0
ok()   { echo "PASS  $1"; pass=$((pass + 1)); }
bad()  { echo "FAIL  $1"; echo "      ---- output ----"; sed 's/^/      /' "$W/out.txt" | tail -15; fail=$((fail + 1)); }
check() { # name, expected exit, grep pattern (in output), [audit file]
    local name=$1 want=$2 pat=$3 audit=${4:-}
    if [ "$CODE" != "$want" ]; then bad "$name (exit $CODE, wanted $want)"; return; fi
    if [ -n "$pat" ] && ! grep -qE "$pat" "$W/out.txt"; then bad "$name (missing: $pat)"; return; fi
    if [ -n "$audit" ] && grep -q VIOLATION "$audit"; then bad "$name (non-whitelisted command reached device)"; return; fi
    ok "$name"
}
src()   { "$PY" "$T/fake_zk.py" 14370 "$W/$1.audit" "${@:2}" & SRC=$!; sleep 0.4; }
spare() { "$PY" "$T/fake_spare.py" 14380 "$W/$1.audit" "${@:2}" & SPR=$!; sleep 0.4; }
stop()  { kill "$1" 2>/dev/null; wait "$1" 2>/dev/null; }
backup() { "$PY" "$ROOT/zk_backup.py" 127.0.0.1 --port 14370 --timeout 3 "$@" > "$W/out.txt" 2>&1; CODE=$?; }
restore() { "$PY" "$ROOT/zk_restore.py" 127.0.0.1 "$@" --port 14380 --timeout 3 > "$W/out.txt" 2>&1; CODE=$?; }
lastrun() { "$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['status'])" "$1/last_run.json"; }

echo "== zk_backup.py =="
src b1; backup --out "$W/b"; stop $SRC
check "normal backup: 5 users, 200 punches" 0 "attendance: 200 read, matches" "$W/b1.audit"
[ "$(lastrun "$W/b")" = ok ] && ok "last_run.json status ok" || bad "last_run.json status ok"

src b2 punch_before_buffer; backup --out "$W/b2out"; stop $SRC
check "punch mid-read: misparse detected and retried" 0 "attendance: 201 read, matches" "$W/b2.audit"
grep -q "inconsistent" "$W/out.txt" && ok "inconsistent first attempt was discarded" || bad "inconsistent first attempt was discarded"

src b3 records=10; backup --out "$W/b"; stop $SRC
check "device wiped between runs: warned, master kept" 0 "DROPPED 200 -> 10" "$W/b3.audit"
[ "$(lastrun "$W/b")" = warning ] && ok "last_run.json status warning" || bad "last_run.json status warning"

touch "$W/b/.lock"; backup --out "$W/b"; rm -f "$W/b/.lock"
check "second run while locked" 3 "another backup run is in progress"

src b5 serial=9999999999; backup --out "$W/b"; stop $SRC
check "different device at the same IP: master untouched" 2 "DEVICE CHANGED" "$W/b5.audit"

src b6 faces150 vx7; backup --out "$W/b6out" --faces; stop $SRC
check "faces via GetUserFace(150), count verified" 0 "4 templates saved, matches the device face counter" "$W/b6.audit"

src b7 faces88; backup --out "$W/b7out" --faces; stop $SRC
check "faces via fallback command 88" 0 "UserTemplate\(88\) works" "$W/b7.audit"

src b8 silent150; backup --out "$W/b8out" --faces; stop $SRC
check "firmware ignores face command: backup still OK" 0 "doesn't serve face templates" "$W/b8.audit"

src b9 late150 photos; backup --out "$W/b9out" --faces --photos; stop $SRC
check "late (stale) reply detected, photos still read" 0 "ReplyMismatch.*stale" "$W/b9.audit"
grep -q "photos: 2 of 5" "$W/out.txt" && ok "session recovered after stale reply" || bad "session recovered after stale reply"

"$PY" "$ROOT/zk_backup.py" 127.0.0.1 --port 14399 --udp --timeout 2 --out "$W/b10" > "$W/out.txt" 2>&1; CODE=$?
check "unreachable device" 1 "READ FAILED"
[ "$(lastrun "$W/b10")" = read_error ] && ok "last_run.json status read_error" || bad "last_run.json status read_error"

src b11
"$PY" - "$ROOT" > "$W/out.txt" 2>&1 <<'EOF'
import sys; sys.path.insert(0, sys.argv[1])
import zk_backup as zb
from zk import ZK
zb.arm_read_only_guard(); zb.self_test_guard()
c = ZK("127.0.0.1", port=14370, force_udp=True, ommit_ping=True, timeout=5).connect()
for strict in (False, True):
    c._strict_replies = strict
    for call in (c.clear_attendance, c.clear_data, c.disable_device, c.restart, lambda: c.delete_user(uid=1)):
        try:
            call(); print("NOT BLOCKED"); sys.exit(1)
        except zb.WriteBlocked:
            pass
c._strict_replies = False; c.disconnect(); print("all blocked")
EOF
CODE=$?; stop $SRC
check "live connection: destructive calls blocked before the wire" 0 "all blocked" "$W/b11.audit"

echo "== zk_restore.py =="
backup_src() { src mk faces150 vx7; backup --out "$W/r" --faces; stop $SRC; SNAP=$(ls -d "$W"/r/snapshots/2* | tail -1); }
backup_src
SER=6650154800999
spare r1; restore "$SNAP"; stop $SPR
check "dry run writes nothing" 0 "DRY RUN" "$W/r1.audit"
grep -qE '^(8|151|1500|1501|1003)$' "$W/r1.audit" && bad "dry run sent write commands" || ok "dry run sent no write commands"

spare r2; restore "$SNAP" --write --confirm-serial 123; stop $SPR
check "wrong --confirm-serial refused" 2 "does not match"

spare r3 serial=6650154800185; restore "$SNAP" --write --confirm-serial 6650154800185; stop $SPR
check "source device refused" 2 "device the snapshot came from"

spare r4 assign_uid stamp accept=0 preload_pin_clash
restore "$SNAP" --only-user 101 --write --confirm-serial $SER
check "single user, already on spare in another slot; slot stamp predicted" 0 "layout #1 .*IDENTICAL" "$W/r4.audit"
restore "$SNAP" --write --confirm-serial $SER --face-layout 1; stop $SPR
check "full restore, all faces verified" 0 "4/4 written and verified" "$W/r4.audit"
grep -q "read-back differs" "$W/out.txt" && bad "no false read-back failures" || ok "no false read-back failures"

spare r6 assign_uid accept=none; restore "$SNAP" --only-user 102 --write --confirm-serial $SER; stop $SPR
check "no face layout verifies: stops, users kept" 2 "no candidate layout verified" "$W/r6.audit"

spare r7 zero_group; restore "$SNAP" --only-user 102 --write --confirm-serial $SER; stop $SPR
check "field changed by device: exact diff shown, stops before faces" 2 "group_id: snapshot '1' -> device ''" "$W/r7.audit"

echo
echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
