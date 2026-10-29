"""Explicit version-one DTO schemas (never Django serialization)."""
from game.constants import ABILITY_KEYS, SKILL_KEYS

STRING = {'type': 'string'}
INTEGER = {'type': 'integer', 'minimum': 0, 'maximum': 2147483647}
ID = {'type': 'string', 'minLength': 1, 'maxLength': 128}


def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def array(items):
    return {'type': 'array', 'items': items}


def signed(keys):
    return object_schema({key: {'type': 'integer', 'minimum': -2147483648, 'maximum': 2147483647} for key in keys})


CATALOG = {'id': ID, 'name': dict(STRING, minLength=1, maxLength=30), 'description': dict(STRING, minLength=1), 'tags': array(STRING)}
CHARACTER = {
    'id': ID, 'name': CATALOG['name'], 'character_class': CATALOG['name'],
    'portrait': {'type': ['string', 'null'], 'minLength': 1}, 'backstory': STRING, 'traits': STRING,
    'abilities': signed(ABILITY_KEYS), 'saves': signed(ABILITY_KEYS), 'skills': signed(SKILL_KEYS),
    'weapons': dict(array(object_schema(dict.fromkeys(('name','attack','damage'),dict(STRING,maxLength=30)))), minItems=5,maxItems=5),
    **dict.fromkeys(('health_current','health_max','speed','armor_class','proficiency_bonus','spell_save_dc','spell_attack_bonus'),INTEGER),
    **dict.fromkeys(('can_cast','can_rage','can_wildshape'), {'type':'boolean'}),
    'spellcasting_ability': STRING,
    'resources': array(object_schema({'kind': STRING,'level':INTEGER,'current':INTEGER,'maximum':INTEGER})),
    'inventory': array(object_schema({'item':ID,'quantity':dict(INTEGER, minimum=1)})),
    'actions': dict(array(ID),uniqueItems=True),
}
SCHEMAS = {
    'events': array(object_schema(dict(CATALOG,color=STRING))),
    'items': array(object_schema(dict(CATALOG,icon=dict(STRING,minLength=1)))),
    'actions': array(object_schema(dict(CATALOG,icon=dict(STRING,minLength=1),activation=STRING,resource=STRING,slot_level={'type':['integer','null'],'minimum':1,'maximum':7}))),
    'characters': array(object_schema(CHARACTER)),
}
