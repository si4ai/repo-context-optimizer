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
# rco replay, once the wheel has it: two commits, the second changes code
if "$rco" replay --help > /dev/null 2>&1; then
  expect 0 "$rco" replay
  grep -q '(method and limits)' out.txt
  expect 0 "$rco" replay --format json
  grep -q '"schema":"rco.replay/1"' out.txt
fi
# Phase 4 commands, once the wheel has them; the pin lives outside the repo
if "$rco" drift --help > /dev/null 2>&1; then
  expect 0 "$rco" pin HEAD~1 --for pkg/core.py --format json --out "$work/core.pin.json"
  expect 1 "$rco" drift "$work/core.pin.json"            # run() gained a parameter
  expect 0 "$rco" drift "$work/core.pin.json" --at HEAD~1
fi
# 0.1.6: the cross-PR notice runs from the wheel alone, with no RCO repo
# tree: one other open PR (pr-7) uses run(), whose signature this head
# changed. gh is a stub that lists that PR; origin is a local bare repo.
py="$(dirname "$rco")/python"
git init -q --bare "$work/origin.git"
git remote add origin "$work/origin.git"
git checkout -q -b other HEAD~1
printf 'from pkg.core import run\n\nrun(1)\n' > pkg/use.py
git add pkg/use.py && git commit -q -m other
other=$(git rev-parse HEAD)
git push -q origin "$other:refs/pull/7/head"
git checkout -q main
mkdir "$work/bin"
cat > "$work/bin/gh" <<EOF
#!/bin/sh
cat <<'JSON'
[{"number": 7, "state": "open", "base": {"ref": "main"}, "head": {"sha": "$other", "ref": "other"}}]
JSON
EOF
chmod +x "$work/bin/gh"
expect 0 env PATH="$work/bin:$PATH" EVENT_NAME=pull_request PR_NUMBER=8 \
  PR_BASE="$(git rev-parse HEAD~1)" PR_BASE_REF=main PR_HEAD="$(git rev-parse HEAD)" \
  GITHUB_REPOSITORY=example/repo GITHUB_STEP_SUMMARY="$work/summary.md" "$py" -m rco.notice_report
grep -q '## pr-7' out.txt
grep -q 'pkg/use.py' out.txt
grep -q 'pkg/use.py' "$work/summary.md"
test -z "$(git status --porcelain --untracked-files=all -- . ':!out.txt' ':!err.txt')"
# rco init writes the workflow that runs that notice step from the wheel
git init -q -b main "$work/fresh"
cd "$work/fresh"
printf 'def f():\n    pass\n' > a.py
git add a.py && git commit -q -m one
expect 0 "$rco" init
grep -q 'python -m rco.notice_report' .github/workflows/rco.yml
grep -q 'pull-requests: read' .github/workflows/rco.yml
echo "smoke: passed"
