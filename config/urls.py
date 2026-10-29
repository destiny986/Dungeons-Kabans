from django.contrib import admin
from django.urls import path
from game.health import healthz

urlpatterns = [path("healthz/", healthz, name="healthz"), path("admin/", admin.site.urls)]
