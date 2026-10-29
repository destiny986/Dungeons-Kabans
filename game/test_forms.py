import tempfile
from pathlib import Path
from django.test import TransactionTestCase, override_settings
from . import forms
from .constants import ABILITY_KEYS, SKILL_KEYS
from .models import Character, CharacterResource, Item, Action, InventoryEntry, CharacterAction, ChatMessage
from .test_media import png
from .catalog import delete_character
from django.db import IntegrityError
from unittest.mock import patch


def character_data(**extra):
    data = dict(name='Герой', character_class='Воин', health_current=150, health_max=100,
                speed=0, armor_class=0, proficiency_bonus=0)
    for prefix in ('ability', 'save'):
        data.update({f'{prefix}_{key}': -3 for key in ABILITY_KEYS})
    data.update({f'skill_{key}': -2 for key in SKILL_KEYS})
    data.update(extra)
    return data


class FormTests(TransactionTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        override = override_settings(MEDIA_ROOT=self.directory.name)
        override.enable()
        self.addCleanup(override.disable)

    def test_character_signed_health_optional_portrait_and_resources(self):
        form = forms.CharacterForm(character_data())
        self.assertTrue(form.is_valid(), form.errors)
        char = form.save()
        self.assertEqual((char.health_current, char.health_max), (150, 100))
        self.assertEqual(char.abilities['str'], -3)
        self.assertEqual(char.skills['arcana'], -2)
        self.assertEqual(char.resources.count(), 9)
        self.assertIsNone(char.portrait)

    def test_catalog_normalizes_duplicates_tags_and_keeps_icon(self):
        form = forms.get_catalog_form('item', {'name':' МЕЧ ', 'description':'Описание', 'tags':' бой, ,бой, сталь '}, {'icon':png()})
        self.assertTrue(form.is_valid(), form.errors)
        item = form.save()
        self.assertEqual(item.tags, ['бой', 'сталь'])
        duplicate = forms.get_catalog_form('item', {'name':'меч','description':'x'}, {'icon':png()})
        self.assertFalse(duplicate.is_valid())
        self.assertIn('name', duplicate.errors)
        edit = forms.get_catalog_form('item', {'name':'Новый меч','description':'x','revision':item.revision}, instance=item)
        self.assertTrue(edit.is_valid(), edit.errors)
        self.assertEqual(edit.save().icon_id, item.icon_id)

    def test_invalid_form_does_not_store_image_and_retains_fields(self):
        form = forms.CharacterForm(character_data(name='', weapon_1_name='Меч'), {'portrait':png()})
        self.assertFalse(form.is_valid())
        self.assertIn('weapon_1_attack', form.errors)
        self.assertEqual(form['weapon_1_name'].value(), 'Меч')
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_capability_validation_and_preserves_disabled_resources(self):
        char = forms.CharacterForm(character_data()).save()
        resource = char.resources.get(kind='rage')
        resource.current, resource.maximum = 2, 3
        resource.save()
        data = character_data(revision=char.revision, rage_current=0, rage_maximum=0)
        saved = forms.CharacterForm(data, instance=char).save()
        self.assertEqual(saved.resources.get(kind='rage').current, 2)
        invalid = forms.CharacterForm(character_data(name='Маг', can_cast='on'))
        self.assertFalse(invalid.is_valid())
        self.assertIn('spellcasting_ability', invalid.errors)
        self.assertIn('slot_1_current', invalid.errors)

    def test_inventory_actions_replacement_and_health_log(self):
        item = forms.get_catalog_form('item', {'name':'Меч','description':'x'}, {'icon':png()}).save()
        action = forms.get_catalog_form('action', {'name':'Удар','description':'x','activation':'action','resource':'none'}, {'icon':png()}).save()
        char = forms.CharacterForm(character_data(**{f'item_{item.pk}':2, f'action_{action.pk}':'on'})).save()
        self.assertEqual(char.inventory.get().quantity, 2)
        self.assertEqual(char.action_grants.get().action_id, action.pk)
        form = forms.CharacterForm(character_data(revision=char.revision, health_current=99, **{f'item_{item.pk}':0}), instance=char)
        saved = form.save()
        self.assertFalse(saved.inventory.exists())
        self.assertFalse(saved.action_grants.exists())
        message = ChatMessage.objects.get(kind='health')
        self.assertEqual(list(message.recipients.values_list('pk', flat=True)), [char.pk])

    def test_stale_save_rechecks_before_file_or_relationship_write(self):
        char = forms.CharacterForm(character_data()).save()
        form = forms.CharacterForm(character_data(revision=char.revision), {'portrait':png()}, instance=char)
        self.assertTrue(form.is_valid(), form.errors)
        Character.objects.filter(pk=char.pk).update(revision=2, health_current=70)
        with self.assertRaises(forms.StaleEdit):
            form.save()
        char.refresh_from_db()
        self.assertEqual(char.health_current, 70)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_slot_range_and_portrait_size(self):
        form = forms.get_catalog_form('action', {'name':'Огонь','description':'x','activation':'bonus','resource':'spell_slot','slot_level':8}, {'icon':png()})
        self.assertFalse(form.is_valid())
        char = forms.CharacterForm(character_data(), {'portrait':png()}).save()
        self.assertEqual((char.portrait.width, char.portrait.height), (512, 512))

    def test_disabled_resource_input_is_ignored_even_if_malformed(self):
        char = forms.CharacterForm(character_data()).save()
        form = forms.CharacterForm(character_data(revision=char.revision, rage_current='oops', rage_maximum=-4), instance=char)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().resources.get(kind='rage').current, 0)

    def test_database_failure_compensates_new_file(self):
        form = forms.CharacterForm(character_data(), {'portrait':png()})
        self.assertTrue(form.is_valid(), form.errors)
        with patch.object(Character, 'save', side_effect=IntegrityError('failed')):
            with self.assertRaises(IntegrityError):
                form.save()
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        self.assertFalse(Character.objects.exists())

    def test_delete_character_removes_orphans_preserves_other_recipient(self):
        first = forms.CharacterForm(character_data()).save()
        second = forms.CharacterForm(character_data(name='Другой')).save()
        shared = ChatMessage.objects.create(kind='text',text='Герой остаётся в тексте')
        shared.recipients.add(first,second)
        orphan = ChatMessage.objects.create(kind='health')
        orphan.recipients.add(first)
        delete_character(first.pk)
        self.assertTrue(ChatMessage.objects.filter(pk=shared.pk).exists())
        self.assertFalse(ChatMessage.objects.filter(pk=orphan.pk).exists())
        self.assertEqual(shared.recipients.count(), 1)

    def test_queryset_character_delete_removes_recipient_orphans(self):
        char = forms.CharacterForm(character_data()).save()
        message = ChatMessage.objects.create(kind='health')
        message.recipients.add(char)
        Character.objects.filter(pk=char.pk).delete()
        self.assertFalse(ChatMessage.objects.filter(pk=message.pk).exists())

    def test_database_integer_overflow_is_a_retained_russian_field_error(self):
        item = forms.get_catalog_form('item', {'name':'Меч','description':'x'}, {'icon':png()}).save()
        huge = str(10 ** 30)
        fields = ['health_current', 'health_max', 'speed', 'armor_class', 'proficiency_bonus',
                  'spell_save_dc', 'spell_attack_bonus', 'rage_current', 'rage_maximum',
                  'wildshape_current', 'wildshape_maximum', f'item_{item.pk}']
        fields += [f'slot_{level}_{suffix}' for level in range(1,8) for suffix in ('current','maximum')]
        for field in fields:
            with self.subTest(field=field):
                data = character_data(can_cast='on',can_rage='on',can_wildshape='on',
                                      spellcasting_ability='int',spell_save_dc=0,spell_attack_bonus=0,
                                      rage_current=0,rage_maximum=0,wildshape_current=0,wildshape_maximum=0)
                for level in range(1,8):
                    data.update({f'slot_{level}_current':0,f'slot_{level}_maximum':0})
                data[field] = huge
                form = forms.CharacterForm(data)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)
                self.assertIn('Значение слишком велико.', form.errors[field])
                self.assertEqual(form[field].value(), huge)
        self.assertFalse(Character.objects.exists())

    def test_signed_json_statistics_do_not_inherit_database_integer_bounds(self):
        huge = 10 ** 30
        form = forms.CharacterForm(character_data(ability_str=huge,save_str=-huge,skill_arcana=huge))
        self.assertTrue(form.is_valid(), form.errors)
        char = form.save()
        self.assertEqual(char.abilities['str'], huge)
        self.assertEqual(char.saves['str'], -huge)
        self.assertEqual(char.skills['arcana'], huge)
