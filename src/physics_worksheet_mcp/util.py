import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from contextlib import contextmanager
from .errors import ServiceError

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def sha(data): return hashlib.sha256(data).hexdigest()
def fingerprint(value): return sha(canonical(value))
def read_json(path): return json.loads(Path(path).read_text(encoding="utf-8-sig"))
def write_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp=Path(f.name); f.write(canonical(value)+b"\n"); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)

def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}",value):
        raise ServiceError("invalid_identifier")
    return value

def confined(root, name):
    p=PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
        raise ServiceError("unsafe_path")
    root=Path(root).absolute(); target=root
    if root.is_symlink(): raise ServiceError("symlink_forbidden")
    for part in p.parts:
        target=target/part
        if target.is_symlink(): raise ServiceError("symlink_forbidden")
    if not target.resolve().is_relative_to(root.resolve()): raise ServiceError("unsafe_path")
    return target

def verify_tree(root):
    root=Path(root); raw=(root/"SHA256SUMS").read_bytes(); declared={}
    for line in raw.decode().splitlines():
        m=re.fullmatch(r"([a-f0-9]{64})  (.+)",line)
        if not m or m[2] in declared or m[2]=="SHA256SUMS": raise ServiceError("release_drift")
        path=confined(root,m[2])
        if not path.is_file() or sha(path.read_bytes())!=m[1]: raise ServiceError("release_drift")
        declared[m[2]]=m[1]
    actual=set()
    for p in root.rglob("*"):
        if p.is_symlink(): raise ServiceError("symlink_forbidden")
        if p.is_file() and p!=root/"SHA256SUMS": actual.add(p.relative_to(root).as_posix())
    if actual!=set(declared) or not declared: raise ServiceError("release_drift")
    return sha(raw)

def make_sums(root):
    root=Path(root)
    (root/"SHA256SUMS").write_text("".join(f"{sha(p.read_bytes())}  {p.relative_to(root).as_posix()}\n" for p in sorted(root.rglob("*")) if p.is_file() and p!=root/"SHA256SUMS"),encoding="utf-8")

@contextmanager
def file_lock(path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a+b") as f:
        if os.name=="posix":
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX)
        else:
            import msvcrt
            f.seek(0); f.write(b"0"); f.flush(); f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
        try: yield
        finally:
            if os.name=="posix": fcntl.flock(f,fcntl.LOCK_UN)
            else: f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
