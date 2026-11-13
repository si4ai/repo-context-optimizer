#!/usr/bin/env bash
set -uo pipefail
zeros=0000000000000000000000000000000000000000
if [ "$EVENT_NAME" = "pull_request" ]; then
  base="$PR_BASE"
  head="$PR_HEAD"
  exclude_args=(--exclude "$PR_NUMBER")
else
  base="$PUSH_BEFORE"
  head="$PUSH_SHA"
  exclude_args=()
fi
if [ -z "$base" ] || [ "$base" = "$zeros" ]; then
  echo "no usable base for this event: no cross-PR contract notice."
  echo "no usable base for this event: no cross-PR contract notice." >> "$GITHUB_STEP_SUMMARY"
  exit 0
fi
gh api --paginate "repos/${GITHUB_REPOSITORY}/pulls?state=open&per_page=100" > pulls_raw.json || exit 3
python3 ci/notice_heads.py pulls_raw.json "${exclude_args[@]}" > others.txt || exit 3
if [ ! -s others.txt ]; then
  echo "no other open PRs: no cross-PR contract notice."
  echo "no other open PRs: no cross-PR contract notice." >> "$GITHUB_STEP_SUMMARY"
  exit 0
fi
while read -r label sha; do
  git fetch --no-tags origin "pull/${label#pr-}/head:refs/heads/${label}"
  got=$(git rev-parse --verify "${label}^{commit}")
  if [ "$got" != "$sha" ]; then
    echo "fetched $label is $got, expected $sha" >&2
    exit 3
  fi
done < others.txt
# notice names the local refs pr-N, never a login or a desk
mapfile -t labels < <(awk '{print $1}' others.txt)
PYTHONPATH=src python -m rco notice --base "$base" --head "$head" "${labels[@]}" > notice.md
code=$?
cat notice.md
cat notice.md >> "$GITHUB_STEP_SUMMARY"
case "$code" in
  0) exit 0 ;;
  *) exit "$code" ;;
esac
