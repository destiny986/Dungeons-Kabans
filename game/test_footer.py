from unittest.mock import patch

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from game.models import Character
from game.test_web import joined, master, web_settings


@pytest.fixture(autouse=True)
def phrase_file(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    return tmp_path / 'phrases.txt'


def test_footer_reads_current_file_and_escapes_text(phrase_file):
    phrase_file.write_text('\ufeff\n  <Кабан> & друзья  \n\n', encoding='utf-8')
    html = render_to_string('game/base.html')
    assert '<footer class="app-footer">&lt;Кабан&gt; &amp; друзья <span>✦</span>' in html
    phrase_file.write_text('Новая фраза', encoding='utf-8')
    assert '<footer class="app-footer">Новая фраза ' in render_to_string('game/base.html')


def test_footer_selects_again_on_each_render(phrase_file):
    phrase_file.write_text('Первая\nВторая\nТретья\n', encoding='utf-8')
    with patch('random.choice', side_effect=lambda phrases: phrases[0]) as choose:
        assert '<footer class="app-footer">Первая ' in render_to_string('game/base.html')
        choose.side_effect = lambda phrases: phrases[-1]
        assert '<footer class="app-footer">Третья ' in render_to_string('game/base.html')
        assert choose.call_count == 2
        assert choose.call_args.args[0] == ['Первая', 'Вторая', 'Третья']


@pytest.mark.parametrize('state', ['missing', 'empty', 'directory', 'invalid_utf8'])
def test_footer_fallback(phrase_file, state):
    if state == 'empty':
        phrase_file.write_text(' \n\n', encoding='utf-8')
    elif state == 'directory':
        phrase_file.mkdir()
    elif state == 'invalid_utf8':
        phrase_file.write_bytes(b'\xff')
    assert '<footer class="app-footer">Броски решают многое. Историю создаёте вы. ' in render_to_string('game/base.html')


@pytest.mark.django_db
def test_polls_do_not_render_or_select_footer(master, phrase_file):
    hero = Character.objects.create(name='Кабан')
    player = joined(hero)
    phrase_file.write_text('Фраза для footer', encoding='utf-8')
    with patch('random.choice', side_effect=AssertionError('Poll selected a phrase')):
        for client, url in [(master, reverse('game:master_poll')),
                            (player, reverse('game:player_poll', args=[hero.pk]))]:
            response = client.get(url, HTTP_HX_REQUEST='true')
            assert response.status_code == 200
            assert b'<footer' not in response.content
            assert b'hx-swap-oob' in response.content
