#!/usr/bin/env python3
"""Check the ApolloQLabs Live Track Record ledger yourself.
Usage:  python3 verify_ledger.py ledger.jsonl
Each line is one JSON record. Its last field, "hash", is the SHA-256 fingerprint of the line without that field,
and its "prev" field is the fingerprint of the line before it (64 zeros for the first line). Changing, removing or
inserting any line breaks every fingerprint after it."""
import hashlib, json, re, sys
prev = "0" * 64
for i, line in enumerate(open(sys.argv[1] if len(sys.argv) > 1 else "ledger.jsonl", encoding="ascii")):
    line = line.rstrip("\n")
    if not line: continue
    m = re.match(r'^(\{.*),"hash":"([0-9a-f]{64})"\}$', line)
    assert m, "line %d is not a sealed record" % (i + 1)
    assert hashlib.sha256((m.group(1) + "}").encode("ascii")).hexdigest() == m.group(2), "line %d: fingerprint does not match" % (i + 1)
    assert json.loads(line)["prev"] == prev, "line %d: does not link to the line before it" % (i + 1)
    prev = m.group(2)
print("OK: %d lines, chain intact. Latest fingerprint: %s" % (i + 1, prev))
