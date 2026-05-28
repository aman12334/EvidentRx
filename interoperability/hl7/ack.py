"""
HL7 v2 acknowledgement (ACK) generator.

Produces AA / AE / AR acknowledgements conforming to HL7 v2.5 §2.9.

ACK types
─────────
  AA  Application Accept   — message accepted and processed
  AE  Application Error    — message accepted but contained errors
  AR  Application Reject   — message rejected (structural failure)

Usage
─────
  from interoperability.hl7.ack import AckGenerator, AckCode

  ack_gen = AckGenerator(sending_application="EVIDENTRX", sending_facility="HQ")
  ack_msg = ack_gen.accept(original_message)
  raw_ack = ack_gen.error(original_message, "Duplicate message ID")
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from interoperability.hl7.parser import HL7Message


class AckCode(str, Enum):
    AA = "AA"   # Application Accept
    AE = "AE"   # Application Error
    AR = "AR"   # Application Reject


class AckGenerator:
    """
    Generates HL7 v2 ACK messages.

    Produces well-formed MSH + MSA segments with correct field ordering.
    Output uses standard HL7 delimiters and CR segment terminators.
    """

    def __init__(
        self,
        sending_application: str = "EVIDENTRX",
        sending_facility:    str = "HQ",
        version:             str = "2.5",
    ) -> None:
        self.sending_application = sending_application
        self.sending_facility    = sending_facility
        self.version             = version

    # ── Public API ─────────────────────────────────────────────────────────────

    def accept(self, original: HL7Message) -> str:
        """Generate AA (Application Accept) acknowledgement."""
        return self._build(original, AckCode.AA)

    def error(self, original: HL7Message, error_detail: str = "") -> str:
        """Generate AE (Application Error) acknowledgement."""
        return self._build(original, AckCode.AE, error_detail)

    def reject(self, original: HL7Message, reject_reason: str = "") -> str:
        """Generate AR (Application Reject) acknowledgement."""
        return self._build(original, AckCode.AR, reject_reason)

    def from_parse_errors(self, original: HL7Message) -> str:
        """
        Derive the appropriate ACK type from a parsed message's error list.

        - No errors          → AA
        - Soft errors        → AE  (message accepted, but had non-fatal issues)
        - is_valid=False     → AR  (structural failure)
        """
        if not original.parse_errors:
            return self.accept(original)
        if original.is_valid:
            return self.error(original, "; ".join(original.parse_errors[:3]))
        return self.reject(original, original.parse_errors[0] if original.parse_errors else "Malformed message")

    # ── Internal builder ────────────────────────────────────────────────────────

    def _build(
        self,
        original:     HL7Message,
        ack_code:     AckCode,
        text:         str = "",
    ) -> str:
        ts        = _hl7_now()
        ack_id    = _ack_message_id()
        orig_id   = original.message_id or ""
        orig_app  = original.sending_facility or "UNKNOWN"
        orig_fac  = original.receiving_facility or "UNKNOWN"

        msh = _build_msh(
            sending_app      = self.sending_application,
            sending_fac      = self.sending_facility,
            receiving_app    = orig_app,
            receiving_fac    = orig_fac,
            timestamp        = ts,
            message_id       = ack_id,
            version          = self.version,
        )

        msa = _build_msa(
            ack_code = ack_code,
            message_control_id = orig_id,
            text     = text,
        )

        return "\r".join([msh, msa]) + "\r"


# ── Segment builders ──────────────────────────────────────────────────────────

def _build_msh(
    sending_app:   str,
    sending_fac:   str,
    receiving_app: str,
    receiving_fac: str,
    timestamp:     str,
    message_id:    str,
    version:       str,
) -> str:
    """
    MSH segment for ACK.

    MSH|^~\&|SendApp|SendFac|RecApp|RecFac|TS||ACK|MsgID|P|Ver
    Fields are 1-based; MSH-1 = field sep, MSH-2 = encoding chars.
    """
    fields = [
        "MSH",
        "^~\\&",          # MSH-2 encoding characters
        sending_app,      # MSH-3 sending application
        sending_fac,      # MSH-4 sending facility
        receiving_app,    # MSH-5 receiving application
        receiving_fac,    # MSH-6 receiving facility
        timestamp,        # MSH-7 date/time of message
        "",               # MSH-8 security (empty)
        "ACK",            # MSH-9 message type
        message_id,       # MSH-10 message control ID
        "P",              # MSH-11 processing ID (P = production)
        version,          # MSH-12 version ID
    ]
    return "|".join(fields)


def _build_msa(
    ack_code:           AckCode,
    message_control_id: str,
    text:               str = "",
) -> str:
    """
    MSA segment.

    MSA|AA|OrigMsgID|[text]
    """
    fields = [
        "MSA",
        ack_code.value,
        message_control_id,
        text[:200] if text else "",     # MSA-3: text message (truncated to 200 chars)
    ]
    return "|".join(fields)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hl7_now() -> str:
    """Return current UTC time in HL7 DTM format: YYYYMMDDHHMMSS."""
    return datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")


def _ack_message_id() -> str:
    """Generate a unique ACK message control ID."""
    return f"ACK{uuid.uuid4().hex[:12].upper()}"
