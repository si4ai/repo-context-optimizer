#!/usr/bin/env bash
# Run every property of rco_update.pml against every variant.
# Each cell has an expected result (PASS or FAIL) from the results table
# in README.md. The run exits nonzero when any check differs from its
# expectation, when spin or the C compiler fails, or when a search is
# not exhaustive.
#
# Usage: ./run.sh            (all)      ./run.sh a 1   (one variant, one property)
set -uo pipefail
cd "$(dirname "$0")"
command -v spin >/dev/null || { echo "run.sh: spin not found (apt-get install spin, or build github.com/nimble-code/Spin)" >&2; exit 2; }
command -v gcc >/dev/null || { echo "run.sh: no C compiler (gcc)" >&2; exit 2; }

OUT=results; mkdir -p "$OUT"
SUM="$OUT/summary.txt"
: > "$SUM"
emit() { printf '%s\n' "$*" | tee -a "$SUM"; }

# variant name -> spin -D flags
declare -A V=(
  [a]="-DGUARD=0 -DTOUCH=0 -DSTALE=0 -DQAFTER=0"
  [b]="-DGUARD=1 -DTOUCH=0 -DSTALE=0 -DQAFTER=0"
  [bcas]="-DGUARD=2 -DTOUCH=0 -DSTALE=0 -DQAFTER=0"
  [fixed]="-DGUARD=2 -DTOUCH=1 -DSTALE=1 -DQAFTER=1"
  # one-fix ablations on top of bcas, to show which fix closes which property
  [bcas_touch]="-DGUARD=2 -DTOUCH=1 -DSTALE=0 -DQAFTER=0"
  [bcas_derived]="-DGUARD=2 -DTOUCH=0 -DSTALE=1 -DQAFTER=0"
  [bcas_qafter]="-DGUARD=2 -DTOUCH=0 -DSTALE=0 -DQAFTER=1"
)
ORDER="a b bcas fixed bcas_touch bcas_derived bcas_qafter"
# property -> bounds
declare -A B=(
  [1]="-DMAXCRASH=1 -DMAXDUP=1" [2]="-DMAXCRASH=1 -DMAXDUP=1" [3]="-DMAXCRASH=1 -DMAXDUP=1"
  [4]="-DMAXCRASH=0 -DMAXDUP=1" [5]="-DMAXCRASH=1 -DMAXDUP=0"
  [6]="-DMAXCRASH=1 -DMAXDUP=1" [7]="-DMAXCRASH=0 -DMAXDUP=1"
)

# Expected verdict for each variant:property, from the Results tables in README.md.
declare -A EXPECT=(
  [a:1]=FAIL [a:2]=FAIL [a:3]=FAIL [a:4]=FAIL [a:5]=FAIL [a:6]=FAIL [a:7]=FAIL
  [b:1]=FAIL [b:2]=FAIL [b:3]=FAIL [b:4]=FAIL [b:5]=FAIL [b:6]=FAIL [b:7]=FAIL
  [bcas:1]=PASS [bcas:2]=FAIL [bcas:3]=FAIL [bcas:4]=PASS [bcas:5]=FAIL [bcas:6]=PASS [bcas:7]=FAIL
  [fixed:1]=PASS [fixed:2]=PASS [fixed:3]=PASS [fixed:4]=PASS [fixed:5]=PASS [fixed:6]=PASS [fixed:7]=PASS
  [bcas_touch:1]=PASS [bcas_touch:2]=PASS [bcas_touch:3]=FAIL [bcas_touch:4]=PASS [bcas_touch:5]=PASS [bcas_touch:6]=PASS [bcas_touch:7]=FAIL
  [bcas_derived:1]=PASS [bcas_derived:2]=FAIL [bcas_derived:3]=PASS [bcas_derived:4]=PASS [bcas_derived:5]=FAIL [bcas_derived:6]=PASS [bcas_derived:7]=FAIL
  [bcas_qafter:1]=PASS [bcas_qafter:2]=FAIL [bcas_qafter:3]=FAIL [bcas_qafter:4]=PASS [bcas_qafter:5]=FAIL [bcas_qafter:6]=PASS [bcas_qafter:7]=PASS
)

VARS=${1:-$ORDER}; PROPS=${2:-"1 2 3 4 5 6 7"}
fail=0; n=0; npass=0; nfail=0
t0=$(date +%s)
emit "$(printf "%-12s %-4s %-6s %-5s %8s  %s" variant prop expect got states detail)"

