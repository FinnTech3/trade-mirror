"""Turning Comtrade's numeric codes into countries.

The data rows carry codes and nothing else, every ``reporterDesc`` and
``partnerDesc`` field in a preview response comes back ``None``, so names have
to be joined in from separate reference tables.

The tables also carry the flag that matters most here: ``isGroup``. Not every
"partner" is a country. "World", "EU-27", "Africa CAMEU region, nes" and
"Other Asia, nes" all appear alongside France and Japan with nothing in a data
row to tell them apart. Treating a group as a country in bilateral analysis
counts the same trade twice and produces a mirror gap that is pure artefact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

#: Comtrade's code for the whole world. The largest group of all, and the one
#: most likely to slip through and quietly double every total.
WORLD = 0


@dataclass(frozen=True, slots=True)
class Area:
    """A reporter or partner: either a country, or an aggregate pretending."""

    code: int
    name: str
    iso3: str | None
    is_group: bool

    @property
    def is_country(self) -> bool:
        return not self.is_group


class CodeBook:
    """Lookup for reporter and partner codes."""

    def __init__(self, areas: dict[int, Area]) -> None:
        self._areas = areas

    @classmethod
    def from_files(cls, partners: Path | str, reporters: Path | str) -> CodeBook:
        areas: dict[int, Area] = {}
        # Reporters load first; partner entries win on conflict because the
        # partner list is the larger of the two and the one data rows index.
        areas.update(cls._load(reporters, "reporter"))
        areas.update(cls._load(partners, "Partner"))
        return cls(areas)

    @staticmethod
    def _load(path: Path | str, prefix: str) -> dict[int, Area]:
        # Accepts a Traversable as well as a path, so it works whether the
        # tables are read from the installed package or from a checkout.
        payload = json.loads(Path(str(path)).read_text(encoding="utf-8-sig"))
        out: dict[int, Area] = {}
        for row in payload.get("results", []):
            code = row.get(f"{prefix}Code")
            if code is None:
                continue
            code = int(code)
            out[code] = Area(
                code=code,
                name=row.get(f"{prefix}Desc") or row.get("text") or str(code),
                iso3=row.get(f"{prefix}CodeIsoAlpha3"),
                # Comtrade sends this as 1/0, and sometimes omits it entirely.
                is_group=bool(row.get("isGroup")) or code == WORLD,
            )
        return out

    def get(self, code: int) -> Area | None:
        return self._areas.get(code)

    def name(self, code: int) -> str:
        area = self._areas.get(code)
        return area.name if area else f"<unknown {code}>"

    def is_country(self, code: int) -> bool:
        """False for groups, for World, and for codes we have never seen.

        Unknown codes are treated as non-countries deliberately. A code absent
        from the reference tables is one we cannot vouch for, and letting it
        into a bilateral comparison risks pairing something with itself.
        """
        area = self._areas.get(code)
        return area is not None and area.is_country

    @cached_property
    def countries(self) -> tuple[Area, ...]:
        return tuple(a for a in self._areas.values() if a.is_country)

    @cached_property
    def groups(self) -> tuple[Area, ...]:
        return tuple(a for a in self._areas.values() if a.is_group)

    def __len__(self) -> int:
        return len(self._areas)
