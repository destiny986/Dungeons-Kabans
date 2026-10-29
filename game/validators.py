"""Shared input validation for models, forms and importers."""
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from .constants import ABILITY_KEYS, SKILL_KEYS

validate_digest = RegexValidator(r"\A[0-9a-f]{64}\Z", "Expected a lowercase SHA-256 digest.")

def normalize_name(value):
    return unicodedata.normalize("NFC", value).strip().casefold()

def normalize_search(value):
    return " ".join(normalize_name(value).split())

def validate_signed_mapping(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys) or any(type(v) is not int for v in value.values()):
        raise ValidationError("Provide exactly the required keys with signed integer values.")

def validate_abilities(value):
    validate_signed_mapping(value, ABILITY_KEYS)

def validate_skills(value):
    validate_signed_mapping(value, SKILL_KEYS)

def validate_weapons(value):
    if not isinstance(value, list) or len(value) != 5:
        raise ValidationError("Provide exactly five weapon rows.")
    for row in value:
        if not isinstance(row, dict) or set(row) != {"name", "attack", "damage"}:
            raise ValidationError("Each weapon needs name, attack and damage.")
        if any(not isinstance(v, str) or len(v) > 30 for v in row.values()):
            raise ValidationError("Weapon values must be text of at most 30 characters.")
        filled = [bool(v.strip()) for v in row.values()]
        if any(filled) and not all(filled):
            raise ValidationError("Fill all weapon fields or leave the entire row empty.")

def validate_tags(value):
    if not isinstance(value, list) or any(not isinstance(tag, str) for tag in value):
        raise ValidationError("Tags must be a list of strings.")

def validate_json_dict(value):
    if not isinstance(value, dict):
        raise ValidationError("Expected a JSON object.")

def validate_relative_path(value):
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if not value or posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts or ".." in windows.parts:
        raise ValidationError("Expected a relative path without parent traversal.")

def default_abilities():
    return dict.fromkeys(ABILITY_KEYS, 0)

def default_skills():
    return dict.fromkeys(SKILL_KEYS, 0)

def default_weapons():
    return [{"name": "", "attack": "", "damage": ""} for _ in range(5)]
