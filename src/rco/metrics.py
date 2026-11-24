"""Token-efficiency metrics for one pull request, and a roll-up over many.

One file per pull request, rco-metrics.json, compares what an agent would
read without rco (every changed file at the head, plus the in-repo files
those import, one hop) with what it reads with rco (the `rco pack --for
CHANGED` markdown). Both sides are counted in bytes and in tokens of one
tokenizer. The file schema is an integer 1, the same document the-mill
already uploads, so a roll-up can mix sources.

    rco metrics measure --base SHA --head SHA --pr N --pack pack.md \\
        --out rco-metrics.json --format json
    rco metrics check rco-metrics.json
    rco metrics summary DIR_OR_FILE...
    rco metrics summary DIR --json
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from rco import __version__, gitread
from rco.imports import file_graph
from rco.output import Exit, UsageError, note

SCHEMA = 1
SCHEMA_SUMMARY = "rco.metrics.summary/1"
TOKENIZER = "tiktoken o200k_base"
FIELDS = ("schema", "pr", "base_sha", "head_sha", "rco_version", "tokenizer",
          "changed_files", "without_rco", "with_rco", "ratio_tokens")
SIDE_FIELDS = ("files", "bytes", "tokens")
METRICS_NAME = "rco-metrics.json"
_PACK_USED = re.compile(r"^# budget used: ~(\d+) / (\d+) estimated tokens", re.M)


def pack_footer(text: str) -> tuple[int | None, int | None]:
    """used and budget from a pack markdown footer; (None, None) if absent."""
    m = _PACK_USED.search(text or "")
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))



def estimate(text: str) -> int:
    """rco's own estimate (characters / 4), the fallback count."""
    return len(text) // 4


def counter() -> tuple[str, Callable[[str], int]]:
    """The tokenizer the metrics use, and its name; rco's estimate if absent."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("o200k_base")
    except Exception as e:  # missing package or no encoding file
        note(f"tiktoken unavailable ({type(e).__name__}); using chars/4")
        return "estimate chars/4", estimate
    return TOKENIZER, lambda s: len(enc.encode(s, disallowed_special=()))


def read_set(changed: Iterable[str], graph: Mapping[str, Sequence[str]]) -> list[str]:
    """Changed files plus the in-repo files each imports (one hop), sorted."""
    out = set(changed)
    for p in list(out):
        out.update(graph.get(p, ()))
    return sorted(out)


def measure(changed: Sequence[str], graph: Mapping[str, Sequence[str]],
            blobs: Mapping[str, bytes], pack_text: str,
            count: Callable[[str], int]) -> dict:
    """Both sides of the comparison; files not in blobs (deleted) are skipped."""
    present = [p for p in changed if p in blobs]
    files = [p for p in read_set(present, graph) if p in blobs]
    text = "".join(blobs[p].decode("utf-8", "replace") for p in files)
    without = {"files": len(files), "bytes": sum(len(blobs[p]) for p in files),
               "tokens": count(text)}
    with_ = {"files": len(present), "bytes": len(pack_text.encode("utf-8")),
             "tokens": count(pack_text)}
    ratio = (round(with_["tokens"] / without["tokens"], 4)
             if without["tokens"] else None)
    return {"changed_files": len(present), "without_rco": without, "with_rco": with_,
            "ratio_tokens": ratio}


def _regular(entries: Sequence[gitread.Entry]) -> dict[str, str]:
    """path -> blob id of every regular file (not a symlink or submodule)."""
    return {e.path: e.blob for e in entries
            if e.mode not in (gitread.MODE_SUBMODULE, gitread.MODE_SYMLINK)}


def write_metrics(repo: Path, base: str, head: str, pr: int, pack_text: str,
                  budget: int, count: Callable[[str], int], tokenizer: str) -> dict:
    """Build the schema-1 document for one pull request at two resolved SHAs."""
    base_sha = gitread.resolve(repo, base)
    head_sha = gitread.resolve(repo, head)
    tree = _regular(gitread.ls_tree(repo, head_sha))
    from rco.diff import changed_paths
    from rco.extract.langs import lang_of
    changed = changed_paths(gitread.ls_tree(repo, base_sha),
                            gitread.ls_tree(repo, head_sha))
    code_paths = [p for p in tree if p.endswith(".py") or lang_of(p)]
    raw_code = gitread.read_blobs(repo, (tree[p] for p in code_paths))
    code = {p: raw_code[tree[p]] for p in code_paths}
    graph = file_graph(sorted(tree), code)
    present = [p for p in changed if p in tree]
    want = [p for p in read_set(present, graph) if p in tree]
    raw = gitread.read_blobs(repo, (tree[p] for p in want))
    blobs = {p: raw[tree[p]] for p in want}
    used, footer_budget = pack_footer(pack_text)
    pack_budget = budget if footer_budget is None else footer_budget
    overrun = 0 if used is None else max(0, used - pack_budget)
    doc = {
        "schema": SCHEMA,
        "pr": pr,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "rco_version": __version__,
        "tokenizer": tokenizer,
        "pack_budget": pack_budget,
        "pack_overrun": overrun,
        **measure(changed, graph, blobs, pack_text, count),
    }
    if used is not None:
        doc["pack_used"] = used
    return doc


def check(doc: object) -> list[str]:
    """A list of missing or wrong-typed fields; empty when the file is usable."""
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    bad = [f"missing {k}" for k in FIELDS if k not in doc]
    for side in ("without_rco", "with_rco"):
        s = doc.get(side)
        if isinstance(s, dict):
            bad += [f"{side}.{k} not an int" for k in SIDE_FIELDS
                    if not isinstance(s.get(k), int)]
        elif side in doc:
            bad.append(f"{side} not an object")
    if doc.get("schema") != SCHEMA and "schema" in doc:
        bad.append(f"schema {doc.get('schema')!r} != {SCHEMA}")
    return bad


def load_file(path: Path) -> dict:
    """A usable schema-1 document, or UsageError."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise UsageError(f"unreadable metrics {path}: {e}") from None
    if not text.strip():
        raise UsageError(f"unreadable metrics {path}: empty")
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise UsageError(f"unreadable metrics {path}: not JSON: {e}") from None
    bad = check(doc)
    if bad:
        raise UsageError(f"unreadable metrics {path}: {bad[0]}")
    return doc


