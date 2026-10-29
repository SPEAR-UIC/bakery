# bakery

Finishes what [parbake](../parbake/) starts.

parbake scans CSVs and writes par-baked Croissant files: identity, column
names, and measurements, with every judgement left out and validation
deliberately failing. The bakery reads those files into a database, asks a
person each outstanding question with the measurements on screen, and writes
finished Croissants back out.

Answers are stored per machine and per dataset, not per file. A column
described once for POLARIS is offered as the default for every later POLARIS
export. On the corpus in `../bakery_test_out`, that reduces roughly 2,400
questions to a handful of new ones per import.

## Install

Python 3.11 or newer (for `hashlib.file_digest`; recorded in
`pyproject.toml`).

This directory contains an in-place virtualenv (`bin/`, `lib/`, `pyvenv.cfg`,
created with `python3 -m venv .`). Run the modules in place with that
interpreter; do not install the bakery as a package.

```
./bin/pip install sqlalchemy textual pandas psutil
```

`sqlalchemy` and `textual` are the bakery's own. `pandas` and `psutil` are
parbake's, but everything here imports parbake through `parbake_link.py`, so
they are required too.

Optional:

```
./bin/pip install 'psycopg[binary]'   # PostgreSQL backend
./bin/pip install pymysql             # MySQL backend
./bin/pip install mlcroissant         # the two tests that validate output
```

pandoc, for the `.baked.txt` beside each Croissant (`apt install pandoc`).

SQLite, the default backend, needs nothing.

The bakery expects parbake to sit alongside it at `../parbake`. That is the
only cross-directory dependency, and `parbake_link.py` is the only module that
touches it; it also re-exports parbake's markers, output directory names, and
Markdown renderer.

## Usage

The examples say `python3`, which works when an environment with the
dependencies above is active — in this directory that is `./bin/python`,
or whichever venv you installed the requirements into.

Typical first session:

```
python3 bakery.py --seed ../complete_documentation ../croissant_files \
                  --import ../bakery_test_out
```

`--seed` and `--import` run before the interface opens and print a report.
Both are idempotent, so rerun them whenever parbake produces more output.
The interface then runs until you press `q`.

Later sessions usually need no flags at all; the database (`./bakery.sqlite`
by default) already knows what it knows:

```
python3 bakery.py
```

All options:

| option | effect |
|---|---|
| `--seed PATH...` | read finished Croissants, field dictionaries, and documentation into the database, unconfirmed |
| `--import DIR` | read a parbake output directory (or its `parbaked_croissants/` folder, or one `.parbaked.json`) into the database |
| `--bake` | write out every file that has a machine, then stop |
| `--notes PATH...` | list every `NOTE:` marker in the Croissants under these paths, then stop |
| `--out DIR` | where written Croissants go (default `./baked`) |
| `--database URL` | which database to use (see Database) |

`python3 bakery.py --help` prints the same list.

Bake without opening the interface (this is the step you put in a script):

```
python3 bakery.py --bake --out ../croissant_files
```

### The older file-in, file-out path

`bake_croissants.py` predates the database. It works on one par-baked file at
a time with plain terminal prompts and writes a single `<name>.baked.json`;
it never touches the original.

```
python3 bake_croissants.py ../parbake/parbake_output
python3 bake_croissants.py <one file>.parbaked.json
python3 bake_croissants.py <directory> --list    # what is missing, ask nothing
```

At any prompt: a value records it, `?` explains the question, blank skips it,
`q` saves and stops. It resumes from a partially answered `<name>.baked.json`
if one exists. The file is written as a `cr:FileSet` matching its own name, so
no checksum is computed. The TUI is the main path; this one is kept for
single-file work.

## The interface

A Textual app with eighteen screens. A file set is a dataset: files of one
format that share a machine and answers and bake to one Croissant.

| screen | how to reach it | what it is |
|---|---|---|
| sets | opens here | every file set: machine, files, columns confirmed, set questions left; then files in no set |
| files | `v` on the sets | every imported file and its set, least finished first; `esc` back |
| set name | `R` on the sets; `s` when it makes a new set | name or rename a set |
| machine | `enter` on a file with no machine | which system the file came from, with the evidence |
| columns | `enter` on a set, or on a file with a machine | one column at a time, measurements beside each question |
| review | `a` | every column of a file, answered or not; `enter` reopens one |
| notes | `n` | everything flagged in this database to come back to |
| machines | `m` | each system, its iterations and hardware |
| copy source / plan / difference | `c` | the three screens of a copy: pick the dataset, review the plan, decide each difference one at a time |
| same as | `s`, with files ticked | give the ticked files a chosen file's machine and answers |
| group | `g`, with files ticked | put the ticked files in one dataset, by name |
| decoding table | `ctrl+e` on the columns screen | link the column to a decoding table, or to none |
| table editor | `new table...` or `edit` in the decoding-table list | make or change a table; saving links it to the column |
| dataset questions | `d` on the file list or review | the dataset's own questions: licence, citation, RAI |
| answer | `enter` on a dataset question | edit one answer; saving confirms it |
| report | after a bulk change | what the change did |

