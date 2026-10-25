"""`manifest lint MAP`: the pull-request check (US-04). Seconds, no DITA-OT: every broken key reference,
conref, local link and image, each with its file, its line and the fix.

    manifest lint docs/product.ditamap                  # file:line: problem. Fix: ...
    manifest lint docs/product.ditamap --format github  # annotations on the pull request
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from manifest.tools.inventory import inventory_map


@dataclass
class Problem:
    file: str  # relative to the current directory, as GitHub annotations need
    line: int | None
    kind: str
    message: str
    fix: str

    def text(self) -> str:
        return f"{self.file}:{self.line or 1}: {self.message} Fix: {self.fix}"

    def github(self) -> str:
        msg = f"{self.message} Fix: {self.fix}".replace("%", "%25").replace("\n", "%0A")
        return f"::error file={self.file},line={self.line or 1},title=MANIFEST {self.kind}::{msg}"


def lint_map(map_path: Path) -> list[Problem]:
    inv = inventory_map(map_path)
    base = map_path.parent
    out: list[Problem] = []
    for tp in inv.topics:
        f = os.path.relpath(base / tp.path)
        for k in tp.keyrefs:
            if not k.defined:
                out.append(
                    Problem(
                        f,
                        k.line,
                        "key",
                        f'<{k.element} keyref="{k.key}"> uses a key the map doesn\'t define.',
                        f"define the key {k.key!r} in a keydef, or correct the keyref.",
                    )
                )
        for c in tp.conrefs:
            if not c.resolves:
                out.append(
                    Problem(
                        f,
                        c.line,
                        "conref",
                        f'<{c.element} conref="{c.target}"> doesn\'t resolve.',
                        "check the file path, the topic ID and the element ID after the slash.",
                    )
                )
        for link in tp.links:
            if link.scope == "local" and link.resolves is False:
                out.append(
                    Problem(
                        f,
                        link.line,
                        "link",
                        f'<{link.element} href="{link.href}"> points to nothing.',
                        "correct the path or ID, or add the target topic to the map.",
                    )
                )
        for m in tp.media:
            if m.scope == "local" and m.resolves is False:
                out.append(
                    Problem(
                        f,
                        m.line,
                        "image",
                        f"<{m.element}> refers to {m.href}, which doesn't exist.",
                        "add the file or correct the path.",
                    )
                )
    return out
