from django.conf import settings
import os
from .models import WorkflowRule, DocumentStatus

def company_config(request):
    """
    Automatycznie wstrzykuje zmienne konfiguracyjne firmy 
    do każdego szablonu HTML w systemie.
    """
    # 1. Pobieranie konfiguracji domyślnej bazy danych
    db_config = settings.DATABASES.get('default', {})
    
    # 2. Wyciąganie nazwy bazy 
    # (Zabezpieczenie: jeśli to lokalne SQLite, 'NAME' jest ścieżką do pliku, więc wycinamy samą nazwę)
    raw_name = str(db_config.get('NAME', 'Nieznana_baza'))
    db_name = os.path.basename(raw_name) if 'sqlite3' in raw_name else raw_name
    
    # 3. Wyciąganie hosta (jeśli nie jest podany w ustawieniach, zakładamy domyślny localhost)
    db_host = db_config.get('HOST', '')
    if not db_host:
        db_host = 'localhost'
        
    # Sformatowany ciąg znaków, np. "192.168.1.100 / BAZA_PROD"
    server_info = f"{db_host} / {db_name}"
    
    has_ksef_access = False
    if request.user.is_authenticated:
        has_ksef_access = WorkflowRule.objects.filter(
            target_status__in=[DocumentStatus.READY_FOR_ACCOUNTING, DocumentStatus.ACCOUNTANT_APPROVAL],
            default_assignee=request.user
        ).exists()

    return {
        'COMPANY_NAME': getattr(settings, 'COMPANY_NAME', 'Nazwa Firmy'),
        'COMPANY_NIP': getattr(settings, 'COMPANY_NIP', 'Brak NIP'),
        'DB_SERVER_NAME': server_info,
    }