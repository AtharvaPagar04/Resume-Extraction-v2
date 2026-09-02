from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .extractor import extract_pdf
from .models import RawResume


@dataclass(frozen=True)
class BatchSummary:
    attempted: int
    successful: int
    failed: int
    skipped: int


def discover_pdfs(input_path: str | Path) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        return [path] if path.suffix.lower() == ".pdf" else []
    return sorted(
        (
            file
            for file in path.rglob("*")
            if file.is_file()
            and file.suffix.lower() == ".pdf"
            and not any(part.startswith(".") for part in file.relative_to(path).parts)
            and not file.name.startswith("~")
            and not file.name.lower().endswith((".tmp.pdf", ".lock.pdf"))
        ),
        key=lambda file: str(file).casefold(),
    )


def write_raw(raw: RawResume, target: str | Path, overwrite: bool = False) -> Path:
    output = Path(target)
    if output.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False)
    try:
        with handle:
            json.dump(raw.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, output)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return output


def run_pipeline(input_path: str | Path, output: str | Path | None = None, overwrite: bool = False) -> BatchSummary:
    input_value = Path(input_path)
    files = discover_pdfs(input_value)
    output_root = Path(output) if output else (input_value.parent / "raw" if input_value.is_file() else input_value / "raw")
    successful = failed = skipped = 0
    for file in files:
        target = output_root if output_root.suffix.lower() == ".json" and input_value.is_file() else output_root / f"{file.stem}.json"
        if target.exists() and not overwrite:
            skipped += 1
            continue
        raw = extract_pdf(file)
        write_raw(raw, target, overwrite=True)
        if raw.extraction.success:
            successful += 1
        else:
            failed += 1
    return BatchSummary(len(files), successful, failed, skipped)
