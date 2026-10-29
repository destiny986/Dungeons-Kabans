from django.urls import include, path
from game.health import healthz

urlpatterns = [path('healthz/',healthz,name='healthz'),path('',include('game.urls'))]
