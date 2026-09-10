"""Dependency-light source decomposition for multimodal projections.

The canonical source store remains outside the projection store.  This module
turns a source revision, or an authoritative parser manifest, into stable
``MultimodalSourceUnit`` records.  Binary assets are represented by
``content_ref`` values and are resolved later by an ``AssetResolver``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
from .multimodal_projection import MultimodalSourceUnit, SourceModality


@dataclass(frozen=True, slots=True)
class MultimodalSourceBundle:
    """All retrieval views derived from one immutable source revision."""

    workspace_id: str
    source_id: str
    source_revision_id: str
    source_uri: str | None
    source_format: str
    units: tuple[MultimodalSourceUnit, ...]

    def __post_init__(self) -> None:
        if not self.workspace_id or not self.source_id or not self.source_revision_id:
            raise ValueError("source bundles require workspace, source, and revision identity")
        if not self.units:
            raise ValueError("source bundles require at least one multimodal unit")
        identities = {(unit.view_id, unit.source_revision_id) for unit in self.units}
        if len(identities) != len(self.units):
            raise ValueError("source bundle contains duplicate view identities")
        if any(
            unit.workspace_id != self.workspace_id
            or unit.source_id != self.source_id
            or unit.source_revision_id != self.source_revision_id
            for unit in self.units
        ):
            raise ValueError("source units must belong to the bundle's source revision")

    def to_payload(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "source_id": self.source_id,
            "source_revision_id": self.source_revision_id,
            "source_uri": self.source_uri,
            "source_format": self.source_format,
            "units": [unit.to_payload() for unit in self.units],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "MultimodalSourceBundle":
        raw_units = payload.get("units")
        if not isinstance(raw_units, Sequence) or isinstance(raw_units, (str, bytes)):
            raise ValueError("source bundle payload requires a units sequence")
        if not all(isinstance(item, Mapping) for item in raw_units):
            raise ValueError("source bundle units must be mappings")
        return cls(
            workspace_id=str(payload["workspace_id"]),
            source_id=str(payload["source_id"]),
            source_revision_id=str(payload["source_revision_id"]),
            source_uri=str(payload["source_uri"]) if payload.get("source_uri") else None,
            source_format=str(payload["source_format"]),
            units=tuple(MultimodalSourceUnit.from_payload(item) for item in raw_units),
        )


def _stable_view_id(
    source_id: str,
    revision_id: str,
    modality: str,
    ordinal: int,
    locator: Mapping[str, object],
) -> str:
    payload = json.dumps(
        {
            "source_id": source_id,
            "source_revision_id": revision_id,
            "modality": modality,
            "ordinal": ordinal,
            "locator": dict(locator),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"mmv-{sha256(payload).hexdigest()[:24]}"


def _unit(
    *,
    workspace_id: str,
    source_id: str,
    revision_id: str,
    modality: SourceModality,
    ordinal: int,
    locator: Mapping[str, object],
    content_ref: str | None = None,
    text: str | None = None,
    asset_sha256: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> MultimodalSourceUnit:
    return MultimodalSourceUnit(
        view_id=_stable_view_id(source_id, revision_id, modality, ordinal, locator),
        workspace_id=workspace_id,
        source_id=source_id,
        source_revision_id=revision_id,
        modality=modality,
        locator=dict(locator),
        content_ref=content_ref,
        text=text,
        asset_sha256=asset_sha256,
        metadata=dict(metadata or {}),
    )


def split_text_units(
    *,
    workspace_id: str,
    source_id: str,
    revision_id: str,
    text: str,
    max_chars: int = 4000,
) -> tuple[MultimodalSourceUnit, ...]:
    """Create stable, half-open text spans from one source revision."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not text:
        return ()
    units: list[MultimodalSourceUnit] = []
    start = 0
    ordinal = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start, end)
            if boundary > start:
                end = boundary
        if end <= start:
            end = min(start + max_chars, len(text))
        locator = {
            "kind": "text_span",
            "start_char": start,
            "end_char": end,
            "end_semantics": "exclusive",
        }
        units.append(
            _unit(
                workspace_id=workspace_id,
                source_id=source_id,
                revision_id=revision_id,
                modality="text",
                ordinal=ordinal,
                locator=locator,
                text=text[start:end],
            )
        )
        ordinal += 1
        start = end
    return tuple(units)


