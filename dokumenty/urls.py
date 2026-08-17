from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', views.wyloguj_uzytkownika, name='logout'),
    path('biurko/', views.moje_biurko, name='moje_biurko'),
    path('faktura/<int:faktura_id>/', views.szczegoly_faktury, name='szczegoly_faktury'),
    path('profil/', views.ustawienia_profilu, name='ustawienia_profilu'),
    path('api/sprawdz-biurko/', views.sprawdz_nowe_dokumenty, name='sprawdz_biurko'),
    path('faktura/<int:faktura_id>/usun/', views.usun_fakture, name='usun_fakture'),
    path('dodaj-dokument/', views.dodaj_dokument, name='dodaj_dokument'),
    path('archiwum/firmowe/', views.archiwum_firmowe, name='archiwum_firmowe'),
    path('archiwum/osobiste/', views.archiwum_osobiste, name='archiwum_osobiste'),
    path('zamowienia-corim/', views.zamowienia_corim, name='zamowienia_corim'),
    path('importuj-corim/', views.importuj_corim, name='importuj_corim'),
    path('wyszukiwarka-ksef/', views.wyszukiwarka_ksef, name='wyszukiwarka_ksef'),
    path('importuj-zbiorczo-ksef/', views.importuj_zbiorczo_ksef, name='importuj_zbiorczo_ksef'),
    path('zablokowane/', views.dokumenty_zablokowane, name='dokumenty_zablokowane'),
    path('api/save-workflow/', views.save_workflow, name='save_workflow'),
    path('kreator/', views.workflow_editor_view, name='kreator'),
]