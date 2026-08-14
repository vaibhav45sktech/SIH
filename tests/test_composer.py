"""
Template library validation and composition.

The validation tests are the important ones: they prove a deliberately broken
template library is REJECTED at load time rather than shipping a message with
a literal {village} in it, or English text to a Punjabi farmer.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from messaging.composer import (
    MAX_TEMPLATE_CHARS,
    REQUIRED_KEYS,
    REQUIRED_LANGUAGES,
    TEMPLATES,
    ComposedMessage,
    TemplateError,
    TemplateValidationError,
    compose,
    load_templates,
    resolve_template_key,
    template_variables,
    validate_library,
)

VARS = {"village": "Longowal", "district": "Sangrur", "decline_m_per_year": "0.58"}

BANDS = ["CRITICAL", "WARNING", "WATCH", "NORMAL"]


def write_yaml(tmp_path, data: dict):
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def good_entry(text_en="Hello {village}", text_hi="नमस्ते {village}", text_pa="ਸਤ ਸ੍ਰੀ ਅਕਾਲ {village}"):
    return {"en": text_en, "hi": text_hi, "pa": text_pa}


class TestShippedLibrary:
    def test_all_required_keys_present(self):
        for key in REQUIRED_KEYS:
            assert key in TEMPLATES, f"missing required key {key}"

    def test_every_key_has_all_three_languages(self):
        for key, langs in TEMPLATES.items():
            for lang in REQUIRED_LANGUAGES:
                assert lang in langs, f"{key} missing {lang}"
                assert langs[lang].strip(), f"{key}.{lang} is empty"

    def test_every_template_within_length_limit(self):
        for key, langs in TEMPLATES.items():
            for lang, text in langs.items():
                assert len(text) <= MAX_TEMPLATE_CHARS, (
                    f"{key}.{lang} is {len(text)} chars"
                )

    def test_variable_sets_identical_across_languages(self):
        for key, langs in TEMPLATES.items():
            sets = {l: template_variables(langs[l]) for l in REQUIRED_LANGUAGES}
            assert len({frozenset(s) for s in sets.values()}) == 1, (
                f"{key} variable mismatch: {sets}"
            )

    def test_no_template_contains_agronomic_advice(self):
        """
        The safety rule is water use only. Guard against a future edit that
        adds crop, sowing or fertiliser advice.
        """
        forbidden = [
            "sow", "sowing", "crop", "variety", "fertiliser", "fertilizer",
            "urea", "dap", "pesticide", "seed", "paddy", "wheat",
            "बोाई", "बोआई", "फसल", "खाद", "यूरिया", "बीज",
            "ਫ਼ਸਲ", "ਫਸਲ", "ਖਾਦ", "ਬੀਜ", "ਬਿਜਾਈ",
        ]
        for key, langs in TEMPLATES.items():
            for lang, text in langs.items():
                lowered = text.lower()
                for word in forbidden:
                    assert word.lower() not in lowered, (
                        f"{key}.{lang} contains agronomic term {word!r}; "
                        f"templates are restricted to water-use advice"
                    )

    def test_no_replacement_characters(self):
        # Catches mojibake introduced by a bad editor round-trip.
        for key, langs in TEMPLATES.items():
            for lang, text in langs.items():
                assert "�" not in text, f"{key}.{lang} contains U+FFFD"


class TestLibraryValidationCatchesBreakage:
    def test_missing_punjabi_is_rejected(self, tmp_path):
        """A missing Punjabi template must crash, not fall back to English."""
        data = {"WATCH__DEFAULT": {"en": "hi {village}", "hi": "नमस्ते {village}"}}
        path = write_yaml(tmp_path, data)
        with pytest.raises(TemplateValidationError, match="missing language"):
            load_templates(path, require_keys=False)

    def test_missing_hindi_is_rejected(self, tmp_path):
        data = {"WATCH__DEFAULT": {"en": "hi {village}", "pa": "ਸਤਿ {village}"}}
        with pytest.raises(TemplateValidationError, match="missing language"):
            load_templates(write_yaml(tmp_path, data), require_keys=False)

    def test_empty_template_is_rejected(self, tmp_path):
        data = {"WATCH__DEFAULT": {"en": "hi", "hi": "नमस्ते", "pa": "   "}}
        with pytest.raises(TemplateValidationError, match="empty"):
            load_templates(write_yaml(tmp_path, data), require_keys=False)

    def test_overlong_template_is_rejected(self, tmp_path):
        data = {"WATCH__DEFAULT": good_entry(text_pa="ਕ" * (MAX_TEMPLATE_CHARS + 1))}
        with pytest.raises(TemplateValidationError, match="exceeds"):
            load_templates(write_yaml(tmp_path, data), require_keys=False)

    def test_variable_mismatch_is_rejected(self, tmp_path):
        """Punjabi lacking {village} would give that farmer a vaguer message."""
        data = {
            "WATCH__DEFAULT": {
                "en": "Water notice for {village}, {district}",
                "hi": "{village}, {district} के लिए सूचना",
                "pa": "{district} ਲਈ ਸੂਚਨਾ",  # {village} missing
            }
        }
        with pytest.raises(TemplateValidationError) as exc:
            load_templates(write_yaml(tmp_path, data), require_keys=False)
        assert "variable mismatch" in str(exc.value)
        assert "village" in str(exc.value)

    def test_extra_variable_in_one_language_is_rejected(self, tmp_path):
        data = {
            "WATCH__DEFAULT": {
                "en": "Notice for {village}",
                "hi": "{village} सूचना",
                "pa": "{village} {district} ਸੂਚਨਾ",
            }
        }
        with pytest.raises(TemplateValidationError, match="variable mismatch"):
            load_templates(write_yaml(tmp_path, data), require_keys=False)

    def test_missing_required_key_is_rejected(self, tmp_path):
        data = {"WATCH__DEFAULT": good_entry()}
        with pytest.raises(TemplateValidationError, match="required key absent"):
            load_templates(write_yaml(tmp_path, data), require_keys=True)

    def test_all_problems_reported_at_once(self, tmp_path):
        """One boot should surface every problem, not just the first."""
        data = {
            "WATCH__DEFAULT": {"en": "a", "hi": "b"},          # missing pa
            "WARNING__DEFAULT": {"en": "", "hi": "b", "pa": "c"},  # empty en
        }
        with pytest.raises(TemplateValidationError) as exc:
            load_templates(write_yaml(tmp_path, data), require_keys=False)
        message = str(exc.value)
        assert "missing language" in message
        assert "empty" in message

    def test_non_mapping_entry_is_rejected(self):
        with pytest.raises(TemplateValidationError, match="expected a mapping"):
            validate_library({"WATCH__DEFAULT": "just a string"}, require_keys=False)

    def test_missing_file_is_rejected(self, tmp_path):
        with pytest.raises(TemplateValidationError, match="not found"):
            load_templates(tmp_path / "does_not_exist.yaml")


class TestKeyResolution:
    def test_specific_key_preferred(self):
        assert (
            resolve_template_key("WARNING", "DECLINE_LOW_RAIN")
            == "WARNING__DECLINE_LOW_RAIN"
        )

    def test_falls_back_to_band_default(self):
        assert resolve_template_key("WARNING", "NO_SUCH_REASON") == "WARNING__DEFAULT"

    def test_raises_when_neither_exists(self):
        with pytest.raises(TemplateError, match="No template for band"):
            resolve_template_key("NONSENSE_BAND", "DEFAULT")

    def test_normal_has_no_default_so_unknown_reason_raises(self):
        # Documented behaviour: NORMAL only ships RECOVERED, and the trigger
        # only fires NORMAL on a transition, which IS a recovery.
        with pytest.raises(TemplateError):
            resolve_template_key("NORMAL", "SOMETHING_ELSE")

    def test_case_insensitive(self):
        assert resolve_template_key("warning", "decline_low_rain") == (
            "WARNING__DECLINE_LOW_RAIN"
        )


class TestCompose:
    @pytest.mark.parametrize("lang", REQUIRED_LANGUAGES)
    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_every_key_composes_in_every_language(self, key, lang):
        band, _, reason = key.partition("__")
        msg = compose(band, reason, lang, VARS)
        assert isinstance(msg, ComposedMessage)
        assert msg.text
        assert msg.language == lang
        assert "{" not in msg.text and "}" not in msg.text
        assert len(msg.text) <= MAX_TEMPLATE_CHARS

    @pytest.mark.parametrize("band", BANDS)
    @pytest.mark.parametrize("lang", REQUIRED_LANGUAGES)
    def test_all_four_bands_compose_in_all_three_languages(self, band, lang):
        reason = "RECOVERED" if band == "NORMAL" else "DEFAULT"
        msg = compose(band, reason, lang, VARS)
        assert msg.band == band
        assert msg.language == lang
        assert msg.text

    def test_variables_are_substituted(self):
        msg = compose("CRITICAL", "DEFAULT", "en", VARS)
        assert "Longowal" in msg.text
        assert "Sangrur" in msg.text
        assert "0.58" in msg.text

    def test_missing_variable_raises(self):
        with pytest.raises(TemplateError, match="missing required variable"):
            compose("CRITICAL", "DEFAULT", "en", {"village": "Longowal"})

    def test_missing_variable_names_the_variable(self):
        with pytest.raises(TemplateError, match="district"):
            compose("CRITICAL", "DEFAULT", "en", {"village": "X", "decline_m_per_year": "1"})

    def test_none_valued_variable_treated_as_missing(self):
        with pytest.raises(TemplateError, match="missing required variable"):
            compose(
                "CRITICAL",
                "DEFAULT",
                "en",
                {"village": "X", "district": None, "decline_m_per_year": "1"},
            )

    def test_never_emits_a_literal_placeholder(self):
        for key in REQUIRED_KEYS:
            band, _, reason = key.partition("__")
            for lang in REQUIRED_LANGUAGES:
                text = compose(band, reason, lang, VARS).text
                assert "{village}" not in text
                assert "{district}" not in text
                assert "{decline_m_per_year}" not in text

    def test_unsupported_language_raises(self):
        with pytest.raises(TemplateError, match="Unsupported language"):
            compose("CRITICAL", "DEFAULT", "ta", VARS)

    def test_normal_recovered_needs_no_decline_variable(self):
        msg = compose("NORMAL", "RECOVERED", "pa", {"village": "X", "district": "Y"})
        assert msg.text

    def test_extra_variables_are_harmless(self):
        msg = compose("WATCH", "DEFAULT", "hi", {**VARS, "unused": "ignored"})
        assert msg.text

    def test_languages_produce_different_text(self):
        texts = {l: compose("CRITICAL", "DEFAULT", l, VARS).text for l in REQUIRED_LANGUAGES}
        assert len(set(texts.values())) == 3

    def test_punjabi_is_gurmukhi_script(self):
        text = compose("CRITICAL", "DEFAULT", "pa", VARS).text
        # Gurmukhi occupies U+0A00-U+0A7F.
        assert any("਀" <= ch <= "੿" for ch in text)

    def test_hindi_is_devanagari_script(self):
        text = compose("CRITICAL", "DEFAULT", "hi", VARS).text
        # Devanagari occupies U+0900-U+097F.
        assert any("ऀ" <= ch <= "ॿ" for ch in text)

    def test_fallback_flag_set_when_falling_back(self):
        msg = compose("WARNING", "TOTALLY_UNKNOWN", "en", VARS)
        assert msg.template_key == "WARNING__DEFAULT"
        assert msg.fell_back is True

    def test_fallback_flag_clear_on_exact_match(self):
        msg = compose("WARNING", "DECLINE_LOW_RAIN", "en", VARS)
        assert msg.fell_back is False
