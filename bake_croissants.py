"""bakery/bake_croissants.py - finishing par-baked Croissants one file at a time, at the terminal

    python3 bake_croissants.py <parbake output | its croissants folder | one file>
    python3 bake_croissants.py <directory> --list      # what is missing, ask nothing
The older path; the bakery's interface is the main one.
Answers go to a new <name>.baked.json; the par-baked file is never touched.
The file is described as a cr:FileSet matching its name, so no checksum is needed.
At any prompt: a value, "?" for help, blank to skip, "q" to save and stop.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import argparse
import functools
import json
from pathlib import Path

from croissant_files import (
    BAKED_SUFFIX,
    PARBAKED_SUFFIX,
    conforms_to,
    find_parbaked_files,
    is_parbaked,
    parbaked_name,
)
from parbake_link import CROISSANT_SUBDIRECTORY
from questions import find_questions, set_answer, wrap_answer

# Shown before the first question, where it will be read.
PROMPT_HELP = """At any prompt:
    a value    record it
    ?          show what the question means, and more evidence
    <enter>    skip this one for now, come back later
    q          save and stop"""

# Properties of a FileObject that a FileSet does not have.
FILE_OBJECT_ONLY = ("contentUrl", "contentSize", "sha256", "md5")


def as_file_set(document):
    """Turn the document's FileObject into a FileSet matching its name, and
    point the fields at it."""
    for entry in document.get("distribution") or []:
        if entry.get("@type") != "cr:FileObject":
            continue
        entry["@type"] = "cr:FileSet"
        entry["includes"] = entry.get("name") or entry.get("@id")
        for key in FILE_OBJECT_ONLY:
            entry.pop(key, None)
    for record_set in document.get("recordSet") or []:
        for field in record_set.get("field") or []:
            source = field.get("source") or {}
            if "fileObject" in source:
                source["fileSet"] = source.pop("fileObject")


# --- asking ---

class StopBaking(Exception):
    """The person asked to stop; answers so far are kept."""


SUFFIXES = {
    "lines": "  (one per line, blank line to finish)",
    "choice": "  (number, or type the value)",
}


def ask_more_lines(first, ask):
    """Return first plus further lines until a blank one; raise StopBaking on q."""
    collected = [first]
    while True:
        more = ask("  > ")
        # "q" stops here too, or a closed stdin returning it loops forever.
        if more is None or more.strip().lower() == "q":
            raise StopBaking()
        if not more.strip():
            return collected
        collected.append(more.strip())


def ask_question(question, ask, say, number, total):
    """Return the answer, or None if skipped; raise StopBaking to stop."""
    say("")
    say(f"  [{number}/{total}] {question.key}")
    if question.evidence:
        for line in question.evidence.splitlines():
            say(f"        {line}")

    while True:
        if question.kind == "choice":
            for index, choice in enumerate(question.choices, start=1):
                say(f"        {index}. {choice}")

        raw = ask(f"  {question.prompt}{SUFFIXES.get(question.kind, '')}\n  > ")
        if raw is None or raw.strip().lower() == "q":
            raise StopBaking()
        answer = raw.strip()
        if answer == "?":
            say(f"        {question.help_text or 'No further guidance for this one.'}")
            continue
        if answer == "":
            return None

        if question.kind == "choice":
            if answer.isdigit() and 1 <= int(answer) <= len(question.choices):
                return question.choices[int(answer) - 1]
            if answer in question.choices:
                return answer
            say(f"        Not one of the choices. Pick 1-{len(question.choices)}, "
                "or press enter to skip.")
            continue

        if question.kind == "lines":
            return ask_more_lines(answer, ask)
        return answer


# --- one document ---

def bake_document(document, ask, say, save=None):
    """Ask about every gap, calling save() after each answer.
    Return (answered, skipped, stopped early)."""
    questions = find_questions(document)
    answered = skipped = 0
    stopped = False

    for number, question in enumerate(questions, start=1):
        try:
            answer = ask_question(question, ask, say, number, len(questions))
        except StopBaking:
            stopped = True
            break

        if answer is None:
            skipped += 1
            continue

        set_answer(document, question.where, wrap_answer(question.key, answer))
        answered += 1
        if save is not None:
            save(document)

    return answered, skipped, stopped


def finish_if_complete(document, say=None):
    """Set a real conformsTo if nothing is left to ask; return whether nothing is."""
    if find_questions(document):
        return False
    if is_parbaked(document):
        document["conformsTo"] = conforms_to(finished=True)
        document.pop("_parbake", None)
        if say:
            say("\n  Nothing left unanswered. conformsTo now names a real Croissant "
                "version, so this file can be validated.")
    return True


# --- files and directories ---

def output_path_for(parbaked_path, output_directory):
    """Return the .baked.json path for a par-baked file."""
    return Path(output_directory) / f"{parbaked_name(parbaked_path)}{BAKED_SUFFIX}"


def load_starting_point(parbaked_path, output_path, say):
    """Return the existing baked file if there is one, else the par-baked file."""
    if output_path.is_file():
        if say:
            say(f"  Carrying on from {output_path.name}")
        return json.loads(output_path.read_text(encoding="utf-8"))
    return json.loads(parbaked_path.read_text(encoding="utf-8"))


def save_document(output_path, current):
    """Write the document as JSON."""
    output_path.write_text(json.dumps(current, indent=2), encoding="utf-8")


def bake_file(parbaked_path, output_directory, ask=input, say=print):
    """Work through one par-baked file; return a summary dict."""
    parbaked_path = Path(parbaked_path)
    output_path = output_path_for(parbaked_path, output_directory)
    Path(output_directory).mkdir(parents=True, exist_ok=True)

    document = load_starting_point(parbaked_path, output_path, say)
    as_file_set(document)
    outstanding = len(find_questions(document))

    say("")
    say("=" * 72)
    say(f"  {parbaked_path.name}")
    say(f"  {outstanding} question(s) outstanding  ->  {output_path.name}")
    say("=" * 72)

    save = functools.partial(save_document, output_path)

    if outstanding == 0:
        say("  Nothing left to ask.")
        finish_if_complete(document, say)
        save(document)
        return {"file": parbaked_path, "output": output_path, "answered": 0,
                "skipped": 0, "remaining": 0, "stopped": False}

    answered, skipped, stopped = bake_document(document, ask, say, save)
    finish_if_complete(document, say)
    save(document)

    left = len(find_questions(document))
    say("")
    say(f"  {answered} answered, {skipped} skipped, {left} still outstanding")
    say(f"  Written to {output_path}")

    return {"file": parbaked_path, "output": output_path, "answered": answered,
            "skipped": skipped, "remaining": left, "stopped": stopped}


def report_gaps(parbaked_files, output_directory, say=print):
    """Say what is missing from each file, asking nothing (--list)."""
    for parbaked_path in parbaked_files:
        output_path = output_path_for(parbaked_path, output_directory)
        document = load_starting_point(parbaked_path, output_path, say=None)
        questions = find_questions(document)

        say("")
        say(f"  {parbaked_path.name}: {len(questions)} outstanding")
        dataset_level = [q for q in questions if len(q.where) == 1]
        # Field questions reach into recordSet -> field.
        field_level = [q for q in questions if len(q.where) > 3]
        other = [q for q in questions
                 if q not in dataset_level and q not in field_level]

        if dataset_level:
            say(f"      dataset: {', '.join(q.key for q in dataset_level)}")
        if other:
            say(f"      also:    {', '.join(q.key for q in other)}")
        if field_level:
            columns = {q.key.split(" / ")[0] for q in field_level}
            say(f"      fields:  {len(field_level)} question(s) across "
                f"{len(columns)} column(s)")


def bake_directory(target, output_directory, ask=input, say=print):
    """Work through every par-baked file under target; return the summaries."""
    parbaked_files = find_parbaked_files(target)
    if not parbaked_files:
        say(f"  No {PARBAKED_SUFFIX} files found in {target}")
        return []

    found_in = parbaked_files[0].parent
    if found_in != Path(target):
        say(f"  Found {len(parbaked_files)} par-baked file(s) in {found_in}")

    results = []
    for parbaked_path in parbaked_files:
        outcome = bake_file(parbaked_path, output_directory, ask, say)
        results.append(outcome)
        if outcome["stopped"]:
            say("\n  Stopped. Run again to carry on where you left off.")
            break
    return results


def main():
    """Parse arguments and bake or list."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "target",
        help="a parbake output directory, its parbaked_croissants/ folder, "
             "or a single .parbaked.json file",
    )
    parser.add_argument(
        "--out", default="baked",
        help="where the baked copies go (default: ./baked)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="say what is missing and ask nothing",
    )
    arguments = parser.parse_args()

    target = Path(arguments.target)
    if not target.exists():
        raise SystemExit(f"No such file or directory: {target}")

    parbaked_files = find_parbaked_files(target)
    if not parbaked_files:
        raise SystemExit(
            f"No {PARBAKED_SUFFIX} files found in {target}.\n"
            f"Point at a parbake output directory, its {CROISSANT_SUBDIRECTORY}/ "
            "folder, or a single .parbaked.json file."
        )

    if arguments.list:
        report_gaps(parbaked_files, arguments.out)
        return

    print(PROMPT_HELP)
    try:
        bake_directory(target, arguments.out)
    except KeyboardInterrupt:
        print("\n\n  Interrupted. Everything answered so far has been saved.")


if __name__ == "__main__":
    main()
