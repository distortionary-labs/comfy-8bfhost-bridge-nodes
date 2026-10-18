"""The filters 8bf-host found, as the node's filter list uses them."""

from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

# Shown in the filter list when there is nothing to choose.
NO_FILTERS = "(no 8bf filters found)"
NO_HOST = "(8bf-host not set up)"


def _strings(value):
    return [str(entry) for entry in value] if isinstance(value, list) else []


def _text(value):
    return None if value is None else str(value)


@dataclass
class Filter:
    id: str
    name: str
    category: str
    path: str = ""
    archs: List[str] = field(default_factory=list)
    modes: List[str] = field(default_factory=list)
    has_about_box: bool = False
    # Where 8bf-host would run it, or None with ``problem`` saying why it cannot.
    runs: Optional[str] = None
    problem: Optional[str] = None

    @classmethod
    def from_json(cls, payload):
        return cls(
            id=str(payload["id"]),
            name=str(payload.get("name", "")),
            category=str(payload.get("category", "")) or "Other",
            path=str(payload.get("path", "")),
            archs=_strings(payload.get("archs")),
            modes=_strings(payload.get("modes")),
            has_about_box=bool(payload.get("has_about_box")),
            runs=_text(payload.get("runs")),
            problem=_text(payload.get("problem")),
        )

    @property
    def runnable(self):
        return self.runs is not None

    @property
    def label(self):
        return "%s/%s" % (self.category, self.name)


class Catalog:
    def __init__(self):
        self.filters = []
        self.problems = []
        # Why the list could not be read at all (8bf-host missing or failing).
        self.error = None

    def load(self, filters, problems=()):
        entries = [
            Filter.from_json(payload)
            for payload in filters
            if isinstance(payload, dict) and payload.get("id")
        ]
        entries.sort(key=lambda e: (e.category.lower(), e.name.lower()))
        self.filters = entries
        self.problems = [
            (str(p.get("path", "")), str(p.get("message", "")))
            for p in problems
            if isinstance(p, dict)
        ]
        self.error = None

    def fail(self, message):
        self.filters = []
        self.problems = []
        self.error = message

    def find(self, filter_id):
        for entry in self.filters:
            if entry.id == filter_id:
                return entry
        return None

    def choices(self):
        """Map each runnable filter's list entry to its id.

        Entries are ``Category/Name``, which keeps saved workflows working
        when a filter file is replaced. Two filters with the same name get
        their id appended, since 8bf-host could not tell them apart either.
        """
        runnable = [entry for entry in self.filters if entry.runnable]
        counts = Counter(entry.label.lower() for entry in runnable)
        out = {}
        for entry in runnable:
            label = entry.label
            if counts[label.lower()] > 1:
                label = "%s [%s]" % (label, entry.id)
            out[label] = entry.id
        return out

    def placeholder(self):
        """The list entry to show when ``choices`` is empty."""
        return NO_HOST if self.error else NO_FILTERS
