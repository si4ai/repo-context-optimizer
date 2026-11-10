/*
 * rco_update.pml -- SPIN model of the `rco update --from OLD --to NEW`
 * protocol in ../../README.md ("Fingerprints", "Incremental update after
 * each merge", "Verification"). Design-only model; there is no rco code.
 *
 * World
 *   Default branch: linear SHAs 0..NS, each a descendant of the previous,
 *   so "NEW descends from P" (git merge-base --is-ancestor P NEW) is NEW >= P.
 *   Two modules, m0 and m1. C(s,m) = module content (git tree id) at SHA s.
 *   T(m,c) = TreeHash of the extracted Tree for content c (deterministic).
 *
 *        sha0  sha1  sha2  sha3
 *   m0    c0    c1    c1    c1     c0->c1 at sha1 is a contract change
 *   m1    c0    c0    c1    c2     c0->c1 internal (same TreeHash), c1->c2 contract
 *
 *   store[m*NC+c]  content-addressed Tree store (Tree for module m, content c)
 *   idx[s]       commit SHA -> module hashes entry for s
 *   pointer        the published SHA ("main" pointer)
 *   forestT[m]     TreeHash the Forest prose of m was written for
 *   stale[m]       stored stale flag (STALE==0 only)
 *
 * Actors: merger (merges 1..NS, can redeliver an old job = duplicate run),
 *   NU updater processes (any interleaving = races and late finishers;
 *   nondeterministic crash midway, always before the pointer write),
 *   one async Forest prose rewriter.
 *
 * Knobs (pass with spin -DNAME=VALUE):
 *   GUARD  0 naive pointer write: publish if the value's hash checks out
 *            (README: "idempotent, with a hash check on the value")
 *          1 guarded, check then write: read pointer, is-ancestor check,
 *            then write (two separate steps, as a script would do it)
 *          2 guarded, atomic compare-and-swap of check+write
 *   TOUCH  0 re-extract only modules in `git diff OLD NEW` (README step 1-2)
 *          1 before the pointer write, check every module of NEW against the
 *            store (cheap rev-parse per module) and extract any miss
 *   STALE  0 Forest staleness is a stored flag set in step 5, cleared by the rewriter
 *          1 staleness is derived at read time: ForestHash's TreeHash != TreeHash
 *   QAFTER 0 prose rewrite queued in step 5 (before the pointer write)
 *          1 prose rewrite queued after the pointer write
 *   PROP   which property this build checks (1..7, see below)
 *   MAXCRASH, MAXDUP  bounds on crashes and duplicate deliveries
 */

#ifndef GUARD
#define GUARD 0
#endif
#ifndef TOUCH
#define TOUCH 0
#endif
#ifndef STALE
#define STALE 0
#endif
#ifndef QAFTER
#define QAFTER 0
#endif
#ifndef PROP
#define PROP 1
#endif
#ifndef MAXCRASH
#define MAXCRASH 1
#endif
#ifndef MAXDUP
#define MAXDUP 1
#endif

#define NS 3          /* latest SHA */
#define NM 2          /* modules */
#define NC 3          /* content values per module */
#define NU 2          /* concurrent updaters */

#define C(s,m)  cont[(s)*NM+(m)]
#define T(m,c)  th[(m)*NC+(c)]
#define TREE_AT(s,m) T(m, C(s,m))
#define HAS_TREES(s) (idx[s] && store[0*NC+C(s,0)] && store[1*NC+C(s,1)])

byte cont[(NS+1)*NM];
byte th[NM*NC];

bool store[NM*NC];
bool idx[NS+1];
byte pointer = 0;
byte forestT[NM];
bool stale[NM];

/* bookkeeping for properties */
bool backward = false;     /* P1: a pointer write moved it backwards */
byte crashes = 0;
byte dups = 0;
byte sent = 0;             /* jobs delivered */
byte finished = 0;         /* jobs done or crashed */
bool mergerDone = false;
bool lastCrashed = false;  /* some run of the job for the latest merge crashed */

