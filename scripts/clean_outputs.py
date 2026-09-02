#!/usr/bin/env python3
"""Cleanup utility script to remove generated JSON output files, temp files, and caches.

Usage:
    python scripts/clean_outputs.py                       # Cleans default output/ directory
    python scripts/clean_outputs.py --dry-run             # Previews deletions without deleting
    python scripts/clean_outputs.py --all                 # Cleans outputs, temp files, and caches
    python scripts/clean_outputs.py --dir custom_output   # Cleans specific output directory
    python scripts/clean_outputs.py --json-only           # Cleans only .json files in target dir
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Protected folders that must NEVER be deleted
PROTECTED_PATHS = {
    PROJECT_ROOT,
    PROJECT_ROOT / "src",
    PROJECT_ROOT / "tests",
    PROJECT_ROOT / "scripts",
    PROJECT_ROOT / "test_resumes_sample",
    PROJECT_ROOT / "training_Resume_data",
}


def _is_safe_target(target: Path) -> bool:
    resolved = target.resolve()
    if resolved in PROTECTED_PATHS:
        return False
    # Check if target is inside protected source dirs (not in output or logs or caches)
    for protected in PROTECTED_PATHS:
        if protected != PROJECT_ROOT and (resolved == protected or protected in resolved.parents):
            return False
    return True


def collect_targets(
    target_dir: Path,
    clean_all: bool = False,
    json_only: bool = False,
) -> tuple[list[Path], list[Path]]:
    """Returns (files_to_delete, dirs_to_delete)."""
    files: list[Path] = []
    dirs: list[Path] = []

    resolved_dir = target_dir.resolve()
    if resolved_dir.exists():
        if not _is_safe_target(resolved_dir):
            raise ValueError(f"Target directory is protected and cannot be deleted: {resolved_dir}")

        if json_only:
            for f in resolved_dir.rglob("*.json"):
                if f.is_file():
                    files.append(f)
        else:
            # All files inside target_dir
            for f in resolved_dir.rglob("*"):
                if f.is_file():
                    files.append(f)
            # Directories inside target_dir (deepest first for deletion)
            for d in sorted(resolved_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if d.is_dir():
                    dirs.append(d)
            dirs.append(resolved_dir)

    if clean_all:
        # Also find any temporary files (*.tmp, .*.tmp) across workspace
        for pattern in ("*.tmp", ".*.tmp"):
            for f in PROJECT_ROOT.rglob(pattern):
                if f.is_file() and not any(part == ".git" or part == ".venv" for part in f.parts):
                    files.append(f)

        # Pytest cache
        pytest_cache = PROJECT_ROOT / ".pytest_cache"
        if pytest_cache.exists():
            dirs.append(pytest_cache)

        # __pycache__ folders
        for pycache in PROJECT_ROOT.rglob("__pycache__"):
            if pycache.is_dir() and not any(part == ".venv" or part == ".git" for part in pycache.parts):
                dirs.append(pycache)

    # Deduplicate while preserving order
    unique_files = list(dict.fromkeys(files))
    unique_dirs = list(dict.fromkeys(dirs))
    return unique_files, unique_dirs


def clean_outputs(
    target_dir: Path | str = "output",
    clean_all: bool = False,
    json_only: bool = False,
    dry_run: bool = False,
    yes: bool = False,
) -> int:
    target_path = Path(target_dir)
    try:
        files, dirs = collect_targets(target_path, clean_all=clean_all, json_only=json_only)
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    total_items = len(files) + (len(dirs) if not json_only else 0)
    if total_items == 0:
        print(f"No cleanable files or directories found in '{target_dir}'. Nothing to do.")
        return 0

    total_bytes = sum(f.stat().st_size for f in files if f.exists())
    size_str = f"{total_bytes / 1024:.1f} KB" if total_bytes < 1024 * 1024 else f"{total_bytes / (1024 * 1024):.2f} MB"

    print(f"Found {len(files)} file(s) and {len(dirs)} directory(ies) ({size_str}) to remove.")
    if dry_run:
        print("[DRY-RUN] The following items would be deleted:")
        for f in files[:20]:
            print(f"  [file] {f.relative_to(PROJECT_ROOT) if f.is_relative_to(PROJECT_ROOT) else f}")
        if len(files) > 20:
            print(f"  ... and {len(files) - 20} more files")
        for d in dirs:
            print(f"  [dir]  {d.relative_to(PROJECT_ROOT) if d.is_relative_to(PROJECT_ROOT) else d}")
        print("[DRY-RUN] No files were deleted.")
        return 0

    if not yes:
        confirm = input(f"Are you sure you want to delete these {total_items} items? (y/N): ").strip().lower()
        if confirm not in {"y", "yes"}:
            print("Operation aborted by user.")
            return 0

    # Execute deletion
    deleted_files = 0
    for f in files:
        try:
            if f.exists():
                f.unlink()
                deleted_files += 1
        except OSError as e:
            print(f"Warning: Failed to delete file {f}: {e}", file=sys.stderr)

    deleted_dirs = 0
    for d in dirs:
        try:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                deleted_dirs += 1
        except OSError as e:
            print(f"Warning: Failed to remove directory {d}: {e}", file=sys.stderr)

    print(f"Successfully cleaned: {deleted_files} files, {deleted_dirs} directories ({size_str} freed).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean generated JSON outputs, temporary files, logs, and caches."
    )
    parser.add_argument(
        "-d",
        "--dir",
        default="output",
        help="Target output directory to clean (default: output)",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Clean target directory plus temporary files (*.tmp) and caches (__pycache__, .pytest_cache)",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Only delete .json files within the target directory, preserving directory tree",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Automatically confirm deletion without prompt",
    )
    args = parser.parse_args()

    return clean_outputs(
        target_dir=args.dir,
        clean_all=args.all,
        json_only=args.json_only,
        dry_run=args.dry_run,
        yes=args.yes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
