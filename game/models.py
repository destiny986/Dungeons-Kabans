import uuid
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q, F

from .constants import ABILITY_CHOICES, COLOR_CHOICES, ACTIVATION_CHOICES, ACTION_RESOURCE_CHOICES, RESOURCE_CHOICES, MESSAGE_KINDS, IMPORT_STATUSES
from .validators import (normalize_name, normalize_search, validate_digest, validate_relative_path,
                         validate_abilities, validate_skills, validate_weapons, validate_tags,
                         validate_json_dict, default_abilities, default_skills, default_weapons)


class JSONValidationMixin:
    """Django skips field validators for blank JSON containers; enforce shape here."""

    def clean(self):
        super().clean()
        errors = {}
        for field in self._meta.fields:
            if isinstance(field, models.JSONField):
                for validator in field.validators:
                    try:
                        validator(getattr(self, field.name))
                    except ValidationError as exc:
                        errors.setdefault(field.name, []).extend(exc.messages)
        if errors:
            raise ValidationError(errors)


class UUIDModel(JSONValidationMixin, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class MediaAsset(UUIDModel):
    digest = models.CharField(max_length=64, unique=True, validators=[validate_digest])
    path = models.CharField(max_length=512, validators=[validate_relative_path])
    width = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    height = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)


class NamedModel(UUIDModel):
    name = models.CharField(max_length=30)
    normalized_name = models.CharField(max_length=90, unique=True, editable=False)
    revision = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def prepare_normalized_fields(self):
        self.name = unicodedata.normalize("NFC", self.name).strip()
        self.normalized_name = normalize_name(self.name)

    def clean_fields(self, exclude=None):
        self.prepare_normalized_fields()
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        self.prepare_normalized_fields()

    def save(self, *args, **kwargs):
        self.prepare_normalized_fields()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"normalized_name", "name", "updated_at"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class CatalogModel(NamedModel):
    description = models.TextField()
    tags = models.JSONField(default=list, blank=True, validators=[validate_tags])
    search_text = models.TextField(editable=False)

    class Meta:
        abstract = True

    def prepare_normalized_fields(self):
        super().prepare_normalized_fields()
        tags = self.tags if isinstance(self.tags, list) else []
        self.search_text = normalize_search(" ".join([self.name, self.description, *[t for t in tags if isinstance(t, str)]]))

    def save(self, *args, **kwargs):
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"search_text"}
        return super().save(*args, **kwargs)


class Event(CatalogModel):
    color = models.CharField(max_length=6, choices=COLOR_CHOICES)


class Item(CatalogModel):
    icon = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name="items")


class Action(CatalogModel):
    icon = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name="actions")
    activation = models.CharField(max_length=8, choices=ACTIVATION_CHOICES, default="action")
    resource = models.CharField(max_length=10, choices=ACTION_RESOURCE_CHOICES, default="none")
    slot_level = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(7)])

    def clean(self):
        super().clean()
        if self.resource == "spell_slot":
            if type(self.slot_level) is not int or not 1 <= self.slot_level <= 7:
                raise ValidationError({"slot_level": "Spell slots require a level from 1 to 7."})
        elif self.slot_level is not None:
            raise ValidationError({"slot_level": "Only spell slot actions have a slot level."})


class Character(NamedModel):
    character_class = models.CharField(max_length=30)
    portrait = models.ForeignKey(MediaAsset, on_delete=models.SET_NULL, null=True, blank=True, related_name="portraits")
    backstory = models.TextField(blank=True)
    traits = models.TextField(blank=True)
    abilities = models.JSONField(default=default_abilities, validators=[validate_abilities])
    saves = models.JSONField(default=default_abilities, validators=[validate_abilities])
    skills = models.JSONField(default=default_skills, validators=[validate_skills])
    weapons = models.JSONField(default=default_weapons, validators=[validate_weapons])
    health_current = models.PositiveIntegerField(default=0)
    health_max = models.PositiveIntegerField(default=0)
    speed = models.PositiveIntegerField(default=0)
    armor_class = models.PositiveIntegerField(default=0)
    proficiency_bonus = models.PositiveIntegerField(default=0)
    can_cast = models.BooleanField(default=False)
    can_rage = models.BooleanField(default=False)
    can_wildshape = models.BooleanField(default=False)
    spellcasting_ability = models.CharField(max_length=3, choices=ABILITY_CHOICES, blank=True)
    spell_save_dc = models.PositiveIntegerField(default=0)
    spell_attack_bonus = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=False)
    invitation_generation = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    def clean(self):
        super().clean()
        errors = {}
        for field, validator in (("abilities", validate_abilities), ("saves", validate_abilities), ("skills", validate_skills), ("weapons", validate_weapons)):
            try:
                validator(getattr(self, field))
            except ValidationError as exc:
                errors[field] = exc.messages
        if self.can_cast and self.spellcasting_ability not in dict(ABILITY_CHOICES):
            errors["spellcasting_ability"] = "Choose a spellcasting ability."
        elif not self.can_cast and self.spellcasting_ability:
            errors["spellcasting_ability"] = "Leave blank when spellcasting is disabled."
        if errors:
            raise ValidationError(errors)


