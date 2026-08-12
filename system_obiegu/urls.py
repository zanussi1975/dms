from django.contrib import admin
from django.urls import path, include
from django.conf import settings # Import ustawień
from django.conf.urls.static import static # Import do obsługi plików

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dokumenty.urls')),
]

# Pozwala serwerowi developerskiemu wyświetlać wgrywane pliki (PDF, obrazki)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)