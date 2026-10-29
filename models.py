"""bakery/models.py - the bakery's SQLAlchemy tables

Machine -> MachineIteration (dated hardware) -> MachinePartition.
Machine -> Dataset -> SourceFile -> FileField; Dataset -> FieldDoc, DatasetAnswer.
Machine -> Enumeration -> EnumerationValue (decoding tables).
String columns have lengths: MySQL will not index TEXT without one.
Machines match on a lowercased key: SQLite compares case-sensitively, MySQL usually not.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Bump when the tables change; another version's database is refused.
SCHEMA_VERSION = 4

STATE_IMPORTED = "imported"
STATE_IN_PROGRESS = "in_progress"
STATE_COMPLETE = "complete"

# recorded_by prefix for answers read from a finished file; offered, not settled.
SEEDED_BY_PREFIX = "seeded from "

PARTITION_FIELDS = ("name", "position", "cpu", "gpu", "interconnect", "memory_type",
                    "storage", "node_count", "rack_count", "notes")


def now():
    """Return the current UTC time, to the second."""
    # Set here, not by the database: backends disagree on CURRENT_TIMESTAMP's zone.
    return datetime.now(timezone.utc).replace(microsecond=0)


def machine_key(name):
    """Return the folded name machines are matched on."""
    return name.strip().lower()


class Base(DeclarativeBase):
    """Declarative base for every bakery table."""

    pass


class Answer:
    """Bookkeeping shared by FieldDoc and DatasetAnswer."""

    def confirm(self, recorded_by=None, when=None, source_file=None):
        """Mark confirmed; record who, when, and the first file seen in."""
        self.is_confirmed = True
        self.recorded_by = recorded_by
        self.recorded_at = when or now()
        if source_file is not None and self.first_seen_in is None:
            self.first_seen_in = source_file.id


class Meta(Base):
    """Housekeeping values, such as the schema version."""

    __tablename__ = "bakery_meta"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)


# --- machines ---

class Machine(Base):
    """A system data comes from; hardware is on its dated iterations."""
    # Decoding tables belong here, not to an iteration: codes belong to the scheduler.

    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    organization: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    iterations: Mapped[list["MachineIteration"]] = relationship(
        back_populates="machine", cascade="all, delete-orphan",
        order_by="MachineIteration.starts_on")
    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="machine", cascade="all, delete-orphan", passive_deletes=True)
    enumerations: Mapped[list["Enumeration"]] = relationship(
        back_populates="machine", cascade="all, delete-orphan")

    def __init__(self, name, **rest):
        """Strip the name and set its folded key."""
        super().__init__(name=name.strip(), key=machine_key(name), **rest)

    @property
    def current_iteration(self):
        """Return the latest open-ended iteration, or None if retired."""
        open_ended = [each for each in self.iterations if each.ends_on is None]
        if open_ended:
            return max(open_ended, key=lambda each: each.starts_on or date.min)
        return None

    def iteration_covering(self, day):
        """Return the iteration covering day, or None."""
        # A suggestion only: a filename's dates can disagree with its data.
        if day is None:
            return None
        return next((each for each in self.iterations if each.covers(day)), None)

    def __repr__(self):
        """Return Machine(...) for debugging."""
        return f"Machine({self.name!r})"


class MachineIteration(Base):
    """What a machine was made of between two dates; ends_on NULL is "present"."""
    # FIXME: nothing stops a machine having two open-ended iterations.

    __tablename__ = "machine_iterations"
    __table_args__ = (
        UniqueConstraint("machine_id", "label", name="one_iteration_per_label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="as deployed")

    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    # "August 2022" is stored as the 1st, with the original text kept here.
    dates_as_written: Mapped[str | None] = mapped_column(String(128))

    vendor: Mapped[str | None] = mapped_column(String(255))
    architecture: Mapped[str | None] = mapped_column(String(255))
    scheduler: Mapped[str | None] = mapped_column(String(128))
    partition_summary: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    derived_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("machine_iterations.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    machine: Mapped["Machine"] = relationship(back_populates="iterations")
    partitions: Mapped[list["MachinePartition"]] = relationship(
        back_populates="iteration", cascade="all, delete-orphan",
        order_by="MachinePartition.position")
    files: Mapped[list["SourceFile"]] = relationship(back_populates="iteration")

    @property
    def is_current(self):
        """Return whether this iteration has no end date."""
        return self.ends_on is None

    @property
    def is_profiled(self):
        """Return whether vendor, architecture, scheduler and partitions are all set."""
        return all([self.vendor, self.architecture, self.scheduler]) and bool(self.partitions)

    @property
    def span(self):
        """Return "start to end", with ? or present for missing dates."""
        return f"{self.starts_on or '?'} to {self.ends_on or 'present'}"

    def covers(self, day):
        """Return whether day falls within this iteration."""
        if self.starts_on and day < self.starts_on:
            return False
        return not (self.ends_on and day > self.ends_on)

    def overlaps(self, other):
        """Return whether this iteration and other share any day."""
        if self.ends_on and other.starts_on and other.starts_on > self.ends_on:
            return False
        return not (other.ends_on and self.starts_on and self.starts_on > other.ends_on)

    def next_iteration(self, starts_on, label, ends_previous=True):
        """Return an unsaved copy, partitions included, starting on starts_on.
        With ends_previous, end this open iteration the day before."""
        if ends_previous and self.ends_on is None and starts_on is not None:
            self.ends_on = starts_on - timedelta(days=1)
        successor = MachineIteration(
            machine_id=self.machine_id,
            label=label,
            starts_on=starts_on,
            ends_on=None,
            vendor=self.vendor,
            architecture=self.architecture,
            scheduler=self.scheduler,
            partition_summary=self.partition_summary,
            derived_from=self,
        )
        successor.partitions = [
            MachinePartition(**{name: getattr(each, name) for name in PARTITION_FIELDS})
            for each in self.partitions
        ]
        return successor

    def __repr__(self):
        """Return MachineIteration(...) for debugging."""
        return f"MachineIteration({self.label!r}, {self.span})"


MachineIteration.derived_from = relationship(
    "MachineIteration", remote_side=[MachineIteration.id])


class MachinePartition(Base):
    """One kind of node in one iteration; a homogeneous machine has one, "default"."""

    __tablename__ = "machine_partitions"
    __table_args__ = (
        UniqueConstraint("iteration_id", "name", name="one_row_per_partition"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    iteration_id: Mapped[int] = mapped_column(
        ForeignKey("machine_iterations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    cpu: Mapped[str | None] = mapped_column(Text)
    gpu: Mapped[str | None] = mapped_column(Text)
    interconnect: Mapped[str | None] = mapped_column(Text)
    memory_type: Mapped[str | None] = mapped_column(String(255))
    storage: Mapped[str | None] = mapped_column(String(255))
    node_count: Mapped[int | None] = mapped_column(Integer)
    rack_count: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)     # estimated counts are noted here

    iteration: Mapped["MachineIteration"] = relationship(back_populates="partitions")

    def __repr__(self):
        """Return MachinePartition(...) for debugging."""
        return f"MachinePartition({self.name!r}, iteration={self.iteration_id})"


# --- datasets and files ---

class Dataset(Base):
    """A series of files published as one, such as POLARIS_DJC; answers belong here."""

    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("machine_id", "name", name="one_dataset_per_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(64))

    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(32))
    date_published: Mapped[str | None] = mapped_column(String(32))
    # Template section 6: naming and distribution details Croissant has no slot for.
    filename_pattern: Mapped[str | None] = mapped_column(Text)
    format_notes: Mapped[str | None] = mapped_column(Text)

    hand_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    machine: Mapped["Machine"] = relationship(back_populates="datasets")
    files: Mapped[list["SourceFile"]] = relationship(
        back_populates="dataset", order_by="SourceFile.name", passive_deletes=True)
    answers: Mapped[list["DatasetAnswer"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan")
    field_docs: Mapped[list["FieldDoc"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan")

    def __repr__(self):
        """Return Dataset(...) for debugging."""
        return f"Dataset({self.name!r}, {len(self.files)} file(s))"


class SourceFile(Base):
    """One imported par-baked Croissant, matched by name; machine via its dataset."""

    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    kind: Mapped[str | None] = mapped_column(String(64))

    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"))
    iteration_id: Mapped[int | None] = mapped_column(
        ForeignKey("machine_iterations.id", ondelete="SET NULL"))
    # A guess from the name and MACHINE_NAME, offered for a person to confirm.
    machine_suggestion: Mapped[str | None] = mapped_column(String(128))

    parbaked_path: Mapped[str] = mapped_column(Text, nullable=False)
    # Shows whether the file has been re-measured since import.
    parbaked_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    baked_path: Mapped[str | None] = mapped_column(Text)

    # From the filename: a label, so it only suggests an iteration.
    covers_from: Mapped[date | None] = mapped_column(Date)
    covers_to: Mapped[date | None] = mapped_column(Date)

    source_csv_path: Mapped[str | None] = mapped_column(Text)
    # BIGINT: files pass 2 GiB and rows near 2**31; MySQL INT stops there.
    source_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    rows: Mapped[int | None] = mapped_column(BigInteger)
    # False after a parbake --preview: rows is then a lower bound. None: not recorded.
    rows_are_whole_file: Mapped[bool | None] = mapped_column(Boolean)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATE_IMPORTED)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    dataset: Mapped["Dataset | None"] = relationship(back_populates="files")
    iteration: Mapped["MachineIteration | None"] = relationship(back_populates="files")
    fields: Mapped[list["FileField"]] = relationship(
        back_populates="file", cascade="all, delete-orphan",
        order_by="FileField.position")
    derived: Mapped[list["DerivedFile"]] = relationship(
        back_populates="file", cascade="all, delete-orphan")

    @property
    def machine(self):
        """Return the dataset's machine, or None."""
        return self.dataset.machine if self.dataset else None

    @property
    def suggested_iteration(self):
        """Return the iteration the name's dates fall in, or None."""
        machine = self.machine
        if machine is None:
            return None
        return machine.iteration_covering(self.covers_from or self.covers_to)

    def __repr__(self):
        """Return SourceFile(...) for debugging."""
        return f"SourceFile({self.name!r}, {self.column_count} fields)"