class _VisibleHtmlCollector(HTMLParser):
    """Collect visible text and asset occurrences without fetching anything."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.images: list[dict[str, object]] = []
        self.tables: list[str] = []
        self._skip_depth = 0
        self._table_depth = 0
        self._table_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        values = dict(attrs)
        if name in {"script", "style", "noscript", "template"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if name == "img":
            src = values.get("src") or values.get("data-src")
            if src:
                self.images.append(
                    {
                        "src": src,
                        "alt": values.get("alt") or "",
                        "ordinal": len(self.images),
                    }
                )
        if name == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table_parts = []

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in {"script", "style", "noscript", "template"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if name == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                value = " ".join(self._table_parts).strip()
                if value:
                    self.tables.append(value)
                self._table_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        self.text_parts.append(value)
        if self._table_depth:
            self._table_parts.append(value)


def webpage_units(
    *,
    workspace_id: str,
    source_id: str,
    revision_id: str,
    html: str,
    source_uri: str | None = None,
) -> tuple[MultimodalSourceUnit, ...]:
    """Extract visible page text, image references, and HTML tables."""

    parser = _VisibleHtmlCollector()
    parser.feed(html)
    parser.close()
    units: list[MultimodalSourceUnit] = []
    visible_text = " ".join(parser.text_parts).strip()
    if visible_text:
        units.append(
            _unit(
                workspace_id=workspace_id,
                source_id=source_id,
                revision_id=revision_id,
                modality="webpage",
                ordinal=0,
                locator={"kind": "html_visible_text"},
                text=visible_text,
                metadata={"source_uri": source_uri} if source_uri else {},
            )
        )
    next_ordinal = len(units)
    for image in parser.images:
        src = str(image["src"])
        locator = {"kind": "dom_image", "ordinal": image["ordinal"], "src": src}
        units.append(
            _unit(
                workspace_id=workspace_id,
                source_id=source_id,
                revision_id=revision_id,
                modality="image",
                ordinal=next_ordinal,
                locator=locator,
                content_ref=src,
                metadata={"alt": image["alt"], "source_uri": source_uri} if source_uri else {"alt": image["alt"]},
            )
        )
        next_ordinal += 1
    for table_index, table_text in enumerate(parser.tables):
        units.append(
            _unit(
                workspace_id=workspace_id,
                source_id=source_id,
                revision_id=revision_id,
                modality="table",
                ordinal=next_ordinal,
                locator={"kind": "html_table", "ordinal": table_index},
                text=table_text,
            )
        )
        next_ordinal += 1
    if not units and source_uri:
        units.append(
            _unit(
                workspace_id=workspace_id,
                source_id=source_id,
                revision_id=revision_id,
                modality="webpage",
                ordinal=0,
                locator={"kind": "html_document"},
                content_ref=source_uri,
            )
        )
    return tuple(units)


def _manifest_unit(
    *,
    workspace_id: str,
    source_id: str,
    revision_id: str,
    item: Mapping[str, object],
    ordinal: int,
) -> MultimodalSourceUnit:
    modality = str(item.get("modality", "text"))
    if modality not in {"text", "image", "pdf_page", "table", "chart", "webpage", "video_frame"}:
        raise ValueError(f"unsupported multimodal manifest modality {modality!r}")
    locator = dict(item.get("locator") or {})
    if not locator:
        locator = {"kind": "manifest_unit", "ordinal": ordinal}
    content_ref = str(item["content_ref"]) if item.get("content_ref") else None
    text = str(item["text"]) if item.get("text") else None
    return _unit(
        workspace_id=workspace_id,
        source_id=source_id,
        revision_id=revision_id,
        modality=modality,  # type: ignore[arg-type]
        ordinal=ordinal,
        locator=locator,
        content_ref=content_ref,
        text=text,
        asset_sha256=str(item["asset_sha256"]) if item.get("asset_sha256") else None,
        metadata=dict(item.get("metadata") or {}),
    )


def manifest_units(
    *,
    workspace_id: str,
    source_id: str,
    revision_id: str,
    manifest: Mapping[str, object],
) -> tuple[MultimodalSourceUnit, ...]:
    """Convert authoritative parser output into revision-bound retrieval units."""

    raw_units = manifest.get("units")
    if not isinstance(raw_units, Sequence) or isinstance(raw_units, (str, bytes)):
        raise ValueError("multimodal manifest requires a units sequence")
    if not all(isinstance(item, Mapping) for item in raw_units):
        raise ValueError("multimodal manifest units must be mappings")
    units = tuple(
        _manifest_unit(
            workspace_id=workspace_id,
            source_id=source_id,
            revision_id=revision_id,
            item=item,
            ordinal=index,
        )
        for index, item in enumerate(raw_units)
    )
    return units


def pdf_manifest_units(
    *,
    workspace_id: str,
    source_id: str,
    revision_id: str,
    pages: Sequence[Mapping[str, object]],
) -> tuple[MultimodalSourceUnit, ...]:
    """Convert normalized PDF parser pages into separate page/asset units.

    PDF parsing/OCR is intentionally not hidden here.  The parser supplies
    page text and external asset references, preserving its existing authority.
    """

    items: list[MultimodalSourceUnit] = []
    for page_index, page in enumerate(pages):
        page_number = int(page.get("page_number", page_index + 1))
        page_locator = {"kind": "pdf_page", "page_number": page_number}
        page_text = str(page["text"]) if page.get("text") else None
        page_ref = str(page["content_ref"]) if page.get("content_ref") else None
        if page_text or page_ref:
            items.append(
                _unit(
                    workspace_id=workspace_id,
                    source_id=source_id,
                    revision_id=revision_id,
                    modality="pdf_page",
                    ordinal=len(items),
                    locator=page_locator,
                    content_ref=page_ref,
                    text=page_text,
                )
            )
        for kind, modality in (("images", "image"), ("tables", "table"), ("charts", "chart")):
            values = page.get(kind, ())
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise ValueError(f"PDF page {page_number} field {kind!r} must be a sequence")
            for asset_index, value in enumerate(values):
                if not isinstance(value, Mapping):
                    raise ValueError(f"PDF page {page_number} {kind} entries must be mappings")
                item = dict(value)
                item.setdefault("modality", modality)
                item.setdefault("locator", {**page_locator, "asset_index": asset_index})
                items.append(
                    _manifest_unit(
                        workspace_id=workspace_id,
                        source_id=source_id,
                        revision_id=revision_id,
                        item=item,
                        ordinal=len(items),
                    )
                )
    return tuple(items)


def build_source_bundle(
    *,
    workspace_id: str,
    source_id: str,
    source_revision_id: str,
    source_format: str,
    source_uri: str | None = None,
    raw_text: str | None = None,
    content_ref: str | None = None,
    manifest: Mapping[str, object] | None = None,
    max_chars: int = 4000,
) -> MultimodalSourceBundle:
    """Build a source bundle for common text, image, HTML, and parsed formats."""

    normalized_format = source_format.lower().lstrip(".")
    if manifest is not None and normalized_format != "pdf":
        units = manifest_units(
            workspace_id=workspace_id,
            source_id=source_id,
            revision_id=source_revision_id,
            manifest=manifest,
        )
    elif normalized_format in {"txt", "text", "md", "markdown"}:
        units = split_text_units(
            workspace_id=workspace_id,
            source_id=source_id,
            revision_id=source_revision_id,
            text=raw_text or "",
            max_chars=max_chars,
        )
    elif normalized_format in {"html", "htm", "webpage", "url"}:
        units = webpage_units(
            workspace_id=workspace_id,
            source_id=source_id,
            revision_id=source_revision_id,
            html=raw_text or "",
            source_uri=source_uri,
        )
    elif normalized_format in {"png", "jpg", "jpeg", "webp", "gif", "image"}:
        if not content_ref:
            raise ValueError("image source bundles require content_ref")
        units = (
            _unit(
                workspace_id=workspace_id,
                source_id=source_id,
                revision_id=source_revision_id,
                modality="image",
                ordinal=0,
                locator={"kind": "source_asset"},
                content_ref=content_ref,
            ),
        )
    elif normalized_format == "pdf":
        pages = (manifest or {}).get("pages") if manifest else None
        if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
            raise ValueError("PDF bundles require normalized manifest pages")
        if not all(isinstance(page, Mapping) for page in pages):
            raise ValueError("PDF manifest pages must be mappings")
        units = pdf_manifest_units(
            workspace_id=workspace_id,
            source_id=source_id,
            revision_id=source_revision_id,
            pages=pages,  # type: ignore[arg-type]
        )
    else:
        raise ValueError(f"unsupported multimodal source format {source_format!r}")
    return MultimodalSourceBundle(
        workspace_id=workspace_id,
        source_id=source_id,
        source_revision_id=source_revision_id,
        source_uri=source_uri,
        source_format=normalized_format,
        units=tuple(units),
    )


class MappingAssetResolver:
    """Small test/local adapter for externally stored asset references."""

    def __init__(self, assets: Mapping[str, object]) -> None:
        self._assets = dict(assets)

    def resolve(self, unit: MultimodalSourceUnit) -> object:
        if not unit.content_ref:
            raise KeyError(f"source unit {unit.view_id} has no content_ref")
        try:
            return self._assets[unit.content_ref]
        except KeyError as exc:
            raise KeyError(f"asset reference is unavailable: {unit.content_ref}") from exc


class LocalFileAssetResolver:
    """Explicit local-file resolver for operator-controlled source storage."""

    def resolve(self, unit: MultimodalSourceUnit) -> bytes:
        if not unit.content_ref:
            raise KeyError(f"source unit {unit.view_id} has no content_ref")
        return Path(unit.content_ref).read_bytes()


__all__ = [
    "LocalFileAssetResolver",
    "MappingAssetResolver",
    "MultimodalSourceBundle",
    "build_source_bundle",
    "manifest_units",
    "pdf_manifest_units",
    "split_text_units",
    "webpage_units",
]
