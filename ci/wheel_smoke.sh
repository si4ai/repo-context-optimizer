#!/usr/bin/env bash
# Smoke test an installed rco on a scratch fixture repo, checking exit codes.
# usage: ci/wheel_smoke.sh PATH_TO_RCO
# Runs outside any checkout, so only the installed wheel is on the path.
set -euo pipefail
rco=$1
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
export GIT_AUTHOR_NAME=fixture GIT_AUTHOR_EMAIL=fixture@example.com
export GIT_COMMITTER_NAME=fixture GIT_COMMITTER_EMAIL=fixture@example.com
git init -q -b main repo
cd repo
mkdir -p pkg db
printf '"""Pkg."""\n' > pkg/__init__.py
printf 'def run(x: int) -> int:\n    return x\n' > pkg/core.py
printf 'CREATE TABLE users (id BIGINT PRIMARY KEY);\n' > db/schema.sql
git add -A && git commit -q -m one
printf 'def run(x: int, y: int = 0) -> int:\n    return x + y\n' > pkg/core.py
git commit -q -am two

expect() {  # expect CODE CMD...
  local want=$1; shift
  set +e; "$@" > out.txt 2> err.txt; local got=$?; set -e
  if [ "$got" != "$want" ]; then
    echo "smoke: '$*' exited $got, want $want"; cat err.txt; exit 1
  fi
  echo "smoke: ok ($got) $*"
}

expect 0 "$rco" --version
expect 0 "$rco" tree
grep -q 'def run' out.txt
expect 0 "$rco" tree --format json
expect 0 "$rco" check --selftest
expect 1 "$rco" diff HEAD~1 HEAD   # a signature changed
expect 0 "$rco" diff HEAD HEAD
expect 2 "$rco" no-such-command
# Phase 2 commands, once the wheel has them
if "$rco" pack --help > /dev/null 2>&1; then
  expect 0 "$rco" pack
  expect 0 "$rco" pack --for pkg/core.py --budget 200
fi
test -z "$(git status --porcelain --untracked-files=all -- . ':!out.txt' ':!err.txt')"
echo "smoke: passed"