def _collect(paths: Sequence[Path]) -> tuple[list[tuple[Path, dict]], int]:
    """Load documents from files and directories; skip bad files only in dirs."""
    found: list[tuple[Path, dict]] = []
    skipped = 0
    if not paths:
        raise UsageError("metrics summary needs a file or directory")
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            found.append((p, load_file(p)))
            continue
        if not p.is_dir():
            raise UsageError(f"not a file or directory: {p}")
        matches = sorted(q for q in p.rglob(METRICS_NAME) if q.is_file())
        for q in matches:
            try:
                found.append((q, load_file(q)))
            except UsageError as e:
                note(str(e))
                skipped += 1
    return found, skipped


def _key(doc: dict) -> tuple[object, object]:
    return (doc.get("pr"), doc.get("head_sha"))


def _ratio(doc: dict) -> float | None:
    r = doc.get("ratio_tokens")
    return r if isinstance(r, (int, float)) else None


def _saved(doc: dict, field: str) -> int:
    return int(doc["without_rco"][field]) - int(doc["with_rco"][field])


def _pr_ref(doc: dict, path: Path) -> dict:
    out = {"path": path.as_posix(), "ratio_tokens": _ratio(doc),
           "head_sha": doc.get("head_sha"), "pr": doc.get("pr")}
    return out


def summarize(paths: Sequence[Path]) -> "SummaryReport":
    """A roll-up of many rco-metrics.json files into one trend."""
    found, skipped = _collect(paths)
    unique: dict[tuple[object, object], tuple[Path, dict]] = {}
    for path, doc in found:
        k = _key(doc)
        if k in unique:
            skipped += 1
            continue
        unique[k] = (path, doc)
    rows = list(unique.values())
    ratios = [_ratio(d) for _, d in rows if _ratio(d) is not None]
    mean = round(sum(ratios) / len(ratios), 4) if ratios else None
    median = round(float(statistics.median(ratios)), 4) if ratios else None
    bytes_saved = sum(_saved(d, "bytes") for _, d in rows)
    tokens_saved = sum(_saved(d, "tokens") for _, d in rows)
    ranked = [( _ratio(d), path, d) for path, d in rows if _ratio(d) is not None]
    best = worst = None
    if ranked:
        # lower ratio is more savings (best); ties: lower pr, then head_sha
        def sort_key(item: tuple[float, Path, dict]) -> tuple:
            r, path, d = item
            pr = d.get("pr")
            prn = pr if isinstance(pr, int) else 0
            return (r, prn, str(d.get("head_sha") or ""), path.as_posix())
        ordered = sorted(ranked, key=sort_key)
        best = _pr_ref(ordered[0][2], ordered[0][1])
        worst = _pr_ref(ordered[-1][2], ordered[-1][1])
    if not rows:
        raise UsageError("no usable rco-metrics.json files")
    return SummaryReport(
        count=len(rows),
        mean_ratio=mean,
        median_ratio=median,
        bytes_saved=bytes_saved,
        tokens_saved=tokens_saved,
        best=best,
        worst=worst,
        skipped=skipped,
    )


