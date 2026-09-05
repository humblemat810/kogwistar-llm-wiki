from __future__ import annotations

from dataclasses import dataclass, field

from tests._helpers.pytest_markers import mark_default_ci_items


@dataclass
class _DummyItem:
    keywords: dict[str, object]
    markers: list[object] = field(default_factory=list)

    def add_marker(self, marker) -> None:
        self.markers.append(marker)


def _marker_names(item: _DummyItem) -> list[str]:
    names: list[str] = []
    for marker in item.markers:
        name = getattr(getattr(marker, "mark", None), "name", None)
        if name is not None:
            names.append(name)
    return names


def test_mark_default_ci_items_marks_only_default_ci_tests() -> None:
    safe_item = _DummyItem(keywords={})
    explicit_ci_item = _DummyItem(keywords={"ci": True})
    blocked_items = [
        _DummyItem(keywords={"ci_full": True}),
        _DummyItem(keywords={"manual": True}),
        _DummyItem(keywords={"longrun": True}),
        _DummyItem(keywords={"requires_ollama": True}),
        _DummyItem(keywords={"llm_real": True}),
        _DummyItem(keywords={"ci_full": True, "manual": True}),
    ]

    items = [safe_item, explicit_ci_item, *blocked_items]
    mark_default_ci_items(items)

    assert _marker_names(safe_item) == ["ci"]
    assert _marker_names(explicit_ci_item) == []
    for item in blocked_items:
        assert _marker_names(item) == []