class FileField(Base):
    """One column of one file, with parbake's measurements."""

    __tablename__ = "file_fields"
    __table_args__ = (UniqueConstraint("file_id", "name", name="one_row_per_column"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    measurements: Mapped[dict | None] = mapped_column(JSON)

    file: Mapped["SourceFile"] = relationship(back_populates="fields")

    def __repr__(self):
        """Return FileField(...) for debugging."""
        return f"FileField({self.name!r} of {self.file_id})"


# --- what people said ---

class FieldDoc(Answer, Base):
    """What a column means in one dataset; a seeded row is unconfirmed."""
    # Per dataset: one column name can mean different things in different exports.

    __tablename__ = "field_docs"
    __table_args__ = (
        UniqueConstraint("dataset_id", "field_name", name="one_description_per_dataset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[str | None] = mapped_column(Text)
    data_type: Mapped[str | None] = mapped_column(String(64))
    # Croissant has no slot for unit or note; baking appends them to the description.
    unit: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)
    enumeration_id: Mapped[int | None] = mapped_column(
        ForeignKey("enumerations.id", ondelete="SET NULL"))

    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    recorded_by: Mapped[str | None] = mapped_column(String(255))
    first_seen_in: Mapped[int | None] = mapped_column(
        ForeignKey("source_files.id", ondelete="SET NULL"))

    dataset: Mapped["Dataset"] = relationship(back_populates="field_docs")
    enumeration: Mapped["Enumeration | None"] = relationship(back_populates="field_docs")

    @property
    def has_answer(self):
        """Return whether a description or type is set."""
        return bool(self.description or self.data_type)

    @property
    def is_complete(self):
        """Return whether both description and type are set."""
        return bool(self.description) and bool(self.data_type)

    @property
    def is_settled(self):
        """Return whether confirmed and complete."""
        return self.is_confirmed and self.is_complete

    @property
    def needs_confirming(self):
        """Return whether complete but not confirmed."""
        return self.is_complete and not self.is_confirmed

    def __repr__(self):
        """Return FieldDoc(...) for debugging."""
        return f"FieldDoc({self.field_name!r}, dataset={self.dataset_id})"


class Enumeration(Base):
    """A decoding table on one machine, shared by every column linked to it."""
    # Per machine: Cobalt and PBS exit codes differ.

    __tablename__ = "enumerations"
    __table_args__ = (
        UniqueConstraint("machine_id", "name", name="one_enumeration_per_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Descriptions of the table's own code and meaning fields; None writes generic text.
    code_description: Mapped[str | None] = mapped_column(Text)
    meaning_description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    machine: Mapped["Machine"] = relationship(back_populates="enumerations")
    values: Mapped[list["EnumerationValue"]] = relationship(
        back_populates="enumeration", cascade="all, delete-orphan",
        order_by="EnumerationValue.position")
    field_docs: Mapped[list["FieldDoc"]] = relationship(back_populates="enumeration")

    def __repr__(self):
        """Return Enumeration(...) for debugging."""
        return f"Enumeration({self.name!r}, {len(self.values)} value(s))"


class EnumerationValue(Base):
    """One code and its meaning; a guess is flagged provisional."""

    __tablename__ = "enumeration_values"
    __table_args__ = (
        UniqueConstraint("enumeration_id", "code", name="one_row_per_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enumeration_id: Mapped[int] = mapped_column(
        ForeignKey("enumerations.id", ondelete="CASCADE"), nullable=False)
    # Text: codes are what appears in the column, not always numbers.
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    meaning: Mapped[str] = mapped_column(Text, nullable=False)
    is_provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    enumeration: Mapped["Enumeration"] = relationship(back_populates="values")

    def __repr__(self):
        """Return EnumerationValue(...) for debugging."""
        return f"EnumerationValue({self.code!r})"


class DatasetAnswer(Answer, Base):
    """A dataset-level answer (licence, citation, RAI blocks), keyed as in questions.py."""

    __tablename__ = "dataset_answers"
    __table_args__ = (
        UniqueConstraint("dataset_id", "key", name="one_answer_per_dataset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    # JSON: an answer is a string, list, boolean or object.
    answer: Mapped[object] = mapped_column(JSON, nullable=False)

    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    recorded_by: Mapped[str | None] = mapped_column(String(255))
    first_seen_in: Mapped[int | None] = mapped_column(
        ForeignKey("source_files.id", ondelete="SET NULL"))

    dataset: Mapped["Dataset"] = relationship(back_populates="answers")

    def __repr__(self):
        """Return DatasetAnswer(...) for debugging."""
        return f"DatasetAnswer({self.key!r}, dataset={self.dataset_id})"


class DerivedFile(Base):
    """A .txt or .md sibling of an imported Croissant; recorded, never read."""
    # Recorded because it goes stale once the Croissant is finished.

    __tablename__ = "derived_files"
    __table_args__ = (UniqueConstraint("file_id", "path", name="one_row_per_artifact"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), nullable=False)
    # VARCHAR, not TEXT: MySQL cannot index TEXT without a length.
    path: Mapped[str] = mapped_column(String(700), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)   # txt | md

    file: Mapped["SourceFile"] = relationship(back_populates="derived")

    def __repr__(self):
        """Return DerivedFile(...) for debugging."""
        return f"DerivedFile({self.format}, {self.path!r})"