Keys on the sets: `enter` answer the set's columns, `a` review them, `d` the
set questions, `b` write out the set's Croissant, `R` rename, `v` the files,
`n` notes, `m` machines, `r` refresh, `q` quit.

Keys on the file list: `b` write out the selected file's dataset, `M` change its
machine, `c` copy another dataset's answers in, `space` tick a file, `T` tick
every file from the last ticked to this one, `U` untick all, `g` group the
ticked files under a new name, `s` give the ticked files (or the file under the
cursor) another file's machine and answers (and asks for a name when that
makes a new set), `d` the dataset questions, `r` refresh, `esc` back to the
sets, `q` quit.

Keys on the columns screen: `ctrl+s` record and move to the next column,
`ctrl+a` accept the offered answer as it stands, `ctrl+e` pick the decoding
table, `ctrl+k` / `ctrl+j` next / previous column, `esc` back.

Every answer is written to the database the moment it is given. Closing the
terminal loses the question being typed and nothing else.

### What gets asked

The order is fixed because each step scopes the next.

1. **Machine.** Import records a suggestion, never a fact. It comes from the
   filename and, if present, a `MACHINE_NAME` column with a single distinct
   value; where the two disagree, the column wins and the disagreement is
   shown. A machine already in the database is recognised wherever it appears
   in a name, so suggestions improve as the database fills. Naming the
   machine puts the file in a dataset (named `<MACHINE>_<KIND>`), and column
   descriptions belong to the dataset.
2. **Columns**, in file order. Each question shows parbake's measurements
   (row count, distinct values, most common value, numeric range) and, if one
   exists, a pre-filled offer. Offers are searched nearest first: an answer
   already recorded here (seeded, unconfirmed), the same column in a sibling
   dataset on this machine, the same column name on another machine, or the
   same name in another case. An offer is shown, never applied.

A column has three states: unanswered, offered (recorded or seeded but not
confirmed here), and confirmed. Only confirmed counts as done.

### Decoding tables

A decoding table maps a column's codes to meanings (`3` is `daemon`). It
belongs to one machine.

- `ctrl+e` on the columns screen lists this machine's tables, each with how
  many of the codes seen it decodes. The first entry is no table. `o` adds
  other machines' tables; choosing one copies it to this machine.
- `new table...` and `edit <table>...` open the editor: name, description,
  one `code = meaning` per line, starting with the undecoded codes. Lines
  without a meaning are dropped. A bad line or a name in use saves nothing.
  `ctrl+s` saves and links. An edit applies to every column using the table.
- A meaning containing "provisional" or "open question" is marked provisional.
- `ctrl+a` on an offer from the same machine also links its table. An offer
  from another machine brings its description only.
- The review shows each column's table.
- Seeding: a `cr:isEnumeration` record set and a `references` on the column.

### Dataset questions

Answered once per dataset, for every file in it: `d` on the file list or the
review lists all 15 with their state (`set`, `seeded`, `-`).

- Needed for a finished file (marked `*`): description, publisher, citeAs and
  the five `rai:` answers.
- Defaults: version `1.0`; datePublished the day of baking.
- Optional: url, creator, license, keywords.
- In the answer box: lists one item per line, objects as JSON. `ctrl+s`
  saves and confirms, including a seeded answer as it is.
- `rai:dataCollectionType` takes values from the Croissant RAI list (Direct
  measurement, Software Collection, ...); synthetic data is recorded there
  and in the limitations. `rai:hasSyntheticData` is not a RAI 1.0 term and is
  not asked or written.
  Saving blank removes the answer.
- Also entered by `--seed`, by copying answers in, or by `bake_croissants.py`.

Machine profiles are not asked in the TUI; they come from `--seed`.

### Seeding

`--seed` reads finished work in so it does not have to be typed again. It
accepts directories and files, and picks up three shapes:

- `*.croissant.json`: field descriptions and types, dataset-level answers,
  value decodings, and `references` links from fields to them
