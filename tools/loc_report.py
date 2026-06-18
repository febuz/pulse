#!/usr/bin/env python3
"""Record lines-of-code by programming language for public, OSS-relevant files.

Scans the repository, classifies each tracked source file by language, counts its
lines, and writes a Markdown record to ``docs/LOC_BY_LANGUAGE.md``. Generated,
vendored, and private artifacts (VCS internals, caches, build output, local
databases, fixtures) are excluded so the record reflects only what ships in the
public open-source repository.

The record is **generated on demand and not version-controlled** (``docs/LOC_BY_LANGUAGE.md``
is gitignored), so it never needs maintaining in a PR — every PR editing the same
generated lines used to be a recurring merge-conflict source. Run this whenever you
want to see the current counts for the working tree.

Usage:
    python3 tools/loc_report.py            # write docs/LOC_BY_LANGUAGE.md (untracked)
    python3 tools/loc_report.py --print    # also print the table to stdout
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = ROOT / "docs" / "LOC_BY_LANGUAGE.md"

# Directories never counted (not public-OSS-relevant source).
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "dist", "build", ".venv", "venv", ".idea", ".vscode",
}

# Extension -> (language, is_code). Config/docs are recorded but flagged non-code.
EXT_LANG = {
    ".py": ("Python", True),
    ".jl": ("Julia", True),
    ".wgsl": ("WGSL (WebGPU)", True),
    ".rs": ("Rust", True),
    ".js": ("JavaScript", True),
    ".ts": ("TypeScript", True),
    ".sh": ("Shell", True),
    ".toml": ("TOML (config)", False),
    ".cfg": ("Config", False),
    ".ini": ("Config", False),
    ".yaml": ("YAML (config)", False),
    ".yml": ("YAML (config)", False),
    ".json": ("JSON (data)", False),
    ".md": ("Markdown (docs)", False),
}

# Files excluded by name/suffix even if extension matches.
EXCLUDE_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm", ".lock")


def _excluded(path: Path) -> bool:
    if path == OUTPUT_FILE:
        return True
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    if path.name.endswith(EXCLUDE_SUFFIXES):
        return True
    return False


def collect() -> dict[str, dict]:
    """Return {language: {"files": n, "lines": n, "is_code": bool}}."""
    stats: dict[str, dict] = {}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or _excluded(path):
            continue
        info = EXT_LANG.get(path.suffix)
        if info is None:
            continue
        lang, is_code = info
        try:
            lines = sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        bucket = stats.setdefault(lang, {"files": 0, "lines": 0, "is_code": is_code})
        bucket["files"] += 1
        bucket["lines"] += lines
    return stats


def render(stats: dict[str, dict]) -> str:
    rows = sorted(stats.items(), key=lambda kv: (-kv[1]["lines"], kv[0]))
    code_lines = sum(v["lines"] for v in stats.values() if v["is_code"])
    code_files = sum(v["files"] for v in stats.values() if v["is_code"])
    total_lines = sum(v["lines"] for v in stats.values())
    total_files = sum(v["files"] for v in stats.values())

    lines = [
        "# Knitweb — Lines of Code by Language",
        "",
        "Public, open-source-relevant source files only (VCS internals, caches,",
        "build output, local databases, and fixtures are excluded).",
        "",
        "Generated on demand by `tools/loc_report.py` and **not version-controlled**",
        "(gitignored). The counts reflect the working tree when it was generated.",
        "",
        "| Language | Files | Lines | Category |",
        "|---|---:|---:|---|",
    ]
    for lang, v in rows:
        category = "code" if v["is_code"] else "config/docs"
        lines.append(f"| {lang} | {v['files']} | {v['lines']} | {category} |")
    lines += [
        "",
        f"**Code total:** {code_files} files, {code_lines} lines.",
        f"**All tracked files:** {total_files} files, {total_lines} lines.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    stats = collect()
    report = render(stats)
    out = OUTPUT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    if "--print" in sys.argv:
        print(report)
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
