"""Match event subjects/assignees to the configured people roster."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from config import Person


def _norm(value: str | None) -> str:
    return (value or "").strip().casefold()


def find_person(people: Iterable[Person], platform: str, login: str | None) -> Person | None:
    needle = _norm(login)
    if not needle:
        return None
    for person in people:
        if _norm(person.login_for(platform)) == needle:
            return person
    return None


def matched_people(people: tuple[Person, ...], event: dict[str, Any]) -> list[Person]:
    """Return the people relevant to one event.

    Assignment/unassignment events belong to exactly their subject. State
    events (opened/closed/reopened/merged) are relevant to every assignee in
    the event-time snapshot.
    """
    platform = event.get("platform") or ""
    subject = event.get("subject") or {}
    if subject.get("login"):
        person = find_person(people, platform, subject.get("login"))
        return [person] if person else []

    matches: list[Person] = []
    seen: set[str] = set()
    for assignee in event.get("assignees") or []:
        person = find_person(people, platform, assignee.get("login"))
        if person and person.name not in seen:
            seen.add(person.name)
            matches.append(person)
    return matches
