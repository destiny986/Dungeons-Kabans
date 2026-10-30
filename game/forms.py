"""Russian journal forms with atomic optimistic concurrency checks."""
from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import transaction

from .constants import ABILITY_CHOICES, SKILL_CHOICES, COLOR_CHOICES, ACTIVATION_CHOICES, ACTION_RESOURCE_CHOICES
from .models import (Character, CharacterResource, InventoryEntry, CharacterAction,
                     Item, Action, ChatMessage, MessageRecipient)
from .validators import normalize_name
from .catalog import CATALOG_MODELS
from .media import prepare_png, store_prepared, release_assets, cleanup_unreferenced_file


class StaleEdit(ValidationError):
    def __init__(self):
        super().__init__('Данные уже изменились. Обновите страницу и повторите редактирование.')


def integer(label='', **kwargs):
    return forms.IntegerField(label=label, error_messages={'required':'Заполните это поле.', 'invalid':'Введите целое число.', 'min_value':'Значение не может быть отрицательным.', 'max_value':'Значение слишком велико.'}, **kwargs)


def database_integer(model, name, label='', **kwargs):
    """Reuse model/database upper bounds without bounding signed JSON values."""
    maxima = [validator.limit_value() if callable(validator.limit_value) else validator.limit_value
              for validator in model._meta.get_field(name).validators
              if isinstance(validator, MaxValueValidator)]
    if maxima:
        kwargs['max_value'] = min(maxima + ([kwargs['max_value']] if 'max_value' in kwargs else []))
    return integer(label, **kwargs)


class RevisionForm(forms.Form):
    name = forms.CharField(label='Имя', max_length=30)
    revision = forms.IntegerField(widget=forms.HiddenInput, required=False, min_value=1)
    model = None
    image_field = None
    image_size = 256

    def __init__(self, data=None, files=None, instance=None, **kwargs):
        self.instance = instance
        self.prepared = None
        initial = kwargs.pop('initial', {})
        if instance is not None:
            initial.update({field.name: getattr(instance, field.name) for field in instance._meta.fields if field.name not in ('icon', 'portrait')})
        super().__init__(data=data, files=files, initial=initial, **kwargs)
        self.fields['revision'].required = instance is not None
        for field in self.fields.values():
            field.error_messages.update({'required':'Заполните это поле.', 'invalid_choice':'Выберите допустимое значение.', 'max_length':'Не более %(limit_value)s символов.'})

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        query = self.model.objects.filter(normalized_name=normalize_name(name))
        if self.instance:
            query = query.exclude(pk=self.instance.pk)
        if query.exists():
            raise ValidationError('Такое имя уже используется.')
        return name

    def clean(self):
        data = super().clean()
        if self.instance and data.get('revision') != self.instance.revision:
            raise StaleEdit()
        if self.instance and not self.model.objects.filter(pk=self.instance.pk, revision=data.get('revision')).exists():
            raise StaleEdit()
        if self.image_field and data.get(self.image_field):
            try:
                self.prepared = prepare_png(data[self.image_field], size=self.image_size)
            except ValidationError as exc:
                self.add_error(self.image_field, exc)
        return data

    def assign(self, obj):
        raise NotImplementedError

    def save_relations(self, obj):
        pass

    def save(self):
        if not self.is_valid():
            raise ValueError('Нельзя сохранить форму с ошибками.')
        try:
            with transaction.atomic():
                if self.instance:
                    obj = self.model.objects.select_for_update().get(pk=self.instance.pk)
                    if obj.revision != self.cleaned_data['revision']:
                        raise StaleEdit()
                    obj.revision += 1
                else:
                    obj = self.model()
                previous_asset = getattr(obj, f'{self.image_field}_id', None) if self.image_field else None
                previous_health = obj.health_current if self.model is Character and self.instance else None
                self.assign(obj)
                if self.prepared:
                    setattr(obj, self.image_field, store_prepared(self.prepared))
                obj.full_clean()
                obj.save()
                self.save_relations(obj)
                if previous_health is not None and previous_health != obj.health_current:
                    message = ChatMessage.objects.create(kind='health', from_master=True,
                        text=f'{obj.name}: здоровье {previous_health} → {obj.health_current}.',
                        metadata={'previous':previous_health, 'current':obj.health_current})
                    MessageRecipient.objects.create(message=message, character=obj)
                if self.image_field and previous_asset != getattr(obj, f'{self.image_field}_id'):
                    release_assets([previous_asset])
                self.instance = obj
                return obj
        except Exception:
            if self.prepared:
                cleanup_unreferenced_file(self.prepared)
            raise


