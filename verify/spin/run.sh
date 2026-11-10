#!/usr/bin/env bash
# Run every property of rco_update.pml against every variant.
# Usage: ./run.sh            (all)      ./run.sh a 1   (one variant, one property)
set -u
cd "$(dirname "$0")"
OUT=results; mkdir -p "$OUT"
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
VARS=${1:-$ORDER}; PROPS=${2:-"1 2 3 4 5 6 7"}
printf "%-12s %-4s %-5s %8s  %s\n" variant prop res states detail | tee "$OUT/summary.txt"
for v in $VARS; do for p in $PROPS; do
  d="$OUT/$v-P$p"; rm -rf "$d"; mkdir -p "$d"; cp rco_update.pml "$d/"
  ( cd "$d"
    spin ${V[$v]} ${B[$p]} -DPROP=$p -a rco_update.pml > spin.log 2>&1 || { echo "spin failed"; cat spin.log; exit 1; }
    if [ "$p" -le 5 ]; then
      gcc -O2 -w -DSAFETY -o pan pan.c 2> gcc.log || { echo "$v P$p ERROR gcc (see $d/gcc.log)"; exit 0; }
      ./pan -m100000 > pan.log 2>&1      # plain run: verdict and state count
      if ! grep -q 'errors: 0' pan.log; then
        # shortest counterexample for the write-up (-i needs -DREACH)
        gcc -O2 -w -DSAFETY -DREACH -o pan_i pan.c
        ./pan_i -i -m100000 > pan-shortest.log 2>&1
      fi
    else
      # Liveness: acceptance cycles. No -f: every process here does finite work, so
      # the only infinite runs are the stutter at quiescence and weak fairness
      # holds on them trivially. SPIN 6.5.1 with -f also drops that terminal
      # stutter and reports PASS for a run that ends with the pointer behind
      # (checked by hand on variant a), so -f would hide real failures.
      gcc -O2 -w -o pan pan.c 2> gcc.log || { echo "$v P$p ERROR gcc (see $d/gcc.log)"; exit 0; }
      ./pan -a -m100000 > pan.log 2>&1
    fi
    err=$(grep -o 'errors: [0-9]*' pan.log | head -1 | awk '{print $2}')
    st=$(grep -E '^ +[0-9]+ states, stored' pan.log | tail -1 | awk '{print $1}')
    if [ "${err:-1}" = "0" ]; then res=PASS; det=""; else
      res=FAIL
      spin ${V[$v]} ${B[$p]} -DPROP=$p -t rco_update.pml > trail.txt 2>&1
      grep 'EV ' trail.txt | sed 's/^ *//' > trail-events.txt
      det="trail: $d/trail-events.txt ($(wc -l < trail-events.txt) events)"
    fi
    rm -f pan pan_i pan.c pan.h pan.b pan.m pan.p pan.t   # keep logs and trails only
    printf "%-12s P%-3s %-5s %8s  %s\n" "$v" "$p" "$res" "${st:-?}" "$det"
  ) | tee -a "$OUT/summary.txt"
done; done
echo "spin: $(spin -V)" | tee -a "$OUT/summary.txt"
