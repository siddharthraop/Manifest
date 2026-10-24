"""Read DITA safely: parse without DTDs, walk a map, resolve keys and references.

Every function here is deterministic and offline. Grammar validation is the corpus's job
(xmllint against the OASIS DTDs); MANIFEST only needs the tree.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

# No DTD, no network, no entity expansion: safe on untrusted input, and fast.
PARSER = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False, remove_comments=True)

# Attributes DITA uses for conditional processing (DITA 1.3 profiling attributes).
PROFILE_ATTRS = ("platform", "audience", "product", "otherprops", "props", "deliveryTarget")

# Elements whose content is metadata, not rendered text. Counted separately.
METADATA_ROOTS = ("prolog", "topicmeta")


def parse(path: Path) -> etree._ElementTree:
    return etree.parse(str(path), PARSER)


def split_href(href: str) -> tuple[str, str | None, str | None]:
    """'file.dita#topicid/elemid' -> ('file.dita', 'topicid', 'elemid')."""
    file_part, _, frag = href.partition("#")
    if not frag:
        return file_part, None, None
    topic_id, _, elem_id = frag.partition("/")
    return file_part, topic_id or None, elem_id or None


def is_local(href: str) -> bool:
    return bool(href) and "://" not in href and not href.startswith("mailto:")


@dataclass(frozen=True)
class TopicEntry:
    """One topic as the map places it."""

    path: Path
    order: int
    processing_role: str  # "normal" or "resource-only"
    conditions: dict[str, tuple[str, ...]] = field(default_factory=dict)  # cascaded from the map


@dataclass
class MapModel:
    path: Path
    topics: list[TopicEntry]
    keys: dict[str, str]  # key name -> text value
    reltable_rows: int


def _conditions(el: etree._Element, inherited: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    out = dict(inherited)
    for a in PROFILE_ATTRS:
        v = el.get(a)
        if v:
            out[a] = tuple(v.split())
    return out


def _keydefs(tree: etree._ElementTree) -> dict[str, str]:
    out: dict[str, str] = {}
    for kd in tree.iter("keydef"):
        text = " ".join("".join(str(t) for t in kd.itertext()).split())
        for k in (kd.get("keys") or "").split():
            out.setdefault(k, text)  # DITA: the first definition of a key wins
    return out


def load_map(map_path: Path) -> MapModel:
    """Walk a root map: topics in reading order, keys from every key map, reltable rows."""
    map_path = map_path.resolve()
    topics: list[TopicEntry] = []
    keys: dict[str, str] = {}
    seen: set[Path] = set()
    rows = 0

    def walk(el: etree._Element, base: Path, role: str, cond: dict[str, tuple[str, ...]]) -> None:
        nonlocal rows
        for child in el:
            if not isinstance(child.tag, str):
                continue
            if child.tag == "reltable":
                rows += sum(1 for _ in child.iter("relrow"))
                continue
            href = child.get("href")
            child_role = child.get("processing-role") or role
            child_cond = _conditions(child, cond)
            if child.tag == "mapref" and href and is_local(href):
                sub = (base / href).resolve()
                sub_tree = parse(sub)
                keys.update({k: v for k, v in _keydefs(sub_tree).items() if k not in keys})
                if child.get("type") != "subjectScheme":
                    walk(sub_tree.getroot(), sub.parent, child_role, child_cond)
                continue
            if child.tag == "topicref" and href and is_local(href) and href.split("#")[0].endswith(".dita"):
                p = (base / href.split("#")[0]).resolve()
                if p not in seen:
                    seen.add(p)
                    topics.append(TopicEntry(p, len(topics), child_role, child_cond))
            walk(child, base, child_role, child_cond)

    root_tree = parse(map_path)
    keys.update(_keydefs(root_tree))
    walk(root_tree.getroot(), map_path.parent, "normal", {})
    return MapModel(map_path, topics, keys, rows)


def iter_body(root: etree._Element) -> Iterator[etree._Element]:
    """Every element outside prolog/topicmeta, in document order."""
    skip: set[etree._Element] = set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag in METADATA_ROOTS:
            skip.update(el.iter())
        if el in skip:
            continue
        yield el


def find_by_id(path: Path, topic_id: str | None, elem_id: str | None) -> etree._Element | None:
    """Resolve the fragment of a DITA href inside a topic file."""
    if not path.exists():
        return None
    root = parse(path).getroot()
    if topic_id and root.get("id") != topic_id:
        nested = [t for t in root.iter() if isinstance(t.tag, str) and t.get("id") == topic_id]
        if not nested:
            return None
        root = nested[0]
    if not elem_id:
        return root
    for el in root.iter():
        if isinstance(el.tag, str) and el.get("id") == elem_id:
            return el
    return None
