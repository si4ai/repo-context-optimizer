"""Read a commit from the git object store (tree-contract section 5).

Never the working tree, so a dirty checkout cannot change the output. The
one way in is `snapshot`, which turns the index or the working tree into a
commit object first, so every reader still sees a commit.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rco.output import UsageError

LFS_MAGIC = b"version https://git-lfs.github.com/spec/v1"
MODE_SUBMODULE = "160000"
MODE_SYMLINK = "120000"


@dataclass(frozen=True, slots=True)
class Entry:
    path: str
    mode: str
    blob: str


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _git(repo: Path, *args: str, stdin: bytes | None = None,
         env: dict[str, str] | None = None) -> bytes:
    cmd = ["git", "-C", str(repo), "-c", "core.quotepath=off", *args]
    p = subprocess.run(cmd, input=stdin, capture_output=True, env={**_env(), **(env or {})})
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise UsageError(f"git {args[0]} failed: {err[0] if err else p.returncode}")
    return p.stdout


def has_commit(repo: Path, rev: str = "HEAD") -> bool:
    """True when rev names a commit (a repo with no commits has no HEAD)."""
    p = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=off",
         "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
        capture_output=True, env=_env())
    return p.returncode == 0


def resolve(repo: Path, rev: str) -> str:
    try:
        out = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    except UsageError:
        raise UsageError(f"not a commit: {rev}") from None
    return out.decode().strip()


def ls_tree(repo: Path, sha: str) -> tuple[Entry, ...]:
    out = _git(repo, "ls-tree", "-r", "-z", "--full-tree", sha)
    entries = []
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, _typ, oid = meta.decode().split(" ")
        entries.append(Entry(path=path.decode("utf-8", "surrogateescape"), mode=mode, blob=oid))
    return tuple(sorted(entries, key=lambda e: e.path))


def read_blobs(repo: Path, blobs: Iterable[str]) -> dict[str, bytes]:
    want = sorted(set(blobs))
    if not want:
        return {}
    out = _git(repo, "cat-file", "--batch", stdin=("\n".join(want) + "\n").encode())
    got: dict[str, bytes] = {}
    i = 0
    for oid in want:
        nl = out.index(b"\n", i)
        header = out[i:nl].decode().split(" ")
        if len(header) != 3:
            raise UsageError(f"missing blob {oid}")
        size = int(header[2])
        got[oid] = out[nl + 1: nl + 1 + size]
        i = nl + 1 + size + 1
    return got


SNAPSHOTS = ("staged", "worktree")
_SNAP_ENV = {"GIT_AUTHOR_NAME": "rco", "GIT_AUTHOR_EMAIL": "rco@localhost",
             "GIT_COMMITTER_NAME": "rco", "GIT_COMMITTER_EMAIL": "rco@localhost",
             "GIT_AUTHOR_DATE": "@0 +0000", "GIT_COMMITTER_DATE": "@0 +0000"}


def snapshot(repo: Path, which: str) -> str:
    """A commit object for the index ("staged") or the working tree ("worktree").

    "worktree" is what `git add -A` would stage: tracked changes plus
    untracked files that are not ignored. It is built in a temporary copy of
    the index, so the real index is never touched. The commit has HEAD as
    its parent (none in a repo with no commits) and fixed author, committer
    and dates, so the same content gives the same SHA. Only loose objects are
    written; no ref moves.
    """
    if which not in SNAPSHOTS:
        raise UsageError(f"unknown snapshot {which!r}")
    try:
        parent = ["-p", resolve(repo, "HEAD")]
    except UsageError:
        parent = []
    if which == "staged":
        tree = _git(repo, "write-tree").decode().strip()
    else:
        index = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index")
                     .decode().strip())
        with tempfile.TemporaryDirectory(prefix="rco-index-") as tmp:
            tmp_index = Path(tmp) / "index"
            if index.exists():
                shutil.copyfile(index, tmp_index)
            env = {"GIT_INDEX_FILE": str(tmp_index)}
            _git(repo, "add", "-A", "--", ".", env=env)
            tree = _git(repo, "write-tree", env=env).decode().strip()
    msg = f"rco {which} snapshot\n".encode()
    return _git(repo, "commit-tree", tree, *parent, stdin=msg, env=_SNAP_ENV).decode().strip()


def merge_base(repo: Path, a: str, b: str) -> str:
    return _git(repo, "merge-base", a, b).decode().strip()


def default_branch(repo: Path) -> str:
    try:
        ref = _git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").decode().strip()
        return ref.removeprefix("refs/remotes/")
    except UsageError:
        pass
    for name in ("main", "master"):
        try:
            resolve(repo, name)
            return name
        except UsageError:
            continue
    raise UsageError("no default branch: no origin/HEAD, main or master")
