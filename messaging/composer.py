"""
Message composition by template lookup and interpolation.

THERE IS NO RUNTIME TRANSLATION IN THIS MODULE OR ANYWHERE DOWNSTREAM.
Every message is a hand-written, pre-reviewed template from
config/templates.yaml, selected by key and interpolated with variables.

The whole library is validated at IMPORT TIME. If a template is missing a
language, is empty, is too long, or its variable set differs across
languages, importing this module raises and the process does not start.
That is deliberate: a missing Punjabi template must crash on boot rather
than silently send English to a Punjabi farmer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Set

import yaml

from messaging.models import Band, Language

logger = logging.getLogger(__name__)

TEMPLATES_PATH = Path(__file__).resolve().parent.parent / "config" / "templates.yaml"

MAX_TEMPLATE_CHARS = 350
REQUIRED_LANGUAGES = tuple(lang.value for lang in Language)

_VARIABLE_RE = re.compile(r"{(\w+)}")

# Keys the system must be able to resolve. Absence is a boot failure.
REQUIRED_KEYS = (
    "CRITICAL__DECLINE_LOW_RAIN_HIGH_EXTRACTION",
    "CRITICAL__DEFAULT",
    "WARNING__DECLINE_HIGH_EXTRACTION",
    "WARNING__DECLINE_LOW_RAIN",
    "WARNING__DEFAULT",
    "WATCH__DEFAULT",
    "NORMAL__RECOVERED",
)


class TemplateError(Exception):
    """Raised for any problem with the template library or a lookup."""


class TemplateValidationError(TemplateError):
    """Raised at import time when the template library is not shippable."""


@dataclass(frozen=True)
class ComposedMessage:
    """The result of a successful composition."""

    text: str
    template_key: str
    language: str
    band: str
    reason_code: str
    fell_back: bool

    @property
    def length(self) -> int:
        return len(self.text)


def template_variables(text: str) -> Set[str]:
    """Extract the {variable} names referenced by a template."""
    return set(_VARIABLE_RE.findall(text))


def _load_raw(path: Path = TEMPLATES_PATH) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        raise TemplateValidationError(f"Template library not found at {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or not data:
        raise TemplateValidationError(f"{path} did not parse to a non-empty mapping.")
    return data


def validate_library(data: Mapping[str, object], *, require_keys: bool = True) -> None:
    """
    Validate an entire template library. Raises TemplateValidationError listing
    EVERY problem found, not just the first, so one boot surfaces all of them.
    """
    problems = []

    for key, langs in data.items():
        if not isinstance(langs, dict):
            problems.append(f"{key}: expected a mapping of language -> text, got {type(langs).__name__}")
            continue

        # Every required language present and non-empty.
        missing = [lang for lang in REQUIRED_LANGUAGES if lang not in langs]
        if missing:
            problems.append(f"{key}: missing language(s) {missing}")

        for lang in REQUIRED_LANGUAGES:
            if lang not in langs:
                continue
            text = langs[lang]
            if not isinstance(text, str):
                problems.append(f"{key}.{lang}: expected a string, got {type(text).__name__}")
                continue
            if not text.strip():
                problems.append(f"{key}.{lang}: template is empty")
            if len(text) > MAX_TEMPLATE_CHARS:
                problems.append(
                    f"{key}.{lang}: {len(text)} characters exceeds the "
                    f"{MAX_TEMPLATE_CHARS} character limit"
                )

        # Variable sets must be identical across languages, else a farmer in
        # one language gets a detail the others do not.
        present = [l for l in REQUIRED_LANGUAGES if isinstance(langs.get(l), str)]
        if len(present) > 1:
            var_sets = {l: template_variables(langs[l]) for l in present}
            reference_lang = present[0]
            reference = var_sets[reference_lang]
            for lang in present[1:]:
                if var_sets[lang] != reference:
                    only_ref = sorted(reference - var_sets[lang])
                    only_other = sorted(var_sets[lang] - reference)
                    detail = []
                    if only_ref:
                        detail.append(f"missing from {lang}: {only_ref}")
                    if only_other:
                        detail.append(f"extra in {lang}: {only_other}")
                    problems.append(
                        f"{key}: variable mismatch between {reference_lang} and "
                        f"{lang} ({'; '.join(detail)})"
                    )

    if require_keys:
        for key in REQUIRED_KEYS:
            if key not in data:
                problems.append(f"required key absent from library: {key}")

    if problems:
        raise TemplateValidationError(
            "Template library is not shippable - "
            f"{len(problems)} problem(s) found:\n  - " + "\n  - ".join(problems)
        )


def load_templates(path: Path = TEMPLATES_PATH, *, require_keys: bool = True):
    """Load and fully validate a template library."""
    data = _load_raw(path)
    validate_library(data, require_keys=require_keys)
    return data


# --- Import-time validation. A broken library stops the process here. -------
TEMPLATES: Dict[str, Dict[str, str]] = load_templates()
logger.info(
    "Template library validated: %d keys x %d languages",
    len(TEMPLATES),
    len(REQUIRED_LANGUAGES),
)


def resolve_template_key(band: str, reason_code: Optional[str]) -> str:
    """
    Resolve BAND__REASON_CODE, falling back to BAND__DEFAULT.

    Raises TemplateError if neither exists, rather than substituting some
    other band's text.
    """
    band_norm = (band.value if isinstance(band, Band) else str(band)).upper()

    if reason_code:
        specific = f"{band_norm}__{str(reason_code).upper()}"
        if specific in TEMPLATES:
            return specific

    default = f"{band_norm}__DEFAULT"
    if default in TEMPLATES:
        return default

    attempted = f"{band_norm}__{str(reason_code).upper()}" if reason_code else "(no reason code)"
    raise TemplateError(
        f"No template for band {band_norm!r} with reason code {reason_code!r}. "
        f"Tried {attempted} then {default}. Available keys: {sorted(TEMPLATES)}"
    )


def compose(
    band: str,
    reason_code: Optional[str],
    language: str,
    variables: Mapping[str, object],
) -> ComposedMessage:
    """
    Compose a message for a band/reason/language from the template library.

    Args:
        band: CRITICAL / WARNING / WATCH / NORMAL
        reason_code: e.g. DECLINE_LOW_RAIN; falls back to BAND__DEFAULT
        language: en / hi / pa
        variables: values for the template's {placeholders}

    Raises:
        TemplateError: unknown band/reason, unknown language, or a missing
            variable. Never emits a literal "{village}".
    """
    lang = language.value if isinstance(language, Language) else str(language).lower()
    if lang not in REQUIRED_LANGUAGES:
        raise TemplateError(
            f"Unsupported language {language!r}. Supported: {list(REQUIRED_LANGUAGES)}"
        )

    key = resolve_template_key(band, reason_code)
    fell_back = key.endswith("__DEFAULT") and bool(reason_code) and not key.endswith(
        f"__{str(reason_code).upper()}"
    )

    text = TEMPLATES[key][lang]

    required = template_variables(text)
    supplied = {k: v for k, v in (variables or {}).items() if v is not None}
    missing = sorted(required - set(supplied))
    if missing:
        raise TemplateError(
            f"Cannot compose {key}/{lang}: missing required variable(s) {missing}. "
            f"Supplied: {sorted(supplied)}. Refusing to send a message containing "
            f"an un-substituted placeholder."
        )

    try:
        rendered = text.format(**supplied)
    except (KeyError, IndexError) as exc:
        raise TemplateError(f"Failed to interpolate {key}/{lang}: {exc}") from exc

    # Defensive: nothing that looks like a placeholder may survive.
    leftover = _VARIABLE_RE.findall(rendered)
    if leftover:
        raise TemplateError(
            f"{key}/{lang} still contains placeholders after interpolation: {leftover}"
        )

    return ComposedMessage(
        text=rendered,
        template_key=key,
        language=lang,
        band=(band.value if isinstance(band, Band) else str(band)).upper(),
        reason_code=(str(reason_code).upper() if reason_code else ""),
        fell_back=fell_back,
    )
