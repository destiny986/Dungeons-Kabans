from django.urls import path
from . import access_views as views

app_name = 'game'
urlpatterns = [
    path('', views.index, name='index'),
    path('master/login/', views.master_login, name='master_login'),
    path('join/<str:token>/', views.join, name='join'),
    path('characters/<uuid:pk>/invite/', views.invite, name='invite'),
    path('characters/<uuid:pk>/revoke/', views.revoke, name='revoke'),
    path('media/<str:digest>/', views.protected_media, name='media'),
]
