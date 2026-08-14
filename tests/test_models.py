"""Phone normalisation, hashing, masking, and consent enforcement."""

from __future__ import annotations

import pytest

from messaging.models import (
    Alert,
    AlertStatus,
    Band,
    Evaluation,
    Farmer,
    Language,
    PhoneError,
    mask_phone,
    normalise_phone,
    phone_hash,
)

CANONICAL = "+919876543210"


class TestNormalisePhone:
    @pytest.mark.parametrize(
        "raw",
        [
            "9876543210",
            "09876543210",
            "919876543210",
            "+919876543210",
            "  9876543210  ",
            "98765-43210",
            "+91 98765 43210",
            "(0)9876543210",
            "+91-98765-43210",
            "987.654.3210",
        ],
    )
    def test_accepted_formats_all_normalise_identically(self, raw):
        assert normalise_phone(raw) == CANONICAL

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            None,
            "123",                 # too short
            "98765432101234",      # too long
            "5876543210",          # starts with 5 - not an Indian mobile
            "0876543210",          # starts with 0 after peel
            "1876543210",          # starts with 1
            "abcdefghij",          # not digits
            "+1234567890",         # wrong country
            "+09876543210",        # +0 is meaningless
            "9876 5432",           # too short after cleaning
            "++919876543210",      # malformed
        ],
    )
    def test_rejected_formats_raise_phone_error(self, raw):
        with pytest.raises(PhoneError):
            normalise_phone(raw)

    def test_error_message_is_specific(self):
        with pytest.raises(PhoneError, match="6, 7, 8 or 9"):
            normalise_phone("5876543210")
        with pytest.raises(PhoneError, match="digits"):
            normalise_phone("abcdefghij")

    @pytest.mark.parametrize("prefix", ["6", "7", "8", "9"])
    def test_all_valid_indian_prefixes_accepted(self, prefix):
        assert normalise_phone(f"{prefix}876543210") == f"+91{prefix}876543210"


class TestPhoneHash:
    def test_is_stable(self):
        assert phone_hash("9876543210") == phone_hash("9876543210")

    def test_same_across_input_formats(self):
        # The whole point: one farmer, one hash, whatever format was typed.
        variants = ["9876543210", "09876543210", "+919876543210", "919876543210"]
        assert len({phone_hash(v) for v in variants}) == 1

    def test_does_not_contain_the_number(self):
        h = phone_hash("9876543210")
        assert "9876543210" not in h
        assert "543210" not in h

    def test_different_numbers_differ(self):
        assert phone_hash("9876543210") != phone_hash("9876543211")

    def test_does_not_raise_on_garbage(self):
        # Logging must never be the thing that crashes a send.
        assert phone_hash("not-a-number")


class TestMaskPhone:
    def test_expected_shape(self):
        assert mask_phone("9876543210") == "+9198XXXXX210"

    def test_hides_middle_digits(self):
        masked = mask_phone("9876543210")
        assert "76543" not in masked

    def test_invalid_input_returns_placeholder(self):
        assert mask_phone("garbage") == "+91XXXXXXXXXX"


class TestFarmerConsent:
    def _kwargs(self, **over):
        base = dict(
            name="Balwinder Singh",
            phone="9876543210",
            village="Longowal",
            district="Sangrur",
            language=Language.PA,
            consent=True,
        )
        base.update(over)
        return base

    def test_consent_true_constructs(self):
        farmer = Farmer(**self._kwargs())
        assert farmer.consent is True
        assert farmer.consent_ts is not None

    def test_consent_false_raises(self):
        with pytest.raises(ValueError, match="without consent"):
            Farmer(**self._kwargs(consent=False))

    def test_consent_defaults_to_false_and_therefore_raises(self):
        kwargs = self._kwargs()
        kwargs.pop("consent")
        with pytest.raises(ValueError, match="without consent"):
            Farmer(**kwargs)

    def test_phone_normalised_on_construction(self):
        farmer = Farmer(**self._kwargs(phone="09876543210"))
        assert farmer.phone == CANONICAL

    def test_invalid_phone_raises(self):
        with pytest.raises(PhoneError):
            Farmer(**self._kwargs(phone="123"))

    @pytest.mark.parametrize("field", ["name", "village", "district"])
    def test_blank_required_fields_raise(self, field):
        with pytest.raises(ValueError):
            Farmer(**self._kwargs(**{field: "   "}))

    def test_repr_never_leaks_the_phone_number(self):
        farmer = Farmer(**self._kwargs())
        text = repr(farmer)
        assert "9876543210" not in text
        assert farmer.phone_hashed in text

    def test_language_coerced_from_string(self):
        farmer = Farmer(**self._kwargs(language="hi"))
        assert farmer.language is Language.HI


class TestEnumsAndEvaluation:
    def test_alert_status_values(self):
        assert {s.value for s in AlertStatus} == {
            "pending",
            "sent",
            "failed",
            "suppressed",
        }

    def test_language_values_match_template_languages(self):
        assert {l.value for l in Language} == {"en", "hi", "pa"}

    def test_alert_coerces_enum_inputs(self):
        alert = Alert(
            farmer_id=1,
            station_id="S1",
            band=Band.CRITICAL,
            reason_code="DEFAULT",
            language=Language.PA,
            template_key="CRITICAL__DEFAULT",
            message_text="x",
        )
        assert alert.band == "CRITICAL"
        assert alert.language == "pa"
        assert alert.status is AlertStatus.PENDING

    def test_evaluation_uppercases(self):
        ev = Evaluation(station_id="S1", band="critical", reason_code="default")
        assert ev.band == "CRITICAL"
        assert ev.reason_code == "DEFAULT"
