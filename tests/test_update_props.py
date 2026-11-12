"""SPIN properties P1 to P7 (verify/spin/rco_update.pml) as tests on the real update.

Each runs against the in-memory fake and SQLite. Crashes are injected
through update's test-only hook. The seeded interleaving test stands in for
Hypothesis: a scheduler lets one updater thread run at a time and picks the
next at each hook point from a fixed-seed random stream.
"""
import random
import threading

import pytest

from rco.forest_model import Prose, ProseSource, is_stale
from rco.store import MemoryStore, NodeKind
from rco.store.sqlite import SCHEMA_SQL, SqliteStore
from rco.update import update
from update_history import make_history


class Crash(Exception):
    pass


@pytest.fixture(params=["memory", "sqlite"])
def make_store(request, tmp_path):
    n = iter(range(10_000))

    def make():
        if request.param == "memory":
            return MemoryStore()
        return SqliteStore(tmp_path / f"s{next(n)}" / "rco.db")
    return make


@pytest.fixture(scope="module")
def hist(tmp_path_factory):
    return make_history(tmp_path_factory.mktemp("h") / "repo")


def _complete(store, sha):
    rows = store.list_by_sha(sha)
    return bool(rows) and all(store.get_node(NodeKind.TREE, r.module_hash) for r in rows)


def _crash_at(point, detail=None):
    def hook(p, d):
        if p == point and (detail is None or d == detail):
            raise Crash(f"{p} {d}")
    return hook


def _drain(store):
    """Stand-in prose worker: write prose for each queued TreeHash, then dequeue."""
    for m, th in store.queued():
        store.put_prose(m, Prose(f"prose {m}", ProseSource.MINED, th))
        store.dequeue(m, th)


def test_p1_late_finisher_never_moves_pointer_back(hist, make_store):
    repo, shas = hist
    s = make_store()
    update(repo, s, shas[0])

    def late(p, d):
        if p == "swap":
            update(repo, s, shas[3])  # a newer update finishes first
    r = update(repo, s, shas[1], hook=late)
    assert not r.swapped and s.get_pointer("main") == shas[3]


def test_p2_published_sha_has_every_tree(hist, make_store):
    repo, shas = hist
    s = make_store()
    update(repo, s, shas[0])
    for point in ("extract", "index", "swap"):
        with pytest.raises(Crash):
            update(repo, s, shas[2], hook=_crash_at(point))
        assert s.get_pointer("main") == shas[0] and _complete(s, shas[0])
    update(repo, s, shas[2])
    assert _complete(s, s.get_pointer("main"))


def test_p3_staleness_from_tree_hashes_no_stale_field(hist, make_store):
    repo, shas = hist
    s = make_store()
    update(repo, s, shas[1])
    _drain(s)
    update(repo, s, shas[2])  # util's contract moves
    now = {r.module: r.tree_hash for r in s.list_by_sha(s.get_pointer("main"))}
    assert is_stale(s.get_prose("util"), now["util"])
    assert not is_stale(s.get_prose("pkg"), now["pkg"])
    assert "stale" not in SCHEMA_SQL.lower()


def test_p4_twice_equals_once(hist, tmp_path):
    repo, shas = hist
    a, b = SqliteStore(tmp_path / "a.db"), SqliteStore(tmp_path / "b.db")
    for sha in shas:
        update(repo, a, sha)
    for sha in shas:
        update(repo, b, sha)
        update(repo, b, sha)  # redelivery
        update(repo, b, shas[max(0, shas.index(sha) - 1)])  # duplicate of an older job
    assert a.dump() == b.dump()


def test_p5_crash_before_extract_is_healed(hist, make_store):
    repo, shas = hist
    s = make_store()
    update(repo, s, shas[1])
    with pytest.raises(Crash):
        update(repo, s, shas[2], hook=_crash_at("extract", "util"))
    # D's diff from C would not name util, but the pointer is still B and
    # rule 2 checks every module of D anyway
    r = update(repo, s, shas[3])
    assert r.swapped and _complete(s, shas[3]) and "util" in r.extracted


