#!/usr/bin/env python3
"""Clean and structure generated RAW resume JSON files into normalized, clean JSON.

Performs text sanitization, page separation, contact field normalization,
and deterministic section detection without any external AI APIs.

Usage:
    python scripts/clean_json_data.py
    python scripts/clean_json_data.py --input output/raw --output output/cleaned
    python scripts/clean_json_data.py --input output/raw/ResumeSavitaPansare.json --output output/cleaned/
    python scripts/clean_json_data.py --in-place
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Authoritative single source of truth for standard resume section heading forms
SECTION_HEADING_FORMS: dict[str, tuple[str, ...]] = {
    "summary": (
        "professional summary",
        "summary",
        "profile",
        "about me",
        "career objective",
        "objective",
    ),
    "experience": (
        "work experience",
        "professional experience",
        "experience",
        "employment history",
        "work history",
        "total experience",
    ),
    "education": (
        "education",
        "academic background",
        "educational qualifications",
        "academics",
    ),
    "skills": (
        "technical skills",
        "skills & abilities",
        "skills and abilities",
        "core competencies",
        "key skills",
        "skills",
        "technologies",
    ),
    "projects": (
        "projects",
        "key projects",
        "academic projects",
        "project experience",
    ),
    "certifications": (
        "certifications",
        "certificates",
        "licenses & certifications",
        "licenses and certifications",
    ),
    "awards": (
        "awards",
        "honors",
        "achievements",
        "awards & achievements",
        "awards and achievements",
    ),
    "personal_details": (
        "personal details",
        "personal information",
        "personal skills",
        "declaration",
    ),
}


def _form_to_regex(form: str) -> str:
    """Convert a literal heading form to regex with token-aware whitespace and connector handling."""
    tokens = form.split()
    if not tokens:
        return ""
    connector_tokens = {"&", "and"}
    pat = re.escape(tokens[0])
    for i in range(1, len(tokens)):
        curr_token = tokens[i]
        prev_token = tokens[i - 1]
        if curr_token in connector_tokens or prev_token in connector_tokens:
            sep = r"\s*"
        else:
            sep = r"\s+"
        pat += sep + re.escape(curr_token)
    return pat



# Major resume section pattern headings (derived directly from SECTION_HEADING_FORMS)
SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (sec, re.compile("^(?:" + "|".join(_form_to_regex(f) for f in forms) + ")$", re.I))
    for sec, forms in SECTION_HEADING_FORMS.items()
]


def _build_whitespace_free_heading_map(
    heading_forms: dict[str, tuple[str, ...]],
) -> dict[str, str]:
    """Map whitespace-free key to its canonical accepted surface form, validating uniqueness."""
    key_to_form: dict[str, str] = {}
    key_to_section: dict[str, str] = {}
    for sec_name, forms in heading_forms.items():
        for form in forms:
            key = re.sub(r"\s+", "", form).lower()
            if key in key_to_section:
                existing_sec = key_to_section[key]
                if existing_sec != sec_name:
                    raise ValueError(
                        f"Cross-bucket collision for key '{key}': maps to '{existing_sec}' and '{sec_name}'"
                    )
            key_to_form[key] = form
            key_to_section[key] = sec_name
    return key_to_form


# Precomputed whitespace-free heading reconstruction map (derived directly from SECTION_HEADING_FORMS)
SECTION_WHITESPACE_FREE_MAP: dict[str, str] = _build_whitespace_free_heading_map(
    SECTION_HEADING_FORMS
)


def clean_text_content(text: str) -> str:
    """Sanitize raw text: normalizes unicode, removes non-printable chars and collapses blank lines."""
    if not text:
        return ""
    # Normalize unicode to NFKC
    cleaned = unicodedata.normalize("NFKC", text)
    # Replace non-breaking spaces and zero-width spaces
    cleaned = cleaned.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    # Remove null bytes and non-printable control characters except \n, \t, \r, \f
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)
    # Standardize line breaks
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    # Clean whitespace per line
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in cleaned.split("\n")]
    cleaned = "\n".join(lines)
    # Collapse 3+ consecutive newlines to 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_phone_number(phone: str) -> str:
    """Standardizes phone numbers, adding spaces or standard prefix where appropriate."""
    phone = phone.strip()
    digits = re.sub(r"\D", "", phone)
    if phone.startswith("+91") and len(digits) == 12:
        return f"+91 {digits[2:7]} {digits[7:]}"
    if len(digits) == 10 and digits[0] in {"6", "7", "8", "9"}:
        # Standard Indian 10-digit mobile
        return f"+91 {digits[:5]} {digits[5:]}"
    return phone


def normalize_heading_candidate(line: str) -> str:
    """Normalize a potential section heading candidate by stripping at most one terminal colon or period."""
    candidate = line.strip()
    if candidate.endswith((":", ".")):
        candidate = candidate[:-1].rstrip()
    return candidate


def normalize_letter_spaced_heading_candidate(candidate: str) -> str:
    """Normalize a potential heading candidate consisting entirely of single alphabetic characters."""
    tokens = candidate.split()
    if len(tokens) < 3:
        return candidate
    if not all(len(token) == 1 and token.isalpha() for token in tokens):
        return candidate
    return "".join(tokens)


def reconstruct_multi_word_letter_spaced_heading_candidate(candidate: str) -> str:
    """Reconstruct a multi-word letter-spaced candidate to its accepted surface form.

    Inspects the entire candidate. If all whitespace-separated tokens are single
    alphabetic characters (>= 3 tokens), checks if its whitespace-free key matches
    an existing accepted heading surface form. If so, returns that accepted surface form;
    otherwise, returns the candidate unchanged.
    """
    tokens = candidate.split()
    if len(tokens) < 3 or not all(len(t) == 1 and t.isalpha() for t in tokens):
        return candidate
    key = "".join(tokens).lower()
    return SECTION_WHITESPACE_FREE_MAP.get(key, candidate)


def split_into_sections(text: str) -> dict[str, list[str]]:
    """Deterministically segment text into standard resume sections with list-of-lines formatting."""
    lines = text.split("\n")
    sections: dict[str, list[str]] = {"header": []}
    current_section = "header"

    for line in lines:
        stripped = line.strip()
        matched_section = None
        if stripped and len(stripped) <= 60:
            candidate = normalize_heading_candidate(stripped)
            # 1. Existing patterns match (covers normal headings + single-word letter spacing)
            single_word_cand = normalize_letter_spaced_heading_candidate(candidate)
            for sec_name, pattern in SECTION_PATTERNS:
                if pattern.match(single_word_cand):
                    matched_section = sec_name
                    break

            # 2. Multi-word letter-spacing reconstruction
            if not matched_section:
                reconstructed = reconstruct_multi_word_letter_spaced_heading_candidate(candidate)
                if reconstructed != candidate:
                    for sec_name, pattern in SECTION_PATTERNS:
                        if pattern.match(reconstructed):
                            matched_section = sec_name
                            break

        if matched_section:
            current_section = matched_section
            if current_section not in sections:
                sections[current_section] = []
        else:
            sections[current_section].append(line)

    # Format each section as an array of line strings
    result: dict[str, list[str]] = {}
    for sec_name, sec_lines in sections.items():
        formatted_lines = [line.strip() for line in sec_lines if line.strip()]
        if formatted_lines:
            result[sec_name] = formatted_lines
    return result


def clean_single_resume_dict(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Clean a single raw resume dictionary."""
    raw_text = raw_data.get("text", "")

    # Clean pages separated by \f into arrays of line strings
    raw_pages = raw_text.split("\f") if "\f" in raw_text else [raw_text]
    cleaned_pages = [
        [line.strip() for line in clean_text_content(page).split("\n") if line.strip()]
        for page in raw_pages
        if page.strip()
    ]
    cleaned_pages = [page for page in cleaned_pages if page]

    # Clean full unified text into an array of line strings
    cleaned_full_text = clean_text_content(raw_text.replace("\f", "\n\n"))
    cleaned_text_lines = [line.strip() for line in cleaned_full_text.split("\n") if line.strip()]

    # Extract & detect sections
    sections = split_into_sections(cleaned_full_text)

    # Clean contact fields
    fields = raw_data.get("extracted_fields", {})
    emails = sorted(list(dict.fromkeys(
        email.strip().lower()
        for email in fields.get("emails", [])
        if email and "@" in email
    )))

    phones = sorted(list(dict.fromkeys(
        normalize_phone_number(phone)
        for phone in fields.get("phone_numbers", [])
        if phone
    )))

    clean_dict: dict[str, Any] = {
        "source": raw_data.get("source", {}),
        "extraction": raw_data.get("extraction", {}),
        "contact_info": {
            "emails": emails,
            "phone_numbers": phones,
            "linkedin_urls": fields.get("linkedin_urls", []),
            "github_urls": fields.get("github_urls", []),
            "other_urls": fields.get("other_urls", []),
        },
        "pages": cleaned_pages,
        "sections": sections,
        "cleaned_text": cleaned_text_lines,
        "hyperlinks": raw_data.get("hyperlinks", []),
        "schema_version": "4.0.0-cleaned",
    }
    return clean_dict


