import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class FileStat:
    path: str
    tokens: int
    lines: int


def _is_ignored_path(p: Path) -> bool:
    ignore_parts = {
        ".git",
        "__pycache__",
        ".venv",
        "node_modules",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
    }
    return any(part in ignore_parts for part in p.parts)


def _iter_files(target: Path) -> Iterable[Path]:
    if target.is_file():
        yield target
        return

    if not target.exists():
        return

    for p in target.rglob("*"):
        if not p.is_file():
            continue
        if _is_ignored_path(p):
            continue
        yield p


def _read_text_file(path: Path, max_bytes: Optional[int]) -> Optional[Tuple[str, int]]:
    try:
        data = path.read_bytes()
    except Exception:
        return None

    if max_bytes is not None and len(data) > max_bytes:
        data = data[:max_bytes]

    if b"\x00" in data:
        return None

    text = data.decode("utf-8", errors="ignore")
    return text, len(data)


def _build_token_counter(model: Optional[str], encoding: Optional[str]) -> Tuple[Callable[[str], int], str]:
    try:
        import tiktoken  # type: ignore

        if model:
            enc = tiktoken.encoding_for_model(model)
            return (lambda s: len(enc.encode(s))), f"tiktoken(model={model})"
        if encoding:
            enc = tiktoken.get_encoding(encoding)
            return (lambda s: len(enc.encode(s))), f"tiktoken(encoding={encoding})"

        enc = tiktoken.get_encoding("cl100k_base")
        return (lambda s: len(enc.encode(s))), "tiktoken(encoding=cl100k_base)"
    except Exception:
        token_re = re.compile(r"\w+|[^\w\s]", re.UNICODE)
        return (lambda s: len(token_re.findall(s))), "approx(regex)"


def _collect_stats(
    target: Path,
    counter: Callable[[str], int],
    max_bytes: Optional[int],
    include_exts: Optional[List[str]],
    exclude_exts: Optional[List[str]],
) -> List[FileStat]:
    out: List[FileStat] = []

    for p in _iter_files(target):
        if p.suffix.lower() == ".log":
            continue

        suffix = p.suffix.lower()
        if include_exts is not None and suffix not in include_exts:
            continue
        if exclude_exts is not None and suffix in exclude_exts:
            continue

        res = _read_text_file(p, max_bytes=max_bytes)
        if res is None:
            continue
        text, size = res
        lines = len(text.splitlines())
        out.append(FileStat(path=str(p), tokens=counter(text), lines=lines))

    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", help="File or directory paths")
    parser.add_argument("--model", default=None)
    parser.add_argument("--encoding", default=None)
    parser.add_argument("--max-bytes", type=int, default=None)
    parser.add_argument("--include-ext", action="append", default=None)
    parser.add_argument("--exclude-ext", action="append", default=None)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    include_exts = None
    if args.include_ext:
        include_exts = [e if e.startswith(".") else f".{e}" for e in args.include_ext]
        include_exts = [e.lower() for e in include_exts]

    exclude_exts = None
    if args.exclude_ext:
        exclude_exts = [e if e.startswith(".") else f".{e}" for e in args.exclude_ext]
        exclude_exts = [e.lower() for e in exclude_exts]

    counter, method = _build_token_counter(args.model, args.encoding)

    results: Dict[str, Dict[str, object]] = {}
    all_files: List[FileStat] = []

    for raw in args.paths:
        p = Path(raw).expanduser()
        stats = _collect_stats(
            p,
            counter=counter,
            max_bytes=args.max_bytes,
            include_exts=include_exts,
            exclude_exts=exclude_exts,
        )
        total_tokens = sum(s.tokens for s in stats)
        total_lines = sum(s.lines for s in stats)
        results[str(p)] = {
            "files": len(stats),
            "tokens": total_tokens,
            "lines": total_lines,
        }
        all_files.extend(stats)

    all_tokens = sum(s.tokens for s in all_files)
    all_lines = sum(s.lines for s in all_files)
    top = sorted(all_files, key=lambda s: s.tokens, reverse=True)[: max(0, args.top)]

    if args.json:
        payload = {
            "method": method,
            "targets": results,
            "total": {"files": len(all_files), "tokens": all_tokens, "lines": all_lines},
            "top": [
                {"path": s.path, "tokens": s.tokens, "lines": s.lines}
                for s in top
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"token_method: {method}")
    for k, v in results.items():
        print(
            f"- {k}: files={v['files']}, tokens={v['tokens']}, lines={v['lines']}"
        )
    print(f"total: files={len(all_files)}, tokens={all_tokens}, lines={all_lines}")
    if top:
        print("top_files_by_tokens:")
        for s in top:
            print(f"- {s.tokens}\t{s.lines}\t{s.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

