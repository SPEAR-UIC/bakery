"""bakery/documentation.py - read the machine profile from a documentation Markdown file

Source: section 3, "System", and the Record metadata section.
Text parsing only; seeding.py writes the result to the database.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import re
from datetime import date

# Bullet label -> profile or partition key.
ITERATION_KEYS = {
    "vendor": "vendor",
    "architecture": "architecture",
    "scheduler": "scheduler",
    "system partition": "partition_summary",
}
PARTITION_KEYS = {
    "cpu": "cpu",
    "gpu": "gpu",
    "interconnect": "interconnect",
    "memory type": "memory_type",
    "storage": "storage",
    "node count": "node_count",
    "rack count": "rack_count",
}
MONTHS = {name.lower(): number for number, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def as_count(written):
    """Return "10,624" as 10624; anything else as None."""
    cleaned = (written or "").replace(",", "").strip()
    return int(cleaned) if cleaned.isdigit() else None


def one_date(part):
    """Return a date written in prose, or None; a month alone is its first day."""
    part = part.strip().rstrip(".")
    found = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", part)      # 27 January 2025
    if found and found[2].lower() in MONTHS:
        return date(int(found[3]), MONTHS[found[2].lower()], int(found[1]))
    found = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", part)    # January 27, 2025
    if found and found[1].lower() in MONTHS:
        return date(int(found[3]), MONTHS[found[1].lower()], int(found[2]))
    found = re.search(r"([A-Za-z]+)\s+(\d{4})", part)                  # August 2022
    if found and found[1].lower() in MONTHS:
        return date(int(found[2]), MONTHS[found[1].lower()], 1)
    found = re.search(r"(\d{4})-(\d{2})-(\d{2})", part)
    if found:
        return date(int(found[1]), int(found[2]), int(found[3]))
    return None


def parse_deployment_dates(written):
    """Return (starts_on, ends_on) for "August 2022 - present"; ends_on None if open."""
    text = (written or "").strip()
    if not text:
        return None, None
    halves = re.split(r"\s+(?:-|--|–|to)\s+", text, maxsplit=1)
    starts = one_date(halves[0])
    if len(halves) == 1:
        return starts, None
    if halves[1].strip().lower().startswith(("present", "ongoing", "now", "current")):
        return starts, None
    return starts, one_date(halves[1])


def read_section(text, heading_words):
    """Return the bullet lines under a heading, matched on its words."""
    # Matched on words, not number: the documents number their sections differently.
    pattern = re.compile(r"^#{1,4}\s*(?:\d+\.?\s*)?" + re.escape(heading_words) + r"\s*$",
                         re.IGNORECASE | re.MULTILINE)
    found = pattern.search(text)
    if not found:
        return []
    rest = text[found.end():]
    next_heading = re.search(r"^#{1,4}\s", rest, re.MULTILINE)
    body = rest[: next_heading.start()] if next_heading else rest
    return [line.rstrip() for line in body.splitlines() if line.strip().startswith("-")]


def new_partition(partitions, name):
    """Append and return a new partition dict with this name."""
    entry = {"name": name}
    partitions.append(entry)
    return entry


def parse_system_section(lines):
    """Return (profile, partitions) from the System section's bullet lines."""
    # Polaris writes flat bullets; Aurora indents them under "Partition One:".
    profile = {"system_name": None, "deployment": None}
    partitions = []
    current = {}
    for line in lines:
        indented = len(line) - len(line.lstrip())
        stripped = line.lstrip()[1:].strip()          # drop the bullet
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key, value = key.strip().lower(), value.strip()

        if key.startswith("partition ") and not value:
            current = new_partition(partitions, stripped.rstrip(":").strip())
        elif key == "system name":
            profile["system_name"] = value
        elif key.startswith("system deployment"):
            profile["deployment"] = value
        elif key in ITERATION_KEYS:
            profile[ITERATION_KEYS[key]] = value
        elif key in PARTITION_KEYS:
            column = PARTITION_KEYS[key]
            if not partitions or (indented == 0 and column in current):
                current = new_partition(partitions, "default" if not partitions
                                        else f"partition {len(partitions) + 1}")
            current[column] = as_count(value) if column.endswith("_count") else value or None
    return profile, partitions


def organisation_in(text):
    """Return the "Originating organization" bullet of Record metadata, or None."""
    for line in read_section(text, "Record metadata"):
        stripped = line.lstrip()[1:].strip()
        if stripped.lower().startswith("originating organization") and ":" in stripped:
            return stripped.split(":", 1)[1].strip()
    return None
