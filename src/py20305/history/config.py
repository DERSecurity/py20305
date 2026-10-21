"""Configuration for the operations history.

Each part is on by default and can be switched off on its own, and the whole
history can be switched off with one setting. A deployment that has no use for
a history, or keeps its own elsewhere, sets ``history.enabled: false`` and the
client writes no file.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Strict(BaseModel):
    """Unknown keys are an error, as everywhere else in the configuration."""

    model_config = ConfigDict(extra="forbid")


class HistoryPartConfig(_Strict):
    """One part of the history."""

    enabled: bool = Field(default=True, description="Keep this part of the history.")


class IntervalHistoryConfig(HistoryPartConfig):
    """The measured-interval part of the history."""

    interval_seconds: int = Field(
        default=900,
        description=(
            "Length of each interval. The default, 15 minutes, matches common "
            "settlement intervals. Must divide a day evenly."
        ),
    )
    max_gap_seconds: float = Field(
        default=900.0,
        gt=0,
        description=(
            "Longest gap between two readings of a device that is bridged by holding "
            "the earlier reading. A longer gap counts as unmeasured. Keep it above the "
            "telemetry post rate."
        ),
    )

    @field_validator("interval_seconds")
    @classmethod
    def _divides_a_day(cls, v: int) -> int:
        """Reject an interval that would make days start at different offsets."""
        if v < 60 or 86400 % v:
            raise ValueError("must be at least 60 and divide 86400 evenly")
        return v


class HistoryConfig(_Strict):
    """The operations history: events, device writes and measured intervals."""

    enabled: bool = Field(
        default=True,
        description="Keep an operations history. Off writes no history file at all.",
    )
    path: Path = Field(
        default=Path("py20305-history.sqlite3"),
        description=(
            "SQLite file the history is kept in. A relative path is relative to the "
            "working directory. In a container, put it on a mounted volume."
        ),
    )
    retention_days: float = Field(
        default=400.0,
        gt=0,
        description="How long rows are kept. The default covers a year and a season.",
    )
    events: HistoryPartConfig = Field(
        default_factory=HistoryPartConfig,
        description="Event lifecycle records and the responses posted for each event.",
    )
    writes: HistoryPartConfig = Field(
        default_factory=HistoryPartConfig,
        description="Every control written to a device, with the mRIDs that caused it.",
    )
    intervals: IntervalHistoryConfig = Field(
        default_factory=IntervalHistoryConfig,
        description="Device readings integrated into fixed intervals. Needs telemetry on.",
    )
    site_intervals: HistoryPartConfig = Field(
        default_factory=HistoryPartConfig,
        description=(
            "Site meter readings integrated into fixed intervals, kept apart from the "
            "devices, import positive. Kept only while `intervals` is on, with its "
            "interval length and gap. Needs a device with role site_meter."
        ),
    )

    @property
    def any_part_enabled(self) -> bool:
        """Whether this configuration keeps anything at all."""
        return self.enabled and (
            self.events.enabled or self.writes.enabled or self.intervals.enabled
        )
