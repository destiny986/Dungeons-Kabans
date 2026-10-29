import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from game import models

pytestmark = pytest.mark.django_db


def character(name="Hero", **kwargs):
    return models.Character.objects.create(name=name, character_class="Wizard", **kwargs)


def test_domain_schema_is_available():
    assert hasattr(models, "Character"), "Character model is required"


def test_health_over_max_is_allowed_but_negative_is_invalid():
    hero = character(health_current=150, health_max=100)
    hero.full_clean()
    hero.health_current = -1
    with pytest.raises(ValidationError):
        hero.full_clean()


def test_unicode_name_uniqueness_and_catalog_search():
    models.Event.objects.create(name="  Café  ", description="ÉTÉ", tags=["MAGIC"], color="red")
    with pytest.raises(IntegrityError), transaction.atomic():
        models.Event.objects.create(name="CAFE\u0301", description="x", color="blue")
    event = models.Event.objects.get()
    assert event.normalized_name == "café"
    assert event.search_text == "café été magic"
    character("  STRASSE ")
    with pytest.raises(IntegrityError), transaction.atomic():
        character("Straße")


@pytest.mark.parametrize("kind,level,current,maximum", [("rage", 1, 0, 1), ("spell_slot", 0, 0, 1), ("spell_slot", 8, 0, 1), ("wildshape", 0, 2, 1), ("bogus", 0, 0, 1), ("rage", 0, -1, 1)])
def test_resource_model_and_database_reject_invalid_states(kind, level, current, maximum):
    row = models.CharacterResource(character=character(), kind=kind, level=level, current=current, maximum=maximum)
    with pytest.raises(ValidationError):
        row.full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        row.save()


def test_resource_unique_and_valid_spell_slot():
    hero = character()
    models.CharacterResource.objects.create(character=hero, kind="spell_slot", level=7, current=1, maximum=2)
    with pytest.raises(IntegrityError), transaction.atomic():
        models.CharacterResource.objects.create(character=hero, kind="spell_slot", level=7, current=0, maximum=2)


def test_action_slot_validation_and_catalog_required_fields():
    icon = models.MediaAsset.objects.create(digest="a"*64, path="icons/a.png", width=1, height=1)
    action = models.Action(name="Spell", description="desc", icon=icon, resource="spell_slot", slot_level=7)
    action.full_clean()
    for level in (None, 0, 8):
        action.slot_level = level
        with pytest.raises(ValidationError):
            action.full_clean()
    action.resource = "none"
    action.slot_level = 1
    with pytest.raises(ValidationError):
        action.full_clean()
    with pytest.raises(ValidationError):
        models.Event(name="x"*31, description="", color="purple").full_clean()


def test_recipient_deletion_preserves_message_for_other_character():
    first, second = character("First"), character("Second")
    event = models.Event.objects.create(name="Storm", description="Rain", color="cyan")
    message = models.ChatMessage.objects.create(kind="event", from_master=True, event=event)
    message.recipients.add(first, second)
    first.delete()
    assert models.ChatMessage.objects.filter(pk=message.pk).exists()
    assert list(message.recipients.all()) == [second]
    event.delete()
    assert not models.ChatMessage.objects.filter(pk=message.pk).exists()


def test_health_endpoint(client):
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert client.post("/healthz/").status_code == 405


@pytest.mark.parametrize("model_name,field", [("Event", "tags"), ("ChatMessage", "metadata"), ("Operation", "result"), ("ImportJob", "payload"), ("ImportJob", "preview"), ("ImportJob", "choices")])
def test_empty_wrong_json_container_is_rejected(model_name, field):
    model = getattr(models, model_name)
    obj = model()
    setattr(obj, field, {} if field == "tags" else [])
    with pytest.raises(ValidationError) as caught:
        obj.full_clean()
    assert field in caught.value.message_dict


def test_inventory_zero_is_rejected_in_database():
    icon = models.MediaAsset.objects.create(digest="b"*64, path="icons/b.png", width=1, height=1)
    item = models.Item.objects.create(name="Rope", description="Rope", icon=icon)
    with pytest.raises(IntegrityError), transaction.atomic():
        models.InventoryEntry.objects.create(character=character(), item=item, quantity=0)


def test_partial_catalog_update_refreshes_derived_search():
    event = models.Event.objects.create(name="Storm", description="Rain", color="cyan")
    event.description = "SNOW"
    event.save(update_fields=["description"])
    event.refresh_from_db()
    assert event.search_text == "storm snow"