class CatalogForm(RevisionForm):
    description = forms.CharField(label='Описание', widget=forms.Textarea)
    tags = forms.CharField(label='Теги', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance:
            self.initial['tags'] = ', '.join(self.instance.tags)

    def clean_tags(self):
        return list(dict.fromkeys(tag.strip() for tag in self.cleaned_data['tags'].split(',') if tag.strip()))

    def assign(self, obj):
        for key in ('name', 'description', 'tags', 'color', 'activation', 'resource', 'slot_level'):
            if key in self.cleaned_data:
                setattr(obj, key, self.cleaned_data[key])


class EventForm(CatalogForm):
    model = CATALOG_MODELS['event']
    color = forms.ChoiceField(label='Цвет', choices=COLOR_CHOICES)


class ItemForm(CatalogForm):
    model = Item
    image_field = 'icon'
    icon = forms.FileField(label='Значок PNG', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['icon'].required = self.instance is None


class ActionForm(ItemForm):
    model = Action
    activation = forms.ChoiceField(label='Активация', choices=ACTIVATION_CHOICES, initial='action')
    resource = forms.ChoiceField(label='Ресурс', choices=ACTION_RESOURCE_CHOICES, initial='none')
    slot_level = integer('Уровень ячейки', min_value=1, max_value=7, required=False, initial=1)

    def clean(self):
        data = super().clean()
        if data.get('resource') == 'spell_slot':
            if not data.get('slot_level') and 'slot_level' not in self.errors:
                data['slot_level'] = 1
        else:
            data['slot_level'] = None
        return data


def get_catalog_form(kind, data=None, files=None, instance=None):
    return {'event':EventForm, 'item':ItemForm, 'action':ActionForm}[kind](data=data, files=files, instance=instance)


class CharacterForm(RevisionForm):
    model = Character
    image_field = 'portrait'
    image_size = 512
    character_class = forms.CharField(label='Класс', max_length=30)
    portrait = forms.FileField(label='Портрет PNG', required=False)
    backstory = forms.CharField(label='Предыстория', widget=forms.Textarea, required=False)
    traits = forms.CharField(label='Черты', widget=forms.Textarea, required=False)
    health_current = database_integer(Character, 'health_current', 'Здоровье', min_value=0, initial=0)
    health_max = database_integer(Character, 'health_max', 'Максимальное здоровье', min_value=0, initial=0)
    speed = database_integer(Character, 'speed', 'Скорость', min_value=0, initial=0)
    armor_class = database_integer(Character, 'armor_class', 'Класс брони', min_value=0, initial=0)
    proficiency_bonus = database_integer(Character, 'proficiency_bonus', 'Бонус мастерства', min_value=0, initial=0)
    can_cast = forms.BooleanField(label='Заклинания', required=False)
    can_rage = forms.BooleanField(label='Ярость', required=False)
    can_wildshape = forms.BooleanField(label='Дикий облик', required=False)
    spellcasting_ability = forms.ChoiceField(label='Характеристика заклинаний', choices=(('', '—'),) + ABILITY_CHOICES, required=False)
    spell_save_dc = database_integer(Character, 'spell_save_dc', 'Сложность спасброска', min_value=0, required=False, initial=0)
    spell_attack_bonus = database_integer(Character, 'spell_attack_bonus', 'Атака заклинанием', min_value=0, required=False, initial=0)

    def __init__(self, data=None, files=None, instance=None, **kwargs):
        # Read the entity and all editable relationships within one database snapshot.
        with transaction.atomic():
            if instance:
                instance = Character.objects.get(pk=instance.pk)
            super().__init__(data=data, files=files, instance=instance, **kwargs)
            self.resource_snapshot = {(r.kind,r.level):(r.current,r.maximum) for r in instance.resources.all()} if instance else {}
            self.items = list(Item.objects.select_related('icon').order_by('name'))
            self.actions = list(Action.objects.select_related('icon').order_by('name'))
            inventory = dict(instance.inventory.values_list('item_id','quantity')) if instance else {}
            actions = set(instance.action_grants.values_list('action_id',flat=True)) if instance else set()
        if self.is_bound:
            self.data = self.data.copy()
        for key, label in ABILITY_CHOICES:
            for prefix, attr in [('ability','abilities'),('save','saves')]:
                self.fields[f'{prefix}_{key}'] = integer(label, initial=getattr(instance,attr)[key] if instance else 0)
        for key, label in SKILL_CHOICES:
            self.fields[f'skill_{key}'] = integer(label, initial=instance.skills[key] if instance else 0)
        self.resource_fields = []
        for kind, level, prefix in [('spell_slot', n, f'slot_{n}') for n in range(1,8)] + [('rage',0,'rage'),('wildshape',0,'wildshape')]:
            values = self.resource_snapshot.get((kind,level),(0,0))
            self.resource_fields.append((kind,level,prefix))
            for index, suffix in enumerate(('current','maximum')):
                self.fields[f'{prefix}_{suffix}'] = database_integer(CharacterResource, suffix, min_value=0, required=False, initial=values[index])
                if self.is_bound and not self.fields[{'spell_slot':'can_cast','rage':'can_rage','wildshape':'can_wildshape'}[kind]].widget.value_from_datadict(self.data, self.files, {'spell_slot':'can_cast','rage':'can_rage','wildshape':'can_wildshape'}[kind]):
                    # Ignore hidden capability values on the server, while leaving
                    # rendered controls usable if the user toggles the capability.
                    self.data[f'{prefix}_{suffix}'] = values[index]
        for n in range(1,6):
            for key in ('name','attack','damage'):
                self.fields[f'weapon_{n}_{key}'] = forms.CharField(required=False, max_length=30,
                    initial=instance.weapons[n-1][key] if instance else '', error_messages={'max_length':'Не более 30 символов.'})
        for item in self.items:
            self.fields[f'item_{item.pk}'] = database_integer(InventoryEntry, 'quantity', item.name, min_value=0, required=False, initial=inventory.get(item.pk,0))
        for action in self.actions:
            self.fields[f'action_{action.pk}'] = forms.BooleanField(label=action.name, required=False, initial=action.pk in actions)

    def clean(self):
        data = super().clean()
        for field in ('spellcasting_ability','spell_save_dc','spell_attack_bonus'):
            if data.get('can_cast') and data.get(field) in (None,'') and field not in self.errors:
                self.add_error(field, 'Заполните это поле.')
        if not data.get('can_cast'):
            data['spellcasting_ability'] = ''
            for field in ('spell_save_dc','spell_attack_bonus'):
                data[field] = getattr(self.instance,field,0)
        for kind,level,prefix in self.resource_fields:
            enabled = data.get({'spell_slot':'can_cast','rage':'can_rage','wildshape':'can_wildshape'}[kind])
            if enabled:
                for suffix in ('current','maximum'):
                    field = f'{prefix}_{suffix}'
                    if data.get(field) is None and field not in self.errors:
                        self.add_error(field,'Заполните это поле.')
                current, maximum = data.get(f'{prefix}_current'), data.get(f'{prefix}_maximum')
                if current is not None and maximum is not None and current > maximum:
                    self.add_error(f'{prefix}_current','Текущее значение не может превышать максимум.')
        for n in range(1,6):
            keys = [f'weapon_{n}_{key}' for key in ('name','attack','damage')]
            if any(data.get(key) for key in keys):
                for key in keys:
                    if not data.get(key) and key not in self.errors:
                        self.add_error(key,'Заполните всю строку оружия или оставьте её пустой.')
        return data

    def assign(self, obj):
        for key in ('name','character_class','backstory','traits','health_current','health_max','speed','armor_class','proficiency_bonus','can_cast','can_rage','can_wildshape','spellcasting_ability','spell_save_dc','spell_attack_bonus'):
            setattr(obj,key,self.cleaned_data[key])
        for prefix,attr,choices in [('ability','abilities',ABILITY_CHOICES),('save','saves',ABILITY_CHOICES),('skill','skills',SKILL_CHOICES)]:
            setattr(obj,attr,{key:self.cleaned_data[f'{prefix}_{key}'] for key,label in choices})
        obj.weapons = [{key:self.cleaned_data[f'weapon_{n}_{key}'] for key in ('name','attack','damage')} for n in range(1,6)]

    def save_relations(self, obj):
        for kind,level,prefix in self.resource_fields:
            enabled = getattr(obj,{'spell_slot':'can_cast','rage':'can_rage','wildshape':'can_wildshape'}[kind])
            current, maximum = (self.cleaned_data[f'{prefix}_current'], self.cleaned_data[f'{prefix}_maximum']) if enabled else self.resource_snapshot.get((kind,level),(0,0))
            resource, _ = CharacterResource.objects.get_or_create(character=obj,kind=kind,level=level,defaults={'current':current,'maximum':maximum})
            resource.current, resource.maximum = current, maximum
            resource.full_clean()
            resource.save()
        for item in self.items:
            quantity = self.cleaned_data.get(f'item_{item.pk}') or 0
            if quantity:
                entry, _ = InventoryEntry.objects.get_or_create(character=obj,item=item,defaults={'quantity':quantity})
                entry.quantity = quantity
                entry.full_clean()
                entry.save()
            else:
                InventoryEntry.objects.filter(character=obj,item=item).delete()
        for action in self.actions:
            if self.cleaned_data[f'action_{action.pk}']:
                CharacterAction.objects.get_or_create(character=obj,action=action)
            else:
                CharacterAction.objects.filter(character=obj,action=action).delete()

    @property
    def ability_rows(self):
        return [{'label':label,'ability':self[f'ability_{key}'],'save':self[f'save_{key}']} for key,label in ABILITY_CHOICES]

    @property
    def skill_rows(self):
        return [{'label':label,'field':self[f'skill_{key}']} for key,label in SKILL_CHOICES]

    @property
    def slot_rows(self):
        return [{'label':label,'current':self[f'slot_{n}_current'],'maximum':self[f'slot_{n}_maximum']} for n,label in enumerate(('I','II','III','IV','V','VI','VII'),1)]

    @property
    def weapon_rows(self):
        return [dict(label=f'Оружие {n}', **{key:self[f'weapon_{n}_{key}'] for key in ('name','attack','damage')}) for n in range(1,6)]

    @property
    def item_rows(self):
        return [{'pk':item.pk,'label':item.name,'field':self[f'item_{item.pk}'],'icon':item.icon,'search_text':item.search_text} for item in self.items]

    @property
    def action_rows(self):
        return [{'pk':action.pk,'label':action.name,'field':self[f'action_{action.pk}'],'icon':action.icon,'activation':action.get_activation_display(),'resource':action.get_resource_display(),'activation_key':action.activation,'resource_key':action.resource,'slot_level':action.slot_level,'search_text':action.search_text} for action in self.actions]