chan jobs = [8] of { byte, byte };   /* (OLD, NEW) */
chan rq = [16] of { byte };          /* prose rewrite queue: module */

/* shown as current by `rco pack` for module m */
#if STALE == 0
#define SHOWN_CURRENT(m) (!stale[m])
#else
#define SHOWN_CURRENT(m) (forestT[m] == TREE_AT(pointer,m))
#endif
#define FOREST_OK(m) (!SHOWN_CURRENT(m) || forestT[m] == TREE_AT(pointer,m))
#define FRESH(m) (forestT[m] == TREE_AT(pointer,m))

inline maybe_crash() {
  if
  :: atomic { (crashes < MAXCRASH) -> crashes++ };
       printf("EV updater %d crashed during update %d->%d\n", _pid, o, n);
       if :: (n == NS) -> lastCrashed = true :: else -> skip fi;
       goto crashed
  :: true -> skip
  fi
}

inline extract(mm) {
  /* step 3: re-extract (parse, no LLM) and publish to the content store */
  c = C(n, mm);
  if
  :: !store[mm*NC+c] -> store[mm*NC+c] = true;
       printf("EV updater %d stored Tree m%d/c%d (update %d->%d)\n", _pid, mm, c, o, n)
  :: else -> skip       /* content-addressed: already there, no redo */
  fi
}

proctype updater() {
  byte o, n, m, c;
end_idle:
  do
  :: jobs?o,n ->
       printf("EV updater %d starts update %d->%d\n", _pid, o, n);
       /* steps 1-5 for each module */
       m = 0;
       do
       :: (m < NM) ->
            maybe_crash();
            if
            :: (TOUCH == 1 || C(o,m) != C(n,m)) ->   /* step 1-2: touched? */
                 extract(m);
                 if
                 :: (T(m,C(o,m)) != T(m,C(n,m))) ->  /* step 5: contract moved */
#if STALE == 0
                      stale[m] = true; printf("EV updater %d marked Forest m%d stale\n", _pid, m);
#endif
#if QAFTER == 0
                      rq!m;
#else
                      skip;
#endif
                 :: else -> skip                     /* step 4: copy Forest forward */
                 fi
            :: else -> skip
            fi;
            m++
       :: else -> break
       od;
       idx[n] = true;          /* commit SHA -> module hashes */
       printf("EV updater %d wrote index for sha%d\n", _pid, n);
       maybe_crash();
       /* pointer write, last */
#if GUARD == 0
       atomic {
         if
         :: idx[n] -> if :: (n < pointer) -> backward = true :: else -> skip fi;
                        printf("EV updater %d writes pointer sha%d (was sha%d)\n", _pid, n, pointer);
                        pointer = n
         :: else -> skip
         fi
       }
#elif GUARD == 1
       if
       :: (n >= pointer) ->       /* git merge-base --is-ancestor POINTER NEW */
            printf("EV updater %d checks sha%d descends from pointer sha%d: yes\n", _pid, n, pointer);
            atomic { printf("EV updater %d writes pointer sha%d (was sha%d)\n", _pid, n, pointer); if :: (n < pointer) -> backward = true :: else -> skip fi;
                     pointer = n }
       :: else -> skip
       fi;
#else
       atomic {
         if
         :: (n >= pointer) ->
              printf("EV updater %d CAS pointer sha%d (was sha%d)\n", _pid, n, pointer);
              pointer = n
         :: else -> skip
         fi
       }
#endif
#if QAFTER == 1
       m = 0;
       do
       :: (m < NM) ->
            if
            :: (T(m,C(o,m)) != T(m,C(n,m))) -> rq!m
            :: else -> skip
            fi;
            m++
       :: else -> break
       od;
#endif
       printf("EV updater %d finished update %d->%d, pointer=sha%d\n", _pid, o, n, pointer);
       finished++;
       goto next;
crashed:
       finished++;
next:
       skip
  od
}

