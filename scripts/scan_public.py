"""Scan tracked working files, staged blobs, all reachable history and identities.

The required private denylist contains one regular expression per line.
Only file labels and pattern numbers are emitted, never matching secret text.
"""
import argparse
import re
import subprocess
from pathlib import Path

def git(*args):
    return subprocess.check_output(["git", *args])

def scan(denylist):
    patterns = [re.compile(p, re.I) for p in Path(denylist).read_text(encoding="utf-8-sig").splitlines() if p and not p.startswith("#")]
    if not patterns:
        raise ValueError("empty denylist")
    violations, seen = [], set()
    def check(label, data):
        key = (label, data)
        if key in seen: return
        seen.add(key)
        text = label+'\n'+data.decode("utf-8", errors="replace")
        for i, pattern in enumerate(patterns, 1):
            if pattern.search(text): violations.append({"file": label, "rule": i})
    for name in git("ls-files", "-z").decode().split("\0"):
        if name and Path(name).is_file(): check("work:"+name, Path(name).read_bytes())
    for line in git("ls-files", "--stage").decode().splitlines():
        info, name = line.split("\t", 1); check("index:"+name, git("cat-file", "blob", info.split()[1]))
    commits = subprocess.run(["git","rev-list","--all"],capture_output=True)
    for commit in commits.stdout.decode().splitlines():
        check("commit:"+commit, git("cat-file", "commit", commit))
        for entry in git("ls-tree", "-rz", commit).split(b"\0"):
            if not entry: continue
            meta, name = entry.split(b"\t", 1)
            if meta.split()[1] != b"blob":
                violations.append({"file": name.decode(errors="replace"),"rule":"non-blob"}); continue
            check("history:"+name.decode(errors="replace"),git("cat-file","blob",meta.split()[2].decode()))
    return violations

if __name__ == "__main__":
    import json
    p=argparse.ArgumentParser(); p.add_argument("--denylist",required=True); a=p.parse_args()
    found=scan(a.denylist); print(json.dumps({"hits":len(found),"violations":found},ensure_ascii=False,indent=2)); raise SystemExit(bool(found))
