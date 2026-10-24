"""Tool 1, inventory_map: count what is in the DITA source. This is the "before" picture.

Every later check compares the migrated output against these numbers, so the inventory
must be exact and must never depend on the converter. It reads the source with lxml only.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from manifest.dita import PROFILE_ATTRS, find_by_id, is_local, iter_body, load_map, parse, split_href

SCHEMA_VERSION = "1"


@dataclass
class KeyRef:
    key: str
    element: str
    defined: bool
    value: str | None
    line: int | None


@dataclass
class ConRef:
    element: str
    target: str
    resolves: bool
    line: int | None


@dataclass
class Condition:
    element: str
    attribute: str
    values: list[str]
    line: int | None


@dataclass
class Link:
    element: str
    href: str
    scope: str  # "local" or "external"
    resolves: bool | None  # None for external links: checked later by lychee
    line: int | None


@dataclass
class TopicInventory:
    path: str  # relative to the map's folder
    topic_id: str | None
    topic_type: str
    processing_role: str
    map_conditions: dict[str, list[str]]
    elements: dict[str, int]
    ids: list[str]
    keyrefs: list[KeyRef] = field(default_factory=list)
    conrefs: list[ConRef] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    media: list[Link] = field(default_factory=list)

    @property
    def totals(self) -> dict[str, int]:
        return {
            "elements": sum(self.elements.values()),
            "ids": len(self.ids),
            "keyrefs": len(self.keyrefs),
            "conrefs": len(self.conrefs),
            "conditions": len(self.conditions),
            "links": len(self.links),
            "media": len(self.media),
        }


@dataclass
class MapInventory:
    schema_version: str
    map: str
    topics: list[TopicInventory]
    keys: dict[str, str]
    reltable_rows: int

    @property
    def totals(self) -> dict[str, int]:
        t: Counter[str] = Counter()
        for topic in self.topics:
            t.update(topic.totals)
        t["topics"] = len(self.topics)
        t["keys_defined"] = len(self.keys)
        t["reltable_rows"] = self.reltable_rows
        return dict(t)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["totals"] = self.totals
        for td, topic in zip(d["topics"], self.topics, strict=True):
            td["totals"] = topic.totals
        return d

    def write(self, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _line(el: etree._Element) -> int | None:
    return el.sourceline if isinstance(el.sourceline, int) else None


def inventory_topic(
    path: Path, base: Path, keys: dict[str, str], role: str, map_conditions: dict[str, tuple[str, ...]]
) -> TopicInventory:
    root = parse(path).getroot()
    elements: Counter[str] = Counter()
    inv = TopicInventory(
        path=str(path.relative_to(base)),
        topic_id=root.get("id"),
        topic_type=str(root.tag),
        processing_role=role,
        map_conditions={k: list(v) for k, v in map_conditions.items()},
        elements={},
        ids=[],
    )
    for el in iter_body(root):
        tag = str(el.tag)
        elements[tag] += 1
        if el.get("id"):
            inv.ids.append(el.get("id") or "")
        keyref = el.get("keyref")
        if keyref:
            name = keyref.split("/")[0]
            inv.keyrefs.append(KeyRef(name, tag, name in keys, keys.get(name), _line(el)))
        conref = el.get("conref")
        if conref:
            f, tid, eid = split_href(conref)
            target = (path.parent / f).resolve() if f else path
            inv.conrefs.append(ConRef(tag, conref, find_by_id(target, tid, eid) is not None, _line(el)))
        for a in PROFILE_ATTRS:
            v = el.get(a)
            if v:
                inv.conditions.append(Condition(tag, a, v.split(), _line(el)))
        href = el.get("href")
        if href and tag in ("xref", "link"):
            local = is_local(href) and el.get("scope") != "external"
            resolves: bool | None = None
            if local:
                f, tid, eid = split_href(href)
                target = (path.parent / f).resolve() if f else path
                resolves = find_by_id(target, tid, eid) is not None
            inv.links.append(Link(tag, href, "local" if local else "external", resolves, _line(el)))
        if tag in ("image", "object"):
            ref = el.get("href") or el.get("data") or ""
            exists = (path.parent / ref).exists() if ref and is_local(ref) else None
            inv.media.append(Link(tag, ref, "local" if exists is not None else "external", exists, _line(el)))
    inv.elements = dict(sorted(elements.items()))
    return inv


def inventory_map(map_path: Path) -> MapInventory:
    """Inventory every topic the map references, in reading order."""
    model = load_map(map_path)
    base = model.path.parent
    topics = [
        inventory_topic(t.path, base, model.keys, t.processing_role, t.conditions) for t in model.topics
    ]
    return MapInventory(
        SCHEMA_VERSION, model.path.name, topics, dict(sorted(model.keys.items())), model.reltable_rows
    )