for v in $VARS; do for p in $PROPS; do
  n=$((n+1))
  expect="${EXPECT[$v:$p]:-}"
  if [ -z "$expect" ]; then
    emit "run.sh: no expectation for $v P$p"
    fail=1
    continue
  fi
  if [ -z "${V[$v]+x}" ]; then
    emit "run.sh: unknown variant $v"
    fail=1
    continue
  fi
  d="$OUT/$v-P$p"; rm -rf "$d"; mkdir -p "$d"; cp rco_update.pml "$d/"
  # The check runs in a subshell that is not piped, so its exit status is kept.
  # shellcheck disable=SC2086
  ( cd "$d"
    spin ${V[$v]} ${B[$p]} -DPROP=$p -a rco_update.pml > spin.log 2>&1 || { echo "spin failed" >&2; cat spin.log >&2; exit 3; }
    if [ "$p" -le 5 ]; then
      gcc -O2 -w -DSAFETY -o pan pan.c 2> gcc.log || { echo "$v P$p ERROR gcc (see $d/gcc.log)" >&2; exit 4; }
      ./pan -m100000 > pan.log 2>&1
      if ! grep -q 'errors: 0' pan.log; then
        # shortest counterexample for the write-up (-i needs -DREACH)
        gcc -O2 -w -DSAFETY -DREACH -o pan_i pan.c 2> gcc-reach.log || { echo "$v P$p ERROR gcc -DREACH (see $d/gcc-reach.log)" >&2; exit 7; }
        ./pan_i -i -m100000 > pan-shortest.log 2>&1 || { echo "$v P$p ERROR pan_i -i failed (see $d/pan-shortest.log)" >&2; exit 8; }
        grep -q 'errors: [1-9]' pan-shortest.log || { echo "$v P$p ERROR pan_i -i found no counterexample (see $d/pan-shortest.log)" >&2; exit 8; }
      fi
    else
      # Liveness: acceptance cycles. No -f: every process here does finite work, so
      # the only infinite runs are the stutter at quiescence and weak fairness
      # holds on them trivially. SPIN 6.5.1 with -f also drops that terminal
      # stutter and reports PASS for a run that ends with the pointer behind
      # (checked by hand on variant a), so -f would hide real failures.
      gcc -O2 -w -o pan pan.c 2> gcc.log || { echo "$v P$p ERROR gcc (see $d/gcc.log)" >&2; exit 4; }
      ./pan -a -m100000 > pan.log 2>&1
    fi
    if [ ! -f pan.log ] || ! grep -q "errors: [0-9]" pan.log; then
      echo "$v P$p: pan gave no result (killed?)" >&2
      exit 5
    fi
    if grep -qE "max search depth too small|out of memory|BITSTATE" pan.log; then
      echo "$v P$p: search not exhaustive, see $d/pan.log" >&2
      exit 6
    fi
    err=$(grep -o 'errors: [0-9]*' pan.log | head -1 | awk '{print $2}')
    st=$(grep -E '^ +[0-9]+ states, stored' pan.log | tail -1 | awk '{print $1}')
    if [ "${err:-1}" = "0" ]; then res=PASS; det=""; else
      res=FAIL
      spin ${V[$v]} ${B[$p]} -DPROP=$p -t rco_update.pml > trail.txt 2>&1 || { echo "$v P$p ERROR spin -t replay failed (see $d/trail.txt)" >&2; exit 9; }
      grep 'EV ' trail.txt | sed 's/^ *//' > trail-events.txt
      [ -s trail-events.txt ] || { echo "$v P$p ERROR spin -t replay gave no events (see $d/trail.txt)" >&2; exit 9; }
      det="trail: $d/trail-events.txt ($(wc -l < trail-events.txt | tr -d ' ') events)"
    fi
    rm -f pan pan_i pan.c pan.h pan.b pan.m pan.p pan.t   # keep logs and trails only
    printf "%s %s\n" "$res" "${st:-?}" > verdict.txt
    printf "%s\n" "$det" > detail.txt
  )
  rc=$?
  if [ $rc -ne 0 ]; then
    emit "$(printf "%-12s P%-3s %-6s %-5s %8s  %s" "$v" "$p" "$expect" ERROR "?" "spin or compiler failed (rc $rc), see $d")"
    fail=1
    continue
  fi
  got=$(awk '{print $1}' "$d/verdict.txt")
  st=$(awk '{print $2}' "$d/verdict.txt")
  det=$(cat "$d/detail.txt")
  mark=""
  if [ "$got" != "$expect" ]; then
    mark=" <-- differs from expectation"
    fail=1
  fi
  if [ "$got" = PASS ]; then npass=$((npass+1)); else nfail=$((nfail+1)); fi
  emit "$(printf "%-12s P%-3s %-6s %-5s %8s  %s" "$v" "$p" "$expect" "$got" "$st" "$det$mark")"
done; done

secs=$(( $(date +%s) - t0 ))
emit "spin: $(spin -V)"
[ "$n" -gt 0 ] || { emit "run.sh: no checks ran"; exit 2; }
if [ "$fail" -eq 0 ]; then
  emit "checks: $n, seconds: $secs; exit 0"
  emit "every check matches its expectation: $npass PASS, $nfail FAIL"
else
  emit "checks: $n, seconds: $secs; exit 1"
fi
exit "$fail"
