"""
Domain models for the advisory messaging layer.

Phone numbers are the sensitive field here. Three helpers exist for three
distinct purposes and they are not interchangeable:

    normalise_phone()  canonical E.164 for STORAGE and SENDING
    phone_hash()       SHA-256 prefix for LOGS - never reversible
    mask_phone()       partly redacted for OPERATOR DISPLAY

Raw phone numbers must never reach a log line. Use phone_hash().
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Language(str, Enum):
    """Supported advisory languages. Must match the keys in templates.yaml."""

    EN = "en"
    HI = "hi"
    PA = "pa"

    @property
    def label(self) -> str:
        return {"en": "English", "hi": "हिन्दी (Hindi)", "pa": "ਪੰਜਾਬੀ (Punjabi)"}[self.value]


class AlertStatus(str, Enum):
    """Lifecycle of a single advisory."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class Band(str, Enum):
    """Advisory severity band. Must match the BAND__ prefix in templates.yaml."""

    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    WATCH = "WATCH"
    NORMAL = "NORMAL"


# --------------------------------------------------------------------------
# Phone handling
# --------------------------------------------------------------------------

# Indian mobile numbers are 10 digits beginning 6-9.
_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")


class PhoneError(ValueError):
    """Raised when a phone number cannot be normalised to E.164."""


def normalise_phone(raw: str) -> str:
    """
    Normalise an Indian mobile number to E.164 (+91XXXXXXXXXX).

    Accepts:
        9876543210          bare 10-digit
        09876543210         leading trunk zero
        919876543210        country code, no plus
        +919876543210       already canonical
    Also tolerates spaces, hyphens, brackets and dots as separators.

    Raises:
        PhoneError: with a specific reason, on anything else.
    """
    if raw is None:
        raise PhoneError("Phone number is required.")

    # Strip common separators but keep a leading '+' so we can detect intent.
    cleaned = re.sub(r"[\s\-().]", "", str(raw).strip())
    if not cleaned:
        raise PhoneError("Phone number is required.")

    had_plus = cleaned.startswith("+")
    digits = cleaned[1:] if had_plus else cleaned

    if not digits.isdigit():
        raise PhoneError(
            f"Phone number must contain only digits (and optional +, spaces, "
            f"hyphens). Got: {raw!r}"
        )

    # Peel off the accepted prefixes to reach a bare 10-digit national number.
    if len(digits) == 10:
        national = digits
    elif len(digits) == 11 and digits.startswith("0"):
        if had_plus:
            raise PhoneError(
                f"'+0...' is not a valid format. Use +91XXXXXXXXXX. Got: {raw!r}"
            )
        national = digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        national = digits[2:]
    else:
        raise PhoneError(
            f"Expected a 10-digit Indian mobile number, optionally prefixed "
            f"with 0, 91 or +91. Got {len(digits)} digits: {raw!r}"
        )

    if not _INDIAN_MOBILE.match(national):
        raise PhoneError(
            f"Not a valid Indian mobile number - must be 10 digits starting "
            f"with 6, 7, 8 or 9. Got: {national!r}"
        )

    return f"+91{national}"


def phone_hash(phone: str, length: int = 12) -> str:
    """
    Stable, non-reversible identifier for a phone number, for use in logs.

    Normalises first so the same number always hashes identically regardless
    of the format it arrived in. Falls back to hashing the raw string if it
    cannot be normalised, so logging never itself raises.
    """
    try:
        canonical = normalise_phone(phone)
    except PhoneError:
        canonical = str(phone)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]


def mask_phone(phone: str) -> str:
    """
    Partly redact a phone number for operator display: +9198XXXXX210.

    Keeps the country code, the first two national digits and the last three,
    which is enough for an operator to recognise a number they already know
    without exposing it to anyone reading over their shoulder.
    """
    try:
        canonical = normalise_phone(phone)
    except PhoneError:
        return "+91XXXXXXXXXX"
    national = canonical[3:]  # strip '+91'
    return f"+91{national[:2]}XXXXX{national[-3:]}"


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------


@dataclass
class Farmer:
    """
    A registered farmer.

    Consent is not optional and not a settable preference. A farmer without
    consent is not a farmer with consent=False, they are simply not
    registered. __post_init__ enforces that so an unconsented record cannot
    exist in memory, let alone reach the database.
    """

    name: str
    phone: str
    village: str
    district: str
    language: Language = Language.EN
    consent: bool = False
    consent_ts: Optional[datetime] = None
    nearest_station_id: Optional[str] = None
    distance_km: Optional[float] = None
    registered_by: Optional[str] = None
    active: bool = True
    id: Optional[int] = None
    created_ts: Optional[datetime] = None

    def __post_init__(self):
        if not self.consent:
            raise ValueError(
                "Cannot create a Farmer without consent. Consent is captured at "
                "registration; an unconsented person is not registered at all. "
                "Do not construct Farmer(consent=False)."
            )

        if not str(self.name).strip():
            raise ValueError("Farmer name is required.")
        if not str(self.village).strip():
            raise ValueError("Village is required - advisories are village-scoped.")
        if not str(self.district).strip():
            raise ValueError("District is required.")

        # Normalise on construction so an unnormalised number cannot be stored.
        self.phone = normalise_phone(self.phone)
        self.name = str(self.name).strip()
        self.village = str(self.village).strip()
        self.district = str(self.district).strip()

        if not isinstance(self.language, Language):
            self.language = Language(str(self.language).lower())

        if self.consent_ts is None:
            self.consent_ts = datetime.now()

    @property
    def phone_masked(self) -> str:
        return mask_phone(self.phone)

    @property
    def phone_hashed(self) -> str:
        return phone_hash(self.phone)

    def __repr__(self) -> str:
        # Deliberately hash-only: repr lands in logs and tracebacks.
        return (
            f"Farmer(id={self.id}, name={self.name!r}, phone_hash={self.phone_hashed}, "
            f"village={self.village!r}, language={self.language.value}, "
            f"station={self.nearest_station_id})"
        )


@dataclass
class Alert:
    """A single advisory, sent or deliberately not sent."""

    farmer_id: int
    station_id: str
    band: str
    reason_code: str
    language: str
    template_key: str
    message_text: str
    status: AlertStatus = AlertStatus.PENDING
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    id: Optional[int] = None
    created_ts: Optional[datetime] = None
    sent_ts: Optional[datetime] = None

    def __post_init__(self):
        if isinstance(self.band, Band):
            self.band = self.band.value
        if isinstance(self.language, Language):
            self.language = self.language.value
        if not isinstance(self.status, AlertStatus):
            self.status = AlertStatus(str(self.status))


@dataclass
class Evaluation:
    """
    The analysis pipeline's verdict for one station - the input to dispatch.

    This is the boundary between the analysis pipeline and the messaging
    layer. Messaging never recomputes hydrology; it consumes this.
    """

    station_id: str
    band: str
    reason_code: str
    metrics: dict = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.band, Band):
            self.band = self.band.value
        self.band = str(self.band).upper()
        self.reason_code = str(self.reason_code).upper()
