from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/scan/", views.api_scan),
    path("api/records/", views.api_records),
    path("api/records/<int:pk>/delete/", views.api_delete),
    path("api/clear/", views.api_clear),
    path("export/", views.export_xlsx, name="export"),
]
