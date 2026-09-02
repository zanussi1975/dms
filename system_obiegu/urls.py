from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings # Import ustawień
from django.conf.urls.static import static # Import do obsługi plików
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dokumenty.urls')),
]

# Pozwala serwerowi developerskiemu wyświetlać wgrywane pliki (PDF, obrazki)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {
        'document_root': settings.MEDIA_ROOT,
    }),
]