import pytest
from django.template.loader import render_to_string

from game.models import Action
from game.test_ui_review import Controls


@pytest.mark.parametrize('activation,symbol', [('action', '◯'), ('bonus', '△'), ('reaction', '✧'), ('free', '')])
@pytest.mark.parametrize('resource', ['none', 'spell_slot', 'rage', 'wildshape'])
@pytest.mark.parametrize('template,preview', [('game/_card_content.html', False), ('game/_card_content.html', True), ('game/import_card.html', False)])
def test_action_metadata_visibility(activation, symbol, resource, preview, template):
    obj = Action(activation=activation, resource=resource, slot_level=3 if resource == 'spell_slot' else None)
    html = render_to_string(template, {'kind': 'action', 'object': obj, 'preview': preview})
    elements = Controls(html).elements
    badges = {attrs['data-action-badge']: attrs for tag, attrs in elements if 'data-action-badge' in attrs}
    if preview or activation != 'free':
        assert ('hidden' in badges['activation']) == (activation == 'free')
    else:
        assert 'activation' not in badges
    if preview or resource != 'none':
        assert ('hidden' in badges['resource']) == (resource == 'none')
    else:
        assert 'resource' not in badges
    if symbol:
        assert symbol in html
    if resource != 'none':
        assert '▣' in html
    containers = [attrs for tag, attrs in elements if 'data-action-meta' in attrs]
    assert bool(containers) == (preview or activation != 'free' or resource != 'none')
    if containers:
        assert ('hidden' in containers[0]) == (activation == 'free' and resource == 'none')


def test_free_is_valid_activation():
    action = Action(activation='free')
    Action._meta.get_field('activation').validate(action.activation, action)
    assert action.get_activation_display() == 'Бесплатно'
