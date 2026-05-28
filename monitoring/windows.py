"""
Rolling window definitions for compliance monitoring.

Windows are the unit of analysis: each monitoring run operates over
one or more windows and computes trends, scores, and correlations
within those date boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class RollingWindow:
    name:        str          # e.g. "30d"
    days:        int
    label:       str          # human-readable
    is_primary:  bool = False # primary window drives the monitoring run cadence


# Canonical window definitions
WINDOW_30D = RollingWindow(name="30d",  days=30,  label="30-Day Rolling",   is_primary=True)
WINDOW_60D = RollingWindow(name="60d",  days=60,  label="60-Day Rolling")
WINDOW_90D = RollingWindow(name="90d",  days=90,  label="90-Day Rolling")

ALL_WINDOWS: tuple[RollingWindow, ...] = (WINDOW_30D, WINDOW_60D, WINDOW_90D)
PRIMARY_WINDOW = WINDOW_30D


@dataclass
class WindowBounds:
    """Resolved start/end dates for a rolling window."""
    window:   RollingWindow
    as_of:    date
    start:    date
    end:      date           # = as_of

    def prior_start(self) -> date:
        """Start of the immediately preceding window (for delta computation)."""
        return self.start - timedelta(days=self.window.days)

    def label(self) -> str:
        return f"{self.window.label}: {self.start} → {self.end}"


def resolve_windows(
    as_of: date | None = None,
    windows: tuple[RollingWindow, ...] = ALL_WINDOWS,
) -> list[WindowBounds]:
    """
    Resolves all rolling windows to concrete date bounds relative to as_of.
    """
    as_of = as_of or date.today()
    return [
        WindowBounds(
            window=w,
            as_of=as_of,
            start=as_of - timedelta(days=w.days),
            end=as_of,
        )
        for w in windows
    ]
