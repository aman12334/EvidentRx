"""
HL7 v2 message parser.

Parses raw HL7 v2 pipe-delimited messages into structured Python objects
without relying on external HL7 libraries (pure stdlib — no hl7apy/python-hl7
dependency required, though either can replace this for production).

Supported message types
───────────────────────
  ADT^A01 / A02 / A03 / A04 / A08   — patient admin events
  ORM^O01                            — pharmacy orders
  ORU^R01                            — observation results
  RDE^O11                            — pharmacy dispense events
  DFT^P03                            — detail financial transactions (claims)

Design
──────
  - Resilient: malformed segments are captured, not raised
  - Replayable: raw message bytes are always preserved
  - Dead-letter: unrecoverable messages routed to HL7DeadLetterQueue
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Optional

log = logging.getLogger("evidentrx.interop.hl7.parser")

# HL7 v2 delimiters (standard)
_FIELD_SEP  = "|"
_COMP_SEP   = "^"
_REPEAT_SEP = "~"
_ESCAPE     = "\\"
_SUBCOMP    = "&"


# ── Message types ─────────────────────────────────────────────────────────────

class HL7MessageType(str, Enum):
    ADT = "ADT"   # Admit / Discharge / Transfer
    ORM = "ORM"   # Order message
    ORU = "ORU"   # Observation result
    RDE = "RDE"   # Pharmacy / treatment encoded order
    DFT = "DFT"   # Detail financial transaction
    ACK = "ACK"   # Acknowledgement
    UNKNOWN = "UNKNOWN"


# ── Parsed structures ─────────────────────────────────────────────────────────

@dataclass
class HL7Segment:
    name:   str
    fields: list[str]               # raw field strings (index 0 = segment ID)
    raw:    str                     # original pipe-delimited line

    def get(self, index: int, component: int = 0) -> Optional[str]:
        """
        Return field value at 1-based index, 0-based component.
        Returns None if out of bounds or empty.
        """
        try:
            field_val = self.fields[index]
            if not field_val:
                return None
            parts = field_val.split(_COMP_SEP)
            val   = parts[component] if component < len(parts) else ""
            return val.strip() or None
        except IndexError:
            return None

    def get_all_components(self, index: int) -> list[str]:
        """Return all components of a field as a list."""
        try:
            return [c.strip() for c in self.fields[index].split(_COMP_SEP)]
        except IndexError:
            return []

    def get_repeating(self, index: int) -> list[str]:
        """Return all repetitions of a repeating field."""
        try:
            return [r.strip() for r in self.fields[index].split(_REPEAT_SEP) if r.strip()]
        except IndexError:
            return []


@dataclass
class HL7Message:
    message_type:    HL7MessageType
    trigger_event:   str                        # e.g. "A01", "O01"
    message_id:      str
    sending_facility:str
    receiving_facility: str
    timestamp:       Optional[datetime]
    version:         str
    segments:        list[HL7Segment]           = field(default_factory=list)
    raw:             str                        = ""
    parse_errors:    list[str]                  = field(default_factory=list)
    is_valid:        bool                       = True

    def get_segment(self, name: str) -> Optional[HL7Segment]:
        """Return first segment with the given name."""
        for seg in self.segments:
            if seg.name == name:
                return seg
        return None

    def get_segments(self, name: str) -> list[HL7Segment]:
        """Return all segments with the given name."""
        return [s for s in self.segments if s.name == name]


# ── Parser ────────────────────────────────────────────────────────────────────

class HL7Parser:
    """
    Pure-Python HL7 v2.x parser.

    Parses one complete HL7 message (MSH through final segment).
    The message must use standard delimiters (|^~\\&).
    """

    def parse(self, raw_message: str) -> HL7Message:
        """
        Parse a raw HL7 message string.

        On parse errors: sets is_valid=False and records errors in
        parse_errors. Never raises — callers check is_valid and route
        invalid messages to the dead-letter queue.
        """
        raw = raw_message.strip().replace("\r\n", "\r").replace("\n", "\r")
        lines = [l for l in raw.split("\r") if l.strip()]

        if not lines:
            return _invalid_message(raw, ["Empty message"])

        # ── Parse MSH ────────────────────────────────────────────────────────
        msh_line = lines[0]
        if not msh_line.startswith("MSH"):
            return _invalid_message(raw, [f"First segment must be MSH, got: {msh_line[:20]!r}"])

        msh_fields = msh_line.split(_FIELD_SEP)
        parse_errors: list[str] = []

        try:
            msg_type_raw   = msh_fields[8] if len(msh_fields) > 8 else ""
            type_parts     = msg_type_raw.split(_COMP_SEP)
            msg_type_str   = type_parts[0].upper()
            trigger_event  = type_parts[1].upper() if len(type_parts) > 1 else ""

            try:
                msg_type = HL7MessageType(msg_type_str)
            except ValueError:
                msg_type = HL7MessageType.UNKNOWN
                parse_errors.append(f"Unknown message type: {msg_type_str!r}")

            message_id        = msh_fields[9]  if len(msh_fields) > 9  else ""
            version           = msh_fields[11] if len(msh_fields) > 11 else "2.5"
            sending_facility  = _comp(msh_fields[3] if len(msh_fields) > 3 else "", 0)
            receiving_facility= _comp(msh_fields[5] if len(msh_fields) > 5 else "", 0)
            timestamp_str     = msh_fields[6]  if len(msh_fields) > 6  else ""
            timestamp         = _parse_hl7_timestamp(timestamp_str)

        except Exception as e:
            return _invalid_message(raw, [f"MSH parse failed: {e}"])

        # ── Parse all segments ────────────────────────────────────────────────
        segments: list[HL7Segment] = []
        msh_seg = HL7Segment(name="MSH", fields=msh_fields, raw=msh_line)
        segments.append(msh_seg)

        for line in lines[1:]:
            if not line.strip():
                continue
            parts = line.split(_FIELD_SEP)
            seg_name = parts[0].strip()
            if not re.match(r"^[A-Z]{2,3}$", seg_name):
                parse_errors.append(f"Invalid segment ID: {seg_name!r}")
                continue
            segments.append(HL7Segment(name=seg_name, fields=parts, raw=line))

        return HL7Message(
            message_type      = msg_type,
            trigger_event     = trigger_event,
            message_id        = message_id,
            sending_facility  = sending_facility,
            receiving_facility= receiving_facility,
            timestamp         = timestamp,
            version           = version,
            segments          = segments,
            raw               = raw,
            parse_errors      = parse_errors,
            is_valid          = len(parse_errors) == 0,
        )

    def parse_batch(self, messages: list[str]) -> list[HL7Message]:
        """Parse multiple messages. Errors are captured per-message."""
        return [self.parse(m) for m in messages]


# ── Convenience extractors ────────────────────────────────────────────────────

def extract_patient_id(msg: HL7Message) -> Optional[str]:
    """Extract the patient MRN from PID-3."""
    pid = msg.get_segment("PID")
    if not pid:
        return None
    # PID-3 is a repeating field; take first CX component (ID value)
    for repeat in pid.get_repeating(3):
        parts = repeat.split(_COMP_SEP)
        if parts[0]:
            return parts[0].strip()
    return None


def extract_ndc(msg: HL7Message) -> Optional[str]:
    """Extract NDC from RXE-2 (encoded order) or RXD-2 (dispense)."""
    for seg_name in ("RXE", "RXD", "RXO"):
        seg = msg.get_segment(seg_name)
        if seg:
            code_cc = seg.get(2)
            if code_cc:
                parts = code_cc.split(_COMP_SEP)
                if len(parts) >= 3 and parts[2].upper() == "NDC":
                    return parts[0].strip()
    return None


def extract_npi(msg: HL7Message) -> Optional[str]:
    """Extract ordering provider NPI from ORC-12."""
    orc = msg.get_segment("ORC")
    if not orc:
        return None
    # ORC-12: ordering provider XCN; NPI is component 1
    provider_field = orc.get(12)
    if provider_field:
        parts = provider_field.split(_COMP_SEP)
        if len(parts) >= 9 and parts[8].upper() == "NPI":
            return parts[0].strip()
    return None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _comp(field: str, index: int) -> str:
    parts = field.split(_COMP_SEP)
    return parts[index].strip() if index < len(parts) else ""


def _parse_hl7_timestamp(ts: str) -> Optional[datetime]:
    """Parse HL7 DTM format: YYYYMMDDHHMMSS[.SSSS][+/-ZZZZ]."""
    ts = ts.strip()[:14]  # drop timezone, microseconds for simplicity
    formats = ["%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"]
    for fmt in formats:
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _invalid_message(raw: str, errors: list[str]) -> HL7Message:
    return HL7Message(
        message_type      = HL7MessageType.UNKNOWN,
        trigger_event     = "",
        message_id        = "",
        sending_facility  = "",
        receiving_facility= "",
        timestamp         = None,
        version           = "2.5",
        raw               = raw,
        parse_errors      = errors,
        is_valid          = False,
    )
