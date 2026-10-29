from django.urls import path
from .access_urls import urlpatterns as access_patterns
from .web import views
from .web import data

app_name = 'game'
urlpatterns = access_patterns + [
    path('master/',views.master,name='master'),
    path('master/poll/',views.master_poll,name='master_poll'),
    path('master/characters/',views.characters,name='characters'),
    path('master/characters/new/',views.character_edit,name='character_new'),
    path('master/characters/<uuid:pk>/edit/',views.character_edit,name='character_edit'),
    path('master/characters/<uuid:pk>/delete/',views.character_delete,name='character_delete'),
    path('master/catalog/<str:kind>/new/',views.catalog_edit,name='catalog_new'),
    path('master/catalog/<str:kind>/<uuid:pk>/edit/',views.catalog_edit,name='catalog_edit'),
    path('master/catalog/<str:kind>/<uuid:pk>/delete/',views.catalog_delete,name='catalog_delete'),
    path('master/catalog/<str:kind>/<uuid:pk>/send/',views.catalog_send,name='catalog_send'),
    path('card/<str:kind>/<uuid:pk>/',views.card,name='card'),
    path('player/<uuid:pk>/',views.player,name='player'),
    path('player/<uuid:pk>/poll/',views.player_poll,name='player_poll'),
    path('player/<uuid:pk>/health/',views.health,name='health'),
    path('player/<uuid:pk>/action/<uuid:action_id>/',views.apply_action,name='apply_action'),
    path('player/<uuid:pk>/item/<uuid:item_id>/remove/',views.remove_item,name='remove_item'),
    path('player/<uuid:pk>/read/',views.chat_read,name='chat_read'),
    path('chat/send/',views.chat_send,name='chat_send'),
    path('master/rest/',views.rest,name='rest'),
    path('master/data/',data.index,name='data'),
    path('master/data/export/',data.export,name='export'),
    path('master/data/import/',data.upload,name='import_upload'),
    path('master/data/import/<uuid:pk>/',data.review,name='import_review'),
    path('master/data/import/<uuid:pk>/commit/',data.commit,name='import_commit'),
    path('master/data/import/<uuid:pk>/cancel/',data.cancel,name='import_cancel'),
    path('master/data/wipe/',data.wipe,name='wipe'),
]