def process_clean_json(
    input_path: Path | str = "output/raw",
    output_dir: Path | str = "output/cleaned",
    in_place: bool = False,
    overwrite: bool = True,
    indent: int = 2,
    verbose: bool = False,
) -> int:
    source = Path(input_path).resolve()
    if not source.exists():
        print(f"Error: Input path does not exist: {source}", file=sys.stderr)
        return 1

    json_files: list[Path] = []
    if source.is_file():
        if source.suffix.lower() == ".json":
            json_files.append(source)
    else:
        json_files = sorted(source.rglob("*.json"))

    if not json_files:
        print(f"No JSON files found in: {source}")
        return 0

    target_dir = source if in_place else Path(output_dir).resolve()
    if not in_place:
        target_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing {len(json_files)} JSON file(s)...")
    print(f"Destination: {'In-place overwrite' if in_place else str(target_dir)}")
    print("-" * 60)

    success_count = 0
    for idx, json_file in enumerate(json_files, 1):
        try:
            with json_file.open("r", encoding="utf-8") as f:
                raw_data = json.load(f)

            cleaned_data = clean_single_resume_dict(raw_data)

            dest_file = json_file if in_place else target_dir / json_file.name
            if dest_file.exists() and not overwrite and not in_place:
                if verbose:
                    print(f"[{idx}/{len(json_files)}] SKIP (exists): {dest_file.name}")
                continue

            with dest_file.open("w", encoding="utf-8") as f:
                json.dump(cleaned_data, f, ensure_ascii=False, indent=indent)
                f.write("\n")

            sec_names = list(cleaned_data.get("sections", {}).keys())
            sec_summary = f"sections: {', '.join(sec_names)}"
            print(f"[{idx}/{len(json_files)}] ✓ Cleaned {json_file.name} -> {dest_file.name} ({sec_summary})")
            if verbose:
                contacts = cleaned_data["contact_info"]
                print(f"    Emails: {', '.join(contacts['emails']) or 'None'}")
                print(f"    Phones: {', '.join(contacts['phone_numbers']) or 'None'}")
                print(f"    Pages: {len(cleaned_data['pages'])}")

            success_count += 1
        except Exception as e:
            print(f"[{idx}/{len(json_files)}] ✗ Failed to clean {json_file.name}: {e}", file=sys.stderr)

    print("-" * 60)
    print(f"Cleaned {success_count}/{len(json_files)} JSON file(s) successfully.")
    return 0 if success_count == len(json_files) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean and structure RAW resume JSON outputs into normalized, section-aware JSON."
    )
    parser.add_argument(
        "-i",
        "--input",
        default="output/raw",
        help="Input JSON file or directory containing raw JSON files (default: output/raw)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="output/cleaned",
        help="Target output directory for cleaned JSON (default: output/cleaned)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Clean files in-place instead of saving to a separate directory",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Do not overwrite existing files in destination directory",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON output indentation (default: 2)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed section and contact information",
    )
    args = parser.parse_args()

    return process_clean_json(
        input_path=args.input,
        output_dir=args.output,
        in_place=args.in_place,
        overwrite=not args.no_overwrite,
        indent=args.indent,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    raise SystemExit(main())
