from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class RawSource:
    file_name: str
    page_count: int


@dataclass
class ExtractionStatus:
    success: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class RawHyperlink:
    page: int
    uri: str


@dataclass
class ExtractedFields:
    emails: list[str] = field(default_factory=list)
    phone_numbers: list[str] = field(default_factory=list)
    linkedin_urls: list[str] = field(default_factory=list)
    github_urls: list[str] = field(default_factory=list)
    other_urls: list[str] = field(default_factory=list)


@dataclass
class RawResume:
    source: RawSource
    extraction: ExtractionStatus
    text: str = ""
    hyperlinks: list[RawHyperlink] = field(default_factory=list)
    extracted_fields: ExtractedFields = field(default_factory=ExtractedFields)
    schema_version: str = "4.0.0"

    def to_dict(self) -> dict:
        return asdict(self)