- `*_field_dictionary*.csv`: the unit column, which Croissant has no property
  for
- `*_documentation*.md`: the machine profile (vendor, architecture,
  partitions, node counts)

Everything seeded lands unconfirmed, tagged with a `recorded_by` naming its
source file. It appears as an offer at the prompt, confirmable with one
keystroke. A confirmed answer is never overwritten by a seeded file; where
two unconfirmed sources disagree, the first read wins and the disagreement is
reported. Placeholder descriptions (`(no meaning supplied for X)`) are not
seeded as descriptions, so those columns stay outstanding.

### Bulk corrections

Four operations are tedious column by column, so each has a screen:

- **Same as a file** (`s` on the file list). Answer one file, then tick the
  rest of its format (`space`, `T` for a range), press `s` and pick the
  answered file. The ticked files join its dataset: they take its machine
  and every answer, and anything answered later applies to all of them.
  Files whose columns differ from the picked file are named in the report.

- **Group files as one format** (`g` on the file list). For files the name
  parser cannot place in a shared dataset, such as 1,200 syslog files named
  `POLARIS.log_syslog_<date>_<part>`, each of which would otherwise become a
  dataset of its own. Tick the files (`space` for one, `T` for a range;
  untouched files are listed together in name order), press `g`, and give a
  machine and a dataset name, or pick an existing dataset to add them to.
  Each file then moves as **Change machine** below moves it, so a file
  already worked on brings its answers into the group. Answers belong to the
  dataset, so a column described while working on any one of the files is
  described for all of them.

- **Change machine** (`M` on the file list, `m` on review). The file moves to
  the new machine's dataset and its answers follow. If it was the only file
  in its old dataset, the answers move and the empty dataset is removed; if
  others remain, the answers are copied and those files keep theirs. Where
  the new dataset has a confirmed, complete answer, that one is kept and the
  difference is reported.