class CharacterResource(models.Model):
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="resources")
    kind = models.CharField(max_length=10, choices=RESOURCE_CHOICES)
    level = models.PositiveSmallIntegerField(default=0)
    current = models.PositiveIntegerField(default=0)
    maximum = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["character", "kind", "level"], name="unique_character_resource"),
            models.CheckConstraint(condition=Q(current__gte=0, maximum__gte=0, current__lte=F("maximum")), name="resource_current_within_max"),
            models.CheckConstraint(condition=Q(kind="spell_slot", level__gte=1, level__lte=7) | Q(kind__in=["rage", "wildshape"], level=0), name="resource_kind_level"),
        ]

    def clean(self):
        super().clean()
        if self.kind == "spell_slot":
            valid = type(self.level) is int and 1 <= self.level <= 7
        else:
            valid = self.kind in {"rage", "wildshape"} and self.level == 0
        if not valid:
            raise ValidationError({"level": "Spell slots use levels 1–7; other resources use level 0."})
        if self.current is not None and self.maximum is not None and self.current > self.maximum:
            raise ValidationError({"current": "Current resource cannot exceed its maximum."})


class InventoryEntry(models.Model):
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="inventory")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="inventory_entries")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["character", "item"], name="unique_character_item"), models.CheckConstraint(condition=Q(quantity__gt=0), name="inventory_quantity_positive")]


class CharacterAction(models.Model):
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="action_grants")
    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name="character_grants")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["character", "action"], name="unique_character_action")]


class CharacterInvitation(UUIDModel):
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="invitations")
    token_digest = models.CharField(max_length=64, unique=True, validators=[validate_digest])
    generation = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class CharacterGrant(UUIDModel):
    session_key = models.CharField(max_length=40, db_index=True)
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="browser_grants")
    invitation = models.ForeignKey(CharacterInvitation, on_delete=models.CASCADE, related_name="browser_grants")
    generation = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    revoked = models.BooleanField(default=False)
    last_read_message_id = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session_key", "character"], name="unique_session_character")]


class ChatMessage(JSONValidationMixin, models.Model):
    id = models.BigAutoField(primary_key=True)
    kind = models.CharField(max_length=12, choices=MESSAGE_KINDS)
    from_master = models.BooleanField(default=False)
    sender_character = models.ForeignKey(Character, on_delete=models.CASCADE, null=True, blank=True, related_name="sent_messages")
    text = models.TextField(blank=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True, related_name="chat_messages")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, null=True, blank=True, related_name="chat_messages")
    action = models.ForeignKey(Action, on_delete=models.CASCADE, null=True, blank=True, related_name="chat_messages")
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_json_dict])
    created_at = models.DateTimeField(auto_now_add=True)
    recipients = models.ManyToManyField(Character, through="MessageRecipient", related_name="received_messages")


class MessageRecipient(models.Model):
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="recipient_links")
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="message_receipts")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "character"], name="unique_message_recipient")]


class Operation(UUIDModel):
    scope = models.CharField(max_length=255)
    key = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    result = models.JSONField(default=dict, blank=True, validators=[validate_json_dict])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scope", "key"], name="unique_operation_scope_key")]


class ImportJob(UUIDModel):
    session_key = models.CharField(max_length=40, db_index=True)
    status = models.CharField(max_length=10, choices=IMPORT_STATUSES, default="staged")
    directory = models.CharField(max_length=512, validators=[validate_relative_path])
    payload = models.JSONField(default=dict, blank=True, validators=[validate_json_dict])
    preview = models.JSONField(default=dict, blank=True, validators=[validate_json_dict])
    choices = models.JSONField(default=dict, blank=True, validators=[validate_json_dict])
    base_digest = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