proctype rewriter() {
  byte m, p, t;
end_idle:
  do
  :: rq?m ->
       p = pointer;              /* read the published SHA */
       printf("EV rewriter starts prose for m%d at pointer sha%d\n", m, p);
       t = TREE_AT(p, m);        /* Tree it will describe */
       /* ... LLM prose generation happens here (async, slow) ... */
       atomic { forestT[m] = t;
#if STALE == 0
                stale[m] = false;
#endif
              }
       printf("EV rewriter wrote Forest m%d for TreeHash %d (fresh)\n", m, t)
  od
}

proctype merger() {
  byte s = 1, k;
  do
  :: (s <= NS) ->
       printf("EV merge sha%d, job %d->%d queued\n", s, s-1, s);
       jobs!(s-1),s;               /* CI on push to the default branch */
       sent++;
       if                          /* redelivery of any earlier job (webhook retry) */
       :: (dups < MAXDUP) ->
            k = 1;
            do
            :: (k < s) -> k++
            :: break
            od;
            dups++;
            printf("EV duplicate job %d->%d queued\n", k-1, k);
            jobs!(k-1),k; sent++
       :: true -> skip
       fi;
       s++
  :: else -> break
  od;
  mergerDone = true
}

#define QUIET (mergerDone && finished == sent)

/* ---- properties ---- */
#if PROP == 1
/* P1 pointer only moves forward */
proctype mon() { end: atomic { backward -> assert(!backward) } }
#elif PROP == 2
/* P2 a published pointer always has its Trees in the store */
proctype mon() { end: atomic { !HAS_TREES(pointer) -> assert(HAS_TREES(pointer)) } }
#elif PROP == 3
/* P3 a stale Forest is never shown as current */
proctype mon() {
  end: atomic { !(FOREST_OK(0) && FOREST_OK(1)) -> assert(FOREST_OK(0) && FOREST_OK(1)) }
}
#elif PROP == 4
/* P4 duplicate runs: final state equals the single-run final state
 * (no crashes in this build; MAXDUP duplicates). Single run of 1..NS ends
 * with pointer=NS, every index entry, and exactly the reachable Trees. */
proctype mon() {
  end: atomic { QUIET ->
    assert(pointer == NS && idx[1] && idx[2] && idx[3] &&
           store[0*NC+0] && store[0*NC+1] && !store[0*NC+2] &&
           store[1*NC+0] && store[1*NC+1] && store[1*NC+2]) }
}
#elif PROP == 5
/* P5 no changed module is skipped: once all updates have finished and the
 * latest merge's update did not crash, every module's Tree for the latest SHA
 * is in the store and indexed */
proctype mon() {
  end: atomic { QUIET -> assert(lastCrashed || HAS_TREES(NS)) }
}
#elif PROP == 6
/* P6 liveness: merges stop; unless the latest merge's update crashes,
 * the pointer eventually reaches the latest SHA and stays there */
ltl p6 { ([] !lastCrashed) -> <> [] (pointer == NS) }
#elif PROP == 7
/* P7 (extra) liveness: with no crashes, every Forest eventually becomes
 * fresh for the published pointer and stays fresh */
ltl p7 { ([] (crashes == 0)) -> <> [] (FRESH(0) && FRESH(1)) }
#endif

init {
  atomic {
    /* contents */
    C(0,0)=0; C(0,1)=0;
    C(1,0)=1; C(1,1)=0;
    C(2,0)=1; C(2,1)=1;
    C(3,0)=1; C(3,1)=2;
    /* TreeHash of each content */
    T(0,0)=0; T(0,1)=1; T(0,2)=2;
    T(1,0)=0; T(1,1)=0; T(1,2)=1;
    /* SHA 0 fully published, Forest fresh */
    store[0*NC+0] = true; store[1*NC+0] = true;
    idx[0] = true; pointer = 0;
    forestT[0] = 0; forestT[1] = 0;
    run merger(); run rewriter();
    run updater(); run updater();
#if PROP <= 5
    run mon()
#endif
  }
}