- **Copy answers in** (`c`). Pick another dataset (the list is sorted by how
  many of this file's columns it has confirmed), review the plan, then write.
  Only answers confirmed in the source are copied. Gaps are filled silently;
  where both sides have different answers, each is shown and you choose (`u`
  take the copy, `k` keep the current, `a` / `x` for all remaining). Leaving
  partway through abandons the entire copy.

### Notes

A note is a flag for a person to come back to on a column that is otherwise
complete ("disagrees with the accounting table by 2%"). It has its own entry
on the columns screen and its own database column. On writing, it is appended
to the description as `NOTE: ...` so it travels with the published file. The
marker is case-sensitive (`NOTE:` or `NOTES:` at a word boundary); a
case-insensitive match would catch the word "notes" in parbake's banner and
report nineteen things nobody wrote.

Two ways to list notes: `n` inside the app shows what this database records,
including notes not yet written out. `--notes PATH...` reads Croissant files
off disk, so it also finds notes typed into descriptions by hand and notes in
files this database has never imported.

```
python3 bakery.py --notes ./baked ../croissant_files
```

## Output

One `<DATASET>.baked.json` per dataset, written to `--out` (default
`./baked`), with a `.baked.md` beside it rendered by parbake's renderer and a
`.baked.txt` rendered from that by pandoc (skipped, with a warning, if pandoc
is not installed). Its
data is one `cr:FileSet` whose `includes` glob matches every file of the
dataset, so no checksum is needed and the data need not be reachable from
where the bakery runs. `b` on the file list writes the selected file's
dataset; `--bake` writes every dataset that has files.

The par-baked marker is the one thing preventing an unfinished file from
looking finished. It is removed only when every column is confirmed, the
needed dataset questions are confirmed (see Dataset questions). Until then
the file is still written, with the marker in place and an `_unfinished`
block listing every reason, each column once. A finished file validates with
`mlcroissant`; an unfinished one does not.

What `mlcroissant` 1.1 itself requires, measured: every column's dataType; a
sha256 or md5 on a FileObject; datePublished, if present, as YYYY-MM-DD. It
warns without version, license, citeAs or datePublished. The rest is optional
to the validator and required here only where listed above.

Units are appended to descriptions (Croissant has no property for them).
Linked decoding tables are written as record sets, keeping seeded
descriptions of their code and meaning fields. A code seen in the data that
the table does not decode is appended to the column's description
(`UNDECODED: 137 (no meaning in exit_codes).`) and reported as a warning; the
file can still finish. Only codes parbake listed are checked
(`--values-shown`, default 10).
`contentUrl` is the bare filename, never the path the data occupied during
import.

## Database

SQLite by default: a single file, `./bakery.sqlite`, in the working
directory, which is why running the tool twice remembers the first run.
The URL is taken in order from `--database`, then the environment variable
`BAKERY_DATABASE_URL`, then the local file.

```
sqlite:///bakery.sqlite
postgresql+psycopg://user@host/bakery
mysql+pymysql://user@host/bakery
```

A missing driver reports what to install. Passwords are never printed.
A MySQL URL gets `charset=utf8mb4` unless it names a charset. When the
database is SQLite, the interface says so in a red banner and in every header.

Move a database into an empty one, keeping every row and id:

```
python3 migrate_database.py sqlite:///bakery.sqlite "$BAKERY_DATABASE_URL"
```

Create the MySQL database with `CHARACTER SET utf8mb4 COLLATE utf8mb4_bin`:
the binary collation compares names case-sensitively, as SQLite does.

The schema is versioned inside the database. Opening an older database
migrates it forward, one additive step per transaction, so an interrupted
upgrade resumes. Current version: 3. A database from a later version is
refused, because this build cannot know what that version did to it. SQLite
foreign keys are enforced per connection with a pragma, so cascades behave
the same on every backend.

| table | level |
|---|---|
| `machines` | a system, by name |
| `machine_iterations` | what a machine was made of, between two dates |
| `machine_partitions` | per-partition hardware of one iteration |
| `datasets` | a series of files, e.g. `POLARIS_DJC`, every date range of it |
| `source_files` | one imported par-baked file |
| `file_fields` | its columns, with parbake's measurements |
| `field_docs` | a column's description, type, unit, note (per dataset) |
| `enumerations` / `enumeration_values` | a decoding table (per machine) |
| `dataset_answers` | licence, citation, the responsible-AI blocks (per dataset) |
| `derived_files` | the `.txt` and `.md` siblings, recorded and never read |

Two placement decisions worth knowing. Hardware hangs off a dated iteration
rather than the machine, because machines change and a node refresh does not
change what `EXIT_CODE` holds. Column descriptions hang off datasets rather
than files or machines, because on one machine a column can mean different
things in different exports (a `START_TIMESTAMP` is when a job began in one
export and when an outage began in another), so one description per machine
would be wrong for at least one of them.

## Tests

```
./bin/pip install pytest
./bin/python -m pytest
```

A bare `pytest` on PATH works only if its interpreter also has `sqlalchemy`
and `textual` importable; the in-place venv does not ship pytest, so install it
in as above and call it through that interpreter.

309 tests. The deciding logic (what is outstanding, what is offered, how
imports and seeds land) lives in `outstanding.py`, `importing.py`,
`seeding.py`, and `transfer.py`, and is tested without a terminal. The
screens are tested through Textual's headless driver. The writing is tested
by validating the output with `mlcroissant` where it is installed (two
tests, skipped without it). Some tests run against the real corpus in
`../bakery_test_out`.

## Modules

| file | role |
|---|---|
| `bakery.py` | the app and the command line |
| `screens/` | the interface: `files`, `columns` (answering and review), `tables` (decoding-table picker and editor), `machine` (machine, grouping, machines page), `copy`, `notes`, plus `common` and `actions` shared between them |
| `bake_croissants.py` | the older file-in, file-out path |
| `models.py` | the tables, the states, the schema version |
| `database.py` | opening a URL, the version check, migrations |
| `queries.py` | find-or-create lookups shared by the modules below |
| `croissant_files.py` | file suffixes, checksums of par-baked files, reading JSON, recognising par-baked files, conformsTo |
| `importing.py` | `--import`: read par-baked Croissants, parse names, suggest a machine |
| `seeding.py` | `--seed`: read finished work in, unconfirmed |
| `documentation.py` | parse the System section of a documentation Markdown file |
| `outstanding.py` | what each file still needs, what other datasets offer, recording answers |
| `transfer.py` | change a file's machine, copy answers between datasets, group files as one format |
| `enumerations.py` | decoding tables: make and edit them, link columns to them, check them against the codes seen |
| `baking.py` | write the two output shapes, plus Markdown |
| `notes.py` | find `NOTE:` markers in files or in the database |
| `questions.py` | the dataset-level question catalogue, and the measurement renderer |
| `parbake_link.py` | the only module that reaches into parbake |

Uncommitted state (see `.gitignore`): the virtualenv itself, `*.sqlite`,
`__pycache__/`, `.pytest_cache/`, and `baked/`.
