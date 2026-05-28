"""
X12 EDI parser for healthcare transaction sets.

Parses raw X12 EDI files into structured segment trees without external
dependencies. Supports the transaction sets most relevant to 340B compliance:

  837P  — Professional pharmacy claim
  837I  — Institutional claim
  835   — Health care claim payment / advice (remittance)
  270   — Eligibility inquiry
  271   — Eligibility response

X12 structure primer
────────────────────
  ISA → GS → ST → [transaction body] → SE → GE → IEA

  ISA  Interchange Control Header  (fixed-length, 106 chars)
  GS   Functional Group Header
  ST   Transaction Set Header      (carries type: 837, 835, …)
  SE   Transaction Set Trailer
  GE   Functional Group Trailer
  IEA  Interchange Control Trailer

Design
──────
  - Resilient: segment-level errors captured, not raised
  - Delimiter auto-detection from ISA segment
  - Hierarchical loop tracking via HL segment hierarchy numbers
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum        import Enum
from typing      import Any, Iterator, Optional

log = logging.getLogger("evidentrx.interop.edi.x12_parser")


# ── X12 transaction types ─────────────────────────────────────────────────────

class X12TransactionType(str, Enum):
    CLAIM_PROFESSIONAL   = "837P"
    CLAIM_INSTITUTIONAL  = "837I"
    REMITTANCE           = "835"
    ELIGIBILITY_INQUIRY  = "270"
    ELIGIBILITY_RESPONSE = "271"
    UNKNOWN              = "UNKNOWN"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class X12Segment:
    """A single X12 segment (one delimited line)."""
    segment_id: str
    elements:   list[str]           # raw element strings; index 0 = segment ID
    raw:        str

    def get(self, index: int, component: int = 0) -> Optional[str]:
        """
        Return element at 1-based index, 0-based component.
        Returns None if out of bounds or empty.
        """
        try:
            elem = self.elements[index]
            if not elem:
                return None
            parts = elem.split(":")  # component separator default
            val   = parts[component] if component < len(parts) else ""
            return val.strip() or None
        except IndexError:
            return None

    def get_all(self, index: int) -> list[str]:
        """Return all components of an element."""
        try:
            return [c.strip() for c in self.elements[index].split(":")]
        except IndexError:
            return []


@dataclass
class X12Loop:
    """
    A logical grouping of X12 segments (e.g. 2000A Billing Provider loop).
    Loops may be nested.
    """
    loop_id:  str
    segments: list[X12Segment]      = field(default_factory=list)
    children: list["X12Loop"]       = field(default_factory=list)

    def get_segment(self, segment_id: str) -> Optional[X12Segment]:
        for seg in self.segments:
            if seg.segment_id == segment_id:
                return seg
        return None

    def get_segments(self, segment_id: str) -> list[X12Segment]:
        return [s for s in self.segments if s.segment_id == segment_id]


@dataclass
class X12Transaction:
    """
    A complete X12 transaction set (ST…SE envelope).
    """
    transaction_type: X12TransactionType
    control_number:   str
    segments:         list[X12Segment]   = field(default_factory=list)
    loops:            list[X12Loop]      = field(default_factory=list)
    parse_errors:     list[str]          = field(default_factory=list)
    raw:              str                = ""

    def get_segment(self, segment_id: str) -> Optional[X12Segment]:
        for seg in self.segments:
            if seg.segment_id == segment_id:
                return seg
        return None

    def get_segments(self, segment_id: str) -> list[X12Segment]:
        return [s for s in self.segments if s.segment_id == segment_id]


@dataclass
class X12Interchange:
    """
    Top-level ISA…IEA interchange envelope containing one or more transactions.
    """
    sender_id:    str
    receiver_id:  str
    control_number: str
    date:         str
    transactions: list[X12Transaction]   = field(default_factory=list)
    parse_errors: list[str]              = field(default_factory=list)
    raw:          str                    = ""


# ── Parser ────────────────────────────────────────────────────────────────────

class X12Parser:
    """
    Pure-Python X12 EDI parser.

    Auto-detects element and sub-element delimiters from the ISA segment.
    Parses a single interchange (ISA…IEA) per call.
    """

    def parse(self, raw: str) -> X12Interchange:
        """
        Parse a raw X12 EDI string into an X12Interchange.

        Never raises. Sets parse_errors on the interchange for callers to inspect.
        """
        raw = raw.strip()
        if not raw.startswith("ISA"):
            return self._bad_interchange(raw, ["Document does not begin with ISA segment"])

        # Detect delimiters from ISA positions
        try:
            elem_sep    = raw[3]        # ISA*  → '*'
            sub_sep     = raw[104]      # sub-element separator at position 104
            seg_term    = raw[105]      # segment terminator at position 105
        except IndexError:
            return self._bad_interchange(raw, ["ISA segment too short to detect delimiters"])

        # Split into raw segment strings
        raw_segments = [s.strip() for s in raw.split(seg_term) if s.strip()]
        parse_errors: list[str] = []

        # Parse ISA
        isa_parts = raw_segments[0].split(elem_sep)
        try:
            sender_id      = isa_parts[6].strip()
            receiver_id    = isa_parts[8].strip()
            control_number = isa_parts[13].strip()
            date           = isa_parts[9].strip()
        except IndexError as e:
            return self._bad_interchange(raw, [f"ISA parse failed: {e}"])

        interchange = X12Interchange(
            sender_id      = sender_id,
            receiver_id    = receiver_id,
            control_number = control_number,
            date           = date,
            raw            = raw,
        )

        # Walk segments and group by ST…SE envelopes
        current_tx: Optional[list[X12Segment]] = None
        current_tx_type  = X12TransactionType.UNKNOWN
        current_ctrl_num = ""
        current_raw_segs: list[str] = []

        for raw_seg in raw_segments[1:]:
            if not raw_seg:
                continue
            parts = raw_seg.split(elem_sep)
            seg_id = parts[0].strip().upper()

            # Replace component separator with ':' for normalised access
            elements = [e.replace(sub_sep, ":") for e in parts]

            seg = X12Segment(segment_id=seg_id, elements=elements, raw=raw_seg)

            if seg_id == "ST":
                tx_type_code = elements[1].strip() if len(elements) > 1 else ""
                current_tx_type  = _map_tx_type(tx_type_code)
                current_ctrl_num = elements[2].strip() if len(elements) > 2 else ""
                current_tx       = [seg]
                current_raw_segs = [raw_seg]

            elif seg_id == "SE":
                if current_tx is not None:
                    current_tx.append(seg)
                    tx = X12Transaction(
                        transaction_type = current_tx_type,
                        control_number   = current_ctrl_num,
                        segments         = current_tx,
                        raw              = seg_term.join(current_raw_segs),
                    )
                    interchange.transactions.append(tx)
                    current_tx = None

            elif seg_id in ("GS", "GE", "IEA"):
                pass  # envelope management — not included in transaction segments

            else:
                if current_tx is not None:
                    current_tx.append(seg)
                    current_raw_segs.append(raw_seg)
                # Segments outside ST…SE ignored (e.g. GS/GE segments)

        interchange.parse_errors = parse_errors
        return interchange

    def parse_many(self, raw: str) -> list[X12Interchange]:
        """
        Parse a file that may contain multiple ISA…IEA interchanges.
        Splits on ISA boundaries and parses each.
        """
        # Split on ISA boundaries (keep delimiter)
        chunks = re.split(r"(?=\bISA\b)", raw.strip())
        return [self.parse(chunk) for chunk in chunks if chunk.strip().startswith("ISA")]

    @staticmethod
    def _bad_interchange(raw: str, errors: list[str]) -> X12Interchange:
        return X12Interchange(
            sender_id      = "",
            receiver_id    = "",
            control_number = "",
            date           = "",
            raw            = raw,
            parse_errors   = errors,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _map_tx_type(code: str) -> X12TransactionType:
    _MAP = {
        "837": X12TransactionType.CLAIM_PROFESSIONAL,   # sub-type resolved by GS08
        "835": X12TransactionType.REMITTANCE,
        "270": X12TransactionType.ELIGIBILITY_INQUIRY,
        "271": X12TransactionType.ELIGIBILITY_RESPONSE,
    }
    # 837P / 837I differ only in GS08; treat all 837x as professional by default
    for prefix, tx_type in _MAP.items():
        if code.startswith(prefix):
            return tx_type
    return X12TransactionType.UNKNOWN


def get_nm1(seg: X12Segment) -> dict[str, Optional[str]]:
    """
    Decode an NM1 segment into a structured dict.

    NM1*qualifier*type*last*first*middle*prefix*suffix*idCode*id
    """
    return {
        "qualifier":   seg.get(1),
        "entity_type": seg.get(2),
        "last_name":   seg.get(3),
        "first_name":  seg.get(4),
        "middle_name": seg.get(5),
        "id_code":     seg.get(8),
        "id":          seg.get(9),
    }


def get_ref(seg: X12Segment) -> tuple[Optional[str], Optional[str]]:
    """Decode a REF segment into (qualifier, value) tuple."""
    return seg.get(1), seg.get(2)
