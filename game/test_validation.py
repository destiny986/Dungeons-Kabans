import pytest
from django.core.exceptions import ValidationError
from game.validators import validate_abilities, validate_skills, validate_weapons


@pytest.mark.parametrize('value', [{}, {'str': True}, {'str': '2'}, [], None])
def test_ability_shape_rejects_missing_or_noninteger_values(value):
    with pytest.raises(ValidationError):
        validate_abilities(value)


def test_signed_abilities_are_accepted():
    validate_abilities(dict(str=-2, dex=0, con=1, int=2, wis=3, cha=4))


def test_skills_require_all_eighteen_integer_values():
    with pytest.raises(ValidationError):
        validate_skills({'acrobatics': -1})


@pytest.mark.parametrize('value', [[], [{}] * 5, [{'name': 'Sword', 'attack': '', 'damage': ''}] * 5])
def test_weapons_require_five_complete_or_empty_rows(value):
    with pytest.raises(ValidationError):
        validate_weapons(value)


@pytest.mark.parametrize("invalid", [True, False, "2", 1.5, None, [], {}])
def test_complete_ability_mapping_rejects_noninteger_values(invalid):
    values = dict(str=-2, dex=0, con=1, int=2, wis=3, cha=invalid)
    with pytest.raises(ValidationError):
        validate_abilities(values)


def test_complete_signed_skills_and_empty_weapons_are_valid():
    validate_skills({"acrobatics": -1, "animal_handling": 0, "arcana": 1, "athletics": 2, "deception": 3, "history": 4, "insight": 5, "intimidation": 6, "investigation": 7, "medicine": 8, "nature": 9, "perception": 10, "performance": 11, "persuasion": 12, "religion": 13, "sleight_of_hand": 14, "stealth": 15, "survival": -16})
    validate_weapons([{"name": "", "attack": "", "damage": ""} for _ in range(5)])
    validate_weapons([{"name": "Bow", "attack": "+5", "damage": "1d8+3"} for _ in range(5)])


@pytest.mark.parametrize("value", ["../x", "C:/secret", "/absolute", "a/../../x", "a\\..\\secret", ""])
def test_paths_cannot_escape_storage(value):
    from game.validators import validate_relative_path
    with pytest.raises(ValidationError):
        validate_relative_path(value)


def test_windows_root_relative_path_cannot_escape_media_root():
    from game.validators import validate_relative_path
    with pytest.raises(ValidationError):
        validate_relative_path(r"\outside\file.png")


def test_valid_nested_storage_path_is_accepted():
    from game.validators import validate_relative_path
    validate_relative_path("icons/portrait.png")
