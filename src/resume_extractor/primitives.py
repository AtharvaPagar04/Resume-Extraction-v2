from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

from .models import ExtractedFields

_EMAIL = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
_URL = re.compile(r"(?i)(?:https?://|www\.)[^\s<>\"']+")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{8,}\d)(?:\s*(?:x|ext\.?)\s*\d{1,6})?(?!\w)", re.I)
_DATE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2})\b")
_OUTER_PUNCTUATION = " \t\r\n\"'<>[]{}(),.;:!?"


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def normalize_url(value: str) -> str | None:
    value = unicodedata.normalize("NFKC", html.unescape(value))
    value = value.replace("\u200b", "").replace("\ufeff", "").replace("\u00a0", "")
    value = re.sub(r"\s*([?&])\s*", r"\1", value).strip(_OUTER_PUNCTUATION)
    value = re.sub(r"^hxxps?://", lambda match: "https://" if "s" in match.group(0).lower() else "http://", value, flags=re.I)
    if value.lower().startswith("mailto:"):
        email = value[7:].strip()
        return f"mailto:{_normalize_email(email)}" if _EMAIL.fullmatch(email) else None
    if value.lower().startswith("www."):
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if len(host) > 253 or any(not label or len(label) > 63 for label in host.split(".")):
        return None
    if not re.fullmatch(r"[a-z0-9.-]+", host) or not re.fullmatch(r"[a-z]{2,24}", host.rsplit(".", 1)[-1]):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = host if port is None or (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80) else f"{host}:{port}"
    return urlunsplit(("https", netloc, parsed.path, parsed.query, parsed.fragment))


def _normalize_email(value: str) -> str:
    local, _, domain = value.rpartition("@")
    return f"{local}@{domain.lower()}"


def reconstruct_broken_urls(text: str) -> str:
    return re.sub(r"(?i)((?:https?://|www\.)[^\s\n]+)\s*\n\s*([^\s]+)", r"\1\2", text)


def _phone_numbers(text: str) -> list[str]:
    masked = _URL.sub(" ", text)
    masked = _EMAIL.sub(" ", masked)
    masked = _DATE.sub(" ", masked)
    results: list[str] = []
    for match in _PHONE.finditer(masked):
        candidate = match.group(0).strip()
        main, extension = re.split(r"\s*(?:x|ext\.?)\s*", candidate, maxsplit=1, flags=re.I) if re.search(r"\s*(?:x|ext\.?)\s*", candidate, re.I) else (candidate, "")
        digits = re.sub(r"\D", "", main)
        if not 10 <= len(digits) <= 15 or len(set(digits)) <= 2:
            continue
        if not main.startswith("+") and digits[:1] in {"0", "1"}:
            continue
        normalized = ("+" if main.lstrip().startswith("+") else "") + digits
        results.append(f"{normalized} x{re.sub(r'\D', '', extension)}" if extension else normalized)
    return _unique(results)


def extract_fields(text: str, annotation_uris: Iterable[str] = ()) -> ExtractedFields:
    stitched = reconstruct_broken_urls(text.replace("\f", "\n"))
    emails = _unique(_normalize_email(match.group(0)) for match in _EMAIL.finditer(stitched))
    candidates = [match.group(0) for match in _URL.finditer(stitched)] + list(annotation_uris)
    urls = _unique(url for candidate in candidates if (url := normalize_url(candidate)) and not url.startswith("mailto:"))
    for uri in annotation_uris:
        if uri.lower().startswith("mailto:") and (email := uri[7:]) and _EMAIL.fullmatch(email):
            emails = _unique([*emails, _normalize_email(email)])
    linkedin = [url for url in urls if urlsplit(url).hostname and (urlsplit(url).hostname == "linkedin.com" or urlsplit(url).hostname.endswith(".linkedin.com"))]
    github = [url for url in urls if urlsplit(url).hostname and (urlsplit(url).hostname == "github.com" or urlsplit(url).hostname.endswith(".github.com"))]
    special = {url.casefold() for url in [*linkedin, *github]}
    return ExtractedFields(emails, _phone_numbers(stitched), linkedin, github, [url for url in urls if url.casefold() not in special])