def test_p6_pointer_ends_at_latest(hist, make_store):
    repo, shas = hist
    for seed in range(5):
        s = make_store()
        order = shas[:]
        random.Random(seed).shuffle(order)
        for sha in order:
            update(repo, s, sha)
        assert s.get_pointer("main") == shas[-1]


def test_p7_queue_drained_means_every_forest_fresh(hist, make_store):
    repo, shas = hist
    s = make_store()
    for sha in shas:
        update(repo, s, sha)
    _drain(s)
    for r in s.list_by_sha(s.get_pointer("main")):
        p = s.get_prose(r.module, r.tree_hash)
        assert p is not None and not is_stale(p, r.tree_hash)


class Scheduler:
    """Run one thread at a time; at every hook point a seeded choice picks who runs next."""

    def __init__(self, seed: int, crash_rate: float = 0.0):
        self.rng = random.Random(seed)
        self.crash_rate = crash_rate
        self.cv = threading.Condition()
        self.turn: int | None = None
        self.live: set[int] = set()
        self.crashes = 0

    def _pick(self):
        self.turn = self.rng.choice(sorted(self.live)) if self.live else None
        self.cv.notify_all()

    def hook(self, me: int):
        def h(point, detail):
            with self.cv:
                crash = self.rng.random() < self.crash_rate
                self._pick()
                self.cv.wait_for(lambda: self.turn == me)
            if crash:
                self.crashes += 1
                raise Crash(point)
        return h

    def run(self, jobs):
        def body(i, job):
            with self.cv:
                self.cv.wait_for(lambda: self.turn == i)
            try:
                job(self.hook(i))
            except Crash:
                pass
            finally:
                with self.cv:
                    self.live.discard(i)
                    self._pick()

        ts = [threading.Thread(target=body, args=(i, j)) for i, j in enumerate(jobs)]
        with self.cv:
            self.live = set(range(len(jobs)))
        for t in ts:
            t.start()
        with self.cv:
            self._pick()
        for t in ts:
            t.join(timeout=60)
            assert not t.is_alive(), "scheduler deadlock"


@pytest.mark.parametrize("seed", range(12))
def test_seeded_interleavings_keep_p1_p2_p6(hist, make_store, seed):
    repo, shas = hist
    s = make_store()
    jobs = []
    for sha in shas[1:] + [shas[2], shas[1]]:  # every merge plus two redeliveries
        jobs.append(lambda h, sha=sha: update(repo, s, sha, hook=h))
    sched = Scheduler(seed, crash_rate=0.0 if seed % 2 == 0 else 0.05)
    seen: list[str] = []
    orig = s.set_pointer

    def watch(name, old, new):
        ok = orig(name, old, new)
        if ok:
            seen.append(new)
        return ok
    s.set_pointer = watch
    sched.run(jobs)
    order = {sha: i for i, sha in enumerate(shas)}
    assert [order[x] for x in seen] == sorted(order[x] for x in seen)  # P1: only forward
    assert seen and _complete(s, s.get_pointer("main"))  # P2
    if sched.crashes == 0:
        assert s.get_pointer("main") == shas[-1]  # P6


def test_rule2_checks_every_module_not_only_the_diff(hist, make_store):
    """A pointer whose SHA is not in this store (set by another writer, or a
    store restored from an older copy) still gets a complete NEW."""
    repo, shas = hist
    s = make_store()
    assert s.set_pointer("main", None, shas[1])
    r = update(repo, s, shas[2])
    assert r.swapped and _complete(s, shas[2])
    assert set(r.extracted) == {row.module for row in s.list_by_sha(shas[2])}


def test_rule4_no_queue_without_a_swap(hist, make_store):
    repo, shas = hist
    s = make_store()
    update(repo, s, shas[1])
    _drain(s)
    with pytest.raises(Crash):
        update(repo, s, shas[2], hook=_crash_at("swap"))  # util's contract moved
    assert s.queued() == ()

    def late(p, d):
        if p == "swap":
            update(repo, s, shas[4])
            _drain(s)
    r = update(repo, s, shas[2], hook=late)
    assert not r.swapped and s.queued() == ()