def _fmt_ratio(r: float | None) -> str:
    return "n/a" if r is None else f"{r:.4f}"


def _fmt_pr(item: dict | None, label: str) -> str:
    if item is None:
        return f"{label}: n/a"
    pr = item.get("pr")
    who = f"PR {pr}" if pr is not None else (item.get("head_sha") or item.get("path"))
    return f"{label}: {who} (ratio {_fmt_ratio(item.get('ratio_tokens'))})"


@dataclass(frozen=True)
class SummaryReport:
    count: int
    mean_ratio: float | None
    median_ratio: float | None
    bytes_saved: int
    tokens_saved: int
    best: dict | None
    worst: dict | None
    skipped: int

    def to_obj(self) -> dict:
        return {
            "schema": SCHEMA_SUMMARY,
            "count": self.count,
            "mean_ratio": self.mean_ratio,
            "median_ratio": self.median_ratio,
            "bytes_saved": self.bytes_saved,
            "tokens_saved": self.tokens_saved,
            "best": self.best,
            "worst": self.worst,
            "skipped": self.skipped,
        }

    def to_markdown(self) -> str:
        lines = [
            "# rco metrics summary",
            "",
            f"- count: {self.count}",
            f"- mean ratio: {_fmt_ratio(self.mean_ratio)}",
            f"- median ratio: {_fmt_ratio(self.median_ratio)}",
            f"- bytes saved: {self.bytes_saved}",
            f"- tokens saved: {self.tokens_saved}",
            f"- {_fmt_pr(self.best, 'best')}",
            f"- {_fmt_pr(self.worst, 'worst')}",
        ]
        if self.skipped:
            lines.append(f"- skipped: {self.skipped}")
        return "\n".join(lines) + "\n"

    def exit_code(self) -> Exit:
        return Exit.OK


@dataclass(frozen=True)
class MetricsReport:
    doc: dict

    def to_obj(self) -> dict:
        return self.doc

    def to_markdown(self) -> str:
        pr = self.doc.get("pr")
        ratio = self.doc.get("ratio_tokens")
        lines = [
            "# rco metrics",
            "",
            f"- pr: {pr}",
            f"- changed files: {self.doc.get('changed_files')}",
            f"- bytes without / with: "
            f"{self.doc['without_rco']['bytes']} / {self.doc['with_rco']['bytes']}",
            f"- tokens without / with: "
            f"{self.doc['without_rco']['tokens']} / {self.doc['with_rco']['tokens']}",
            f"- ratio: {_fmt_ratio(ratio if isinstance(ratio, (int, float)) else None)}",
        ]
        overrun = self.doc.get("pack_overrun") or 0
        if overrun:
            used = self.doc.get("pack_used")
            budget = self.doc.get("pack_budget")
            lines.append(
                f"- warning: pack used ~{used} / {budget} estimated tokens (over budget)"
            )
        return "\n".join(lines) + "\n"

    def exit_code(self) -> Exit:
        return Exit.OK


def check_file(path: Path) -> Exit:
    """Exit 1 unless the metrics file is present and complete."""
    if not path.is_file() or path.stat().st_size == 0:
        note(f"{path} missing or empty")
        return Exit.FINDINGS
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        note(f"{path}: not JSON: {e}")
        return Exit.FINDINGS
    except OSError as e:
        note(f"{path}: {e}")
        return Exit.FINDINGS
    bad = check(doc)
    for b in bad:
        note(f"{path}: {b}")
    return Exit.FINDINGS if bad else Exit.OK
