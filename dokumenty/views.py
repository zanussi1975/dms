from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model # Do pobrania użytkowników
from .models import Document, DocumentStatus, WorkflowRule, DocumentAttachment, DocumentHistory, UserProfile
from django.db.models import Q
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
import uuid
from datetime import datetime
import pandas as pd
from dbfread import DBF
import os
from django.conf import settings
from .models import Document, DocumentStatus, WorkflowRule, DocumentAttachment, DocumentHistory, UserProfile, Contractor
import json
import requests
import login
from lxml import etree
import pdfkit
from django.core.files import File
import qr_code
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from django.utils import timezone



User = get_user_model()

@login_required
def moje_biurko(request):
    """Widok wyświetlający faktury przypisane do zalogowanego użytkownika oraz z jego puli (Bufor)."""
    # NOWOŚĆ: Pobieramy zapytanie z paska adresu
    query = request.GET.get('q', '')
    
    warunek = Q(assigned_to=request.user)
    
    moje_statusy_domyslne = WorkflowRule.objects.filter(
        default_assignee=request.user
    ).values_list('target_status', flat=True)
    
    if moje_statusy_domyslne:
        warunek = warunek | Q(status__in=moje_statusy_domyslne, assigned_to__isnull=True)
        
    # Pobieramy bazowe dokumenty (z uwzględnieniem wykluczenia archiwum i dokumentow zablokowanych)
    faktury = Document.objects.filter(warunek).exclude(status__in=[DocumentStatus.ARCHIVED, DocumentStatus.BLOCKED])
    
    # NOWOŚĆ: Logika wyszukiwania
    if query:
        faktury = faktury.filter(
            Q(document_number__icontains=query) |
            Q(contractor_name__icontains=query) |
            Q(contractor_nip__icontains=query)
        )
        
    faktury = faktury.order_by('updated_at')
    
    kontrahenci = Contractor.objects.all().order_by('name')
    
    context = {
        'faktury': faktury,
        'query': query,
        'kontrahenci': kontrahenci # Przekazujemy kontrahentów do HTML
    }
    
    return render(request, 'dokumenty/moje_biurko.html', context)



@login_required
def szczegoly_faktury(request, faktura_id):
    faktura = get_object_or_404(Document, id=faktura_id)
    wszyscy_uzytkownicy = User.objects.filter(is_active=True).order_by('last_name')

    dostepne_zamowienia = None
    if faktura.typ_dokumentu == 'FAKTURA':
        dostepne_zamowienia = Document.objects.filter(
            typ_dokumentu='ZAMOWIENIE',
            contractor_nip=faktura.contractor_nip
        ).exclude(status=DocumentStatus.REJECTED).order_by('-created_at')

    if request.method == 'POST':
        akcja = request.POST.get('akcja')
        komentarz = request.POST.get('komentarz', '')
        reczny_adresat_id = request.POST.get('reczny_adresat')
        
        statusy_koncowe = [DocumentStatus.READY_FOR_ACCOUNTING, DocumentStatus.ORDER_ACCEPTED, DocumentStatus.ARCHIVED]
        
        # TWARDA BLOKADA: Dokument gotowy do księgowania / archiwum
        zablokowane_akcje = ['dodaj_zalacznik', 'usun_zalacznik', 'paruj', 'approve_accountant', 'approve_director', 'deleguj', 'zablokuj']
        if faktura.status in statusy_koncowe and akcja in zablokowane_akcje:
            messages.error(request, "Odmowa: Zablokowano edycję i blokowanie dla tego statusu dokumentu.")
            return redirect('szczegoly_faktury', faktura_id=faktura.id)

        # Sprawdzamy czy to Księgowość
        is_chief_accountant = WorkflowRule.objects.filter(target_status=DocumentStatus.READY_FOR_ACCOUNTING, default_assignee=request.user).exists()

        try:
            # 1. OBSŁUGA DODAWANIA ZAŁĄCZNIKA
            if akcja == 'dodaj_zalacznik':
                plik = request.FILES.get('nowy_plik')
                if plik:
                    if not plik.name.lower().endswith('.pdf'):
                        messages.error(request, "Można wgrywać tylko pliki w formacie PDF.")
                    else:
                        DocumentAttachment.objects.create(
                            document=faktura,
                            file=plik,
                            uploaded_by=request.user,
                            filename=plik.name
                        )
                        # Wpis w historii używa Bootstrap Icons w HTML
                        DocumentHistory.objects.create(
                            document=faktura, user=request.user, previous_status=faktura.status, new_status=faktura.status,
                            comment=f'<i class="bi bi-paperclip text-secondary me-1"></i> Dodano załącznik: {plik.name}'
                        )
                        messages.success(request, "Załącznik został pomyślnie dodany.")
                else:
                    messages.error(request, "Nie wybrano żadnego pliku.")
                return redirect('szczegoly_faktury', faktura_id=faktura.id)

            # 2. OBSŁUGA USUWANIA ZAŁĄCZNIKA
            elif akcja == 'usun_zalacznik':
                zalacznik_id = request.POST.get('zalacznik_id')
                zalacznik = get_object_or_404(DocumentAttachment, id=zalacznik_id, document=faktura)
                nazwa_usunietego = zalacznik.filename 
                
                zalacznik.file.delete() 
                zalacznik.delete()      
                
                DocumentHistory.objects.create(
                    document=faktura, user=request.user, previous_status=faktura.status, new_status=faktura.status,
                    comment=f'<i class="bi bi-trash3 text-danger me-1"></i> Usunięto załącznik: {nazwa_usunietego}'
                )
                messages.success(request, "Załącznik usunięto.")
                return redirect('szczegoly_faktury', faktura_id=faktura.id)
            
            # 3. PAROWANIE Z ZAMÓWIENIEM
            elif akcja == 'paruj':
                id_zamowienia = request.POST.get('id_zamowienia')
                
                if faktura.related_order and faktura.paired_by and faktura.paired_by != request.user:
                    messages.error(request, "Odmowa: Tylko osoba, która sparowała ten dokument, może zmienić lub usunąć powiązanie.")
                    return redirect('szczegoly_faktury', faktura_id=faktura.id)

                if id_zamowienia:
                    zamowienie = get_object_or_404(Document, id=id_zamowienia, typ_dokumentu='ZAMOWIENIE')
                    faktura.related_order = zamowienie
                    faktura.paired_by = request.user
                    faktura.save()
                    
                    DocumentHistory.objects.create(
                        document=faktura, user=request.user, previous_status=faktura.status, new_status=faktura.status,
                        comment=f'<i class="bi bi-link-45deg text-primary fs-5 align-middle me-1"></i> Sparowano z zamówieniem nr <strong>{zamowienie.document_number}</strong>'
                    )
                    messages.success(request, f"Pomyślnie sparowano fakturę z zamówieniem {zamowienie.document_number}.")
                else:
                    if faktura.related_order:
                        stare = faktura.related_order.document_number
                        faktura.related_order = None
                        faktura.paired_by = None
                        faktura.save()
                        
                        DocumentHistory.objects.create(
                            document=faktura, user=request.user, previous_status=faktura.status, new_status=faktura.status,
                            comment=f'<i class="bi bi-scissors text-warning me-1"></i> Usunięto powiązanie z zamówieniem nr <strong>{stare}</strong>'
                        )
                        messages.success(request, "Usunięto powiązanie z zamówieniem.")
                return redirect('szczegoly_faktury', faktura_id=faktura.id)

            # 4. AKCEPTACJA: GŁÓWNY KSIĘGOWY
            elif akcja == 'approve_accountant':
                if not getattr(request.user.profile, 'can_approve_accountant', False):
                    raise ValidationError("Brak uprawnień do akceptacji jako Główny Księgowy.")
                    
                faktura.is_accountant_approved = True
                stary_status = faktura.status
                
                # Zabezpieczenie: jeśli z jakiegoś powodu dyrektor zatwierdził wcześniej
                if getattr(faktura, 'is_director_approved', False):
                    faktura.status = DocumentStatus.READY_FOR_ACCOUNTING
                    docelowy_status_reguly = DocumentStatus.READY_FOR_ACCOUNTING
                else:
                    faktura.status = DocumentStatus.ACCOUNTANT_APPROVAL
                    docelowy_status_reguly = DocumentStatus.DIRECTOR_APPROVAL
                
                # AUTOMATYCZNE DELEGOWANIE NA PODSTAWIE REGUŁY
                regula = WorkflowRule.objects.filter(target_status=docelowy_status_reguly).first()
                if regula and regula.default_assignee:
                    faktura.assigned_to = regula.default_assignee
                    adresat_nazwa = f"{regula.default_assignee.first_name} {regula.default_assignee.last_name}"
                else:
                    raise ValidationError(f"Brak przypisanej reguły obiegu dla statusu: {docelowy_status_reguly}. Skonfiguruj reguły w panelu.")
                    
                faktura.save()
                
                # Wpisy do historii: Akceptacja
                tresc_komentarza = '<i class="bi bi-check-circle-fill text-success me-1"></i> <strong>Akceptacja: Główny Księgowy</strong>'
                if komentarz.strip():
                    tresc_komentarza += f"<br><span class='text-muted small'>Komentarz: {komentarz}</span>"
                    
                DocumentHistory.objects.create(
                    document=faktura, user=request.user, previous_status=stary_status, new_status=faktura.status,
                    comment=tresc_komentarza
                )
                
                # Wpisy do historii: Automatyczne przekazanie
                DocumentHistory.objects.create(
                    document=faktura, user=request.user, previous_status=faktura.status, new_status=faktura.status,
                    comment=f'<i class="bi bi-robot text-primary me-1"></i> <strong>System DMS:</strong> Automatyczne przekazanie na biurko: <strong>{adresat_nazwa}</strong>'
                )
                    
                messages.success(request, f"Zapisano akceptację. Dokument został przekazany do: {adresat_nazwa}.")
                return redirect('moje_biurko') # Wyrzucamy użytkownika z powrotem na biurko!

            # 5. AKCEPTACJA: DYREKTOR
            elif akcja == 'approve_director':
                if not getattr(request.user.profile, 'can_approve_director', False):
                    raise ValidationError("Brak uprawnień do akceptacji jako Dyrektor.")
                    
                stary_status = faktura.status
                faktura.is_director_approved = True
                
                # --- ŚCIEŻKA DLA ZAMÓWIENIA ---
                if faktura.typ_dokumentu == 'ZAMOWIENIE':
                    faktura.status = DocumentStatus.ORDER_ACCEPTED
                    
                    # Szukamy pierwszego twórcy dokumentu (nadawcy)
                    pierwszy_krok = faktura.history.order_by('created_at').first()
                    if pierwszy_krok and pierwszy_krok.user:
                        faktura.assigned_to = pierwszy_krok.user
                    else:
                        faktura.assigned_to = request.user # Fallback
                        
                    adresat_nazwa = f"{faktura.assigned_to.first_name} {faktura.assigned_to.last_name}"
                    faktura.save()
                    
                    tresc_komentarza = '<i class="bi bi-check-circle-fill text-success me-1"></i> <strong>Akceptacja: Dyrektor</strong>'
                    if komentarz.strip():
                        tresc_komentarza += f"<br><span class='text-muted small'>Komentarz: {komentarz}</span>"
                        
                    DocumentHistory.objects.create(
                        document=faktura, user=request.user, previous_status=stary_status, new_status=DocumentStatus.ORDER_ACCEPTED,
                        comment=tresc_komentarza
                    )
                    
                    DocumentHistory.objects.create(
                        document=faktura, user=request.user, previous_status=DocumentStatus.ORDER_ACCEPTED, new_status=faktura.status,
                        comment=f'<i class="bi bi-robot text-primary me-1"></i> <strong>System DMS:</strong> Zamówienie zaakceptowane. Automatyczny zwrot do nadawcy: <strong>{adresat_nazwa}</strong>'
                    )
                        
                    messages.success(request, f"Zaakceptowano zamówienie. Dokument wrócił do nadawcy: {adresat_nazwa}.")
                    return redirect('moje_biurko')
                
                # --- ŚCIEŻKA DLA FAKTURY ---
                else:
                    brakowalo_ksiegowej = not getattr(faktura, 'is_accountant_approved', False)
                    faktura.is_accountant_approved = True
                    faktura.status = DocumentStatus.READY_FOR_ACCOUNTING
                    
                    # AUTOMATYCZNE DELEGOWANIE DO KSIĘGOWOŚCI FINALNEJ
                    regula = WorkflowRule.objects.filter(target_status=DocumentStatus.READY_FOR_ACCOUNTING).first()
                    if regula and regula.default_assignee:
                        faktura.assigned_to = regula.default_assignee
                        adresat_nazwa = f"{regula.default_assignee.first_name} {regula.default_assignee.last_name}"
                    else:
                        raise ValidationError("Brak przypisanej reguły obiegu dla 'Gotowe do księgowania'. Skonfiguruj reguły w panelu.")
                        
                    faktura.save()
                    
                    tresc_komentarza = '<i class="bi bi-check-circle-fill text-success me-1"></i> <strong>Akceptacja: Dyrektor</strong>'
                    if komentarz.strip():
                        tresc_komentarza += f"<br><span class='text-muted small'>Komentarz: {komentarz}</span>"
                        
                    DocumentHistory.objects.create(
                        document=faktura, user=request.user, previous_status=stary_status, new_status=DocumentStatus.DIRECTOR_APPROVAL,
                        comment=tresc_komentarza
                    )
                    
                    if brakowalo_ksiegowej:
                        DocumentHistory.objects.create(
                            document=faktura, user=request.user, previous_status=DocumentStatus.DIRECTOR_APPROVAL, new_status=DocumentStatus.READY_FOR_ACCOUNTING,
                            comment='<i class="bi bi-check-all text-success me-1"></i> <strong>Akceptacja Głównego Księgowego</strong> <span class="text-muted small">(Nadana automatycznie mocą autorytetu Dyrektora)</span>'
                        )
                    
                    DocumentHistory.objects.create(
                        document=faktura, user=request.user, previous_status=DocumentStatus.READY_FOR_ACCOUNTING, new_status=faktura.status,
                        comment=f'<i class="bi bi-robot text-primary me-1"></i> <strong>System DMS:</strong> Dokument gotowy do księgowania. Automatyczne przekazanie na biurko: <strong>{adresat_nazwa}</strong>'
                    )
                        
                    messages.success(request, f"Zapisano akceptację. Dokument jest gotowy do zaksięgowania i trafił do: {adresat_nazwa}.")
                    return redirect('moje_biurko')

            # 6. UNIWERSALNE DELEGOWANIE (Przekaż innemu użytkownikowi)
            elif akcja == 'deleguj':
                if not reczny_adresat_id:
                    raise ValidationError("Aby przekazać dokument, musisz najpierw wybrać pracownika z listy.")
                
                next_assignee = User.objects.get(id=reczny_adresat_id)
                previous_status = faktura.status
                
                # Jeśli ktoś wyciąga dokument z ogólnego Bufora, zmieniamy status na Opis (W obiegu)
                if faktura.status == DocumentStatus.BUFFER:
                    faktura.status = DocumentStatus.DESCRIPTION
                    
                faktura.assigned_to = next_assignee
                faktura.save()
                
                tresc_historii = f'<i class="bi bi-send-fill text-info me-1"></i> Przekazano dokument do: <strong>{next_assignee.first_name} {next_assignee.last_name}</strong>.'
                if komentarz.strip():
                    tresc_historii += f"<br><span class='text-muted small'>Komentarz: {komentarz}</span>"

                DocumentHistory.objects.create(
                    document=faktura,
                    user=request.user,
                    previous_status=previous_status,
                    new_status=faktura.status,
                    comment=tresc_historii
                )
                messages.success(request, f"Dokument został przekazany do: {next_assignee.first_name} {next_assignee.last_name}")
                return redirect('moje_biurko')
                
            # 7. ARCHIWIZACJA (Połączona z Auto-generowaniem Karty Obiegu)
            elif akcja == 'archiwizuj':
                if faktura.status not in [DocumentStatus.READY_FOR_ACCOUNTING, DocumentStatus.ORDER_ACCEPTED]:
                    raise ValidationError("Tylko dokumenty gotowe do księgowania (lub zamówienia) mogą trafić do archiwum.")
                
                # --- KROK 1: ZMIANA STATUSU I WPIS O ARCHIWIZACJI ---
                previous_status = faktura.status
                faktura.status = DocumentStatus.ARCHIVED
                faktura.assigned_to = None  # Zdejmujemy dokument z wirtualnego biurka
                faktura.save()
                
                tresc_historii = '<i class="bi bi-archive-fill text-secondary me-1"></i> <strong>Dokument przeniesiony do Archiwum.</strong>'
                if komentarz.strip():
                    tresc_historii += f"<br><span class='text-muted small'>Komentarz: {komentarz}</span>"
                    
                # Zapisujemy archiwizację w historii (aby ten wpis załapał się do PDF)
                DocumentHistory.objects.create(
                    document=faktura,
                    user=request.user,
                    previous_status=previous_status,
                    new_status=faktura.status,
                    comment=tresc_historii
                )
                
                # --- KROK 2: GENEROWANIE KARTY OBIEGU (PDF) ---
                czysty_numer = str(faktura.document_number).replace('/', '_').replace('\\', '_')
                nazwa_pliku = f"Karta_Obiegu_{czysty_numer}.pdf"
                
                if not faktura.attachments.filter(filename=nazwa_pliku).exists():
                    # Pobieramy pełną historię (zawiera już wpis o archiwizacji wykonany przed sekundą!)
                    historia_pelna = faktura.history.all().order_by('created_at')
                    
                    context = {
                        'faktura': faktura,
                        'historia': historia_pelna,
                        'wygenerowal': request.user,
                        'data_generacji': timezone.now(),
                    }
                    html_string = render_to_string('dokumenty/raport_historii.html', context)
                    wkhtmltopdf_path = r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe'
                    pdf_config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
                    options = {
                        'page-size': 'A4', 'margin-top': '15mm', 'margin-right': '8mm',
                        'margin-bottom': '15mm', 'margin-left': '8mm', 'encoding': "UTF-8", 'no-outline': None
                    }
                    pdf_bytes = pdfkit.from_string(html_string, False, configuration=pdf_config, options=options)
                    
                    zalacznik = DocumentAttachment(
                        document=faktura, uploaded_by=request.user, filename=nazwa_pliku
                    )
                    zalacznik.file.save(nazwa_pliku, ContentFile(pdf_bytes))
                    zalacznik.save()
                    
                    # Opcjonalny, dodatkowy wpis techniczny, że plik PDF został wygenerowany po zamknięciu dokumentu
                    DocumentHistory.objects.create(
                        document=faktura, user=request.user, previous_status=faktura.status, new_status=faktura.status,
                        comment=f'<i class="bi bi-printer text-info me-1"></i> Wygenerowano kartę obiegu (PDF) z pełną historią: <strong>{nazwa_pliku}</strong>'
                    )

                messages.success(request, "Dokument został pomyślnie zarchiwizowany, a pełna Karta Obiegu zapisana jako załącznik.")
                return redirect('moje_biurko')

            # 8. GENEROWANIE RAPORTU HISTORII OBIEGU (PDF)
            elif akcja == 'generuj_raport':
                # Blokada: Można wygenerować tylko w etapie końcowym
                if faktura.status not in [DocumentStatus.READY_FOR_ACCOUNTING, DocumentStatus.ORDER_ACCEPTED]:
                    raise ValidationError("Karta obiegu może zostać wygenerowana tylko dla dokumentów gotowych do księgowania.")
                
                czysty_numer = str(faktura.document_number).replace('/', '_').replace('\\', '_')
                nazwa_pliku = f"Karta_Obiegu_{czysty_numer}.pdf"
                
                # Zabezpieczenie przed podwójnym wygenerowaniem raportu (sprawdzamy po nazwie pliku)
                if faktura.attachments.filter(filename=nazwa_pliku).exists():
                    messages.warning(request, "Karta obiegu została już wygenerowana i znajduje się na liście załączników.")
                    return redirect('szczegoly_faktury', faktura_id=faktura.id)

                # Pobieramy pełną historię posortowaną chronologicznie
                historia_pelna = faktura.history.all().order_by('created_at')
                
                context = {
                    'faktura': faktura,
                    'historia': historia_pelna,
                    'wygenerowal': request.user,
                    'data_generacji': timezone.now(),
                }
                
                html_string = render_to_string('dokumenty/raport_historii.html', context)
                
                wkhtmltopdf_path = r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe'
                pdf_config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
                options = {
                    'page-size': 'A4',
                    'margin-top': '15mm',
                    'margin-right': '8mm',
                    'margin-bottom': '15mm',
                    'margin-left': '8mm',
                    'encoding': "UTF-8",
                    'no-outline': None
                }
                
                pdf_bytes = pdfkit.from_string(html_string, False, configuration=pdf_config, options=options)
                
                zalacznik = DocumentAttachment(
                    document=faktura,
                    uploaded_by=request.user,
                    filename=nazwa_pliku
                )
                zalacznik.file.save(nazwa_pliku, ContentFile(pdf_bytes))
                zalacznik.save()
                
                DocumentHistory.objects.create(
                    document=faktura, 
                    user=request.user, 
                    previous_status=faktura.status, 
                    new_status=faktura.status,
                    comment=f'<i class="bi bi-printer text-info me-1"></i> Wygenerowano kartę obiegu (PDF) i podpięto jako załącznik: <strong>{nazwa_pliku}</strong>'
                )
                
                messages.success(request, "Pomyślnie wygenerowano raport z historią obiegu i dodano jako załącznik.")
                return redirect('szczegoly_faktury', faktura_id=faktura.id)
            
            # 9. SZYBKA ŚCIEŻKA: AUTOMATYCZNA AKCEPTACJA (100% ZGODNOŚCI)
            elif akcja == 'auto_approve_compliant':
                if not getattr(faktura, 'is_fully_compliant', False):
                    raise ValidationError("Dokument nie jest w 100% zgodny z zamówieniem. Szybka ścieżka jest niedostępna.")
                
                if not komentarz.strip():
                    raise ValidationError("Komentarz jest bezwzględnie wymagany przy korzystaniu z opcji automatycznej akceptacji.")
                
                stary_status = faktura.status
                
                # Zaznaczamy w tle obie zgody
                faktura.is_accountant_approved = True
                faktura.is_director_approved = True
                faktura.status = DocumentStatus.READY_FOR_ACCOUNTING
                
                # Szukamy osoby przypisanej do ostatecznego księgowania
                regula = WorkflowRule.objects.filter(target_status=DocumentStatus.READY_FOR_ACCOUNTING).first()
                if regula and regula.default_assignee:
                    faktura.assigned_to = regula.default_assignee
                    adresat_nazwa = f"{regula.default_assignee.first_name} {regula.default_assignee.last_name}"
                else:
                    raise ValidationError("Brak przypisanej reguły obiegu dla 'Gotowe do księgowania'. Skonfiguruj reguły w panelu.")
                    
                faktura.save()
                
                # Wpis 1: Zapisanie akcji użytkownika
                tresc_komentarza = '<i class="bi bi-lightning-charge-fill text-warning me-1"></i> <strong>Szybka ścieżka: Pełna zgodność z zamówieniem</strong>'
                tresc_komentarza += f"<br><span class='text-muted small'>Komentarz: {komentarz}</span>"
                
                DocumentHistory.objects.create(
                    document=faktura, user=request.user, previous_status=stary_status, new_status=DocumentStatus.READY_FOR_ACCOUNTING,
                    comment=tresc_komentarza
                )
                
                # Wpis 2: Informacja systemowa o zamknięciu procedur
                DocumentHistory.objects.create(
                    document=faktura, user=request.user, previous_status=DocumentStatus.READY_FOR_ACCOUNTING, new_status=faktura.status,
                    comment=f'<i class="bi bi-robot text-primary me-1"></i> <strong>System DMS:</strong> Automatyczna akceptacja (Zastępuje Księgowość i Dyrekcję). Przekazanie na biurko: <strong>{adresat_nazwa}</strong>'
                )
                
                messages.success(request, f"Dokument pomyślnie przeszedł szybką ścieżkę! Trafił prosto na biurko: {adresat_nazwa}.")
                return redirect('moje_biurko')
            
            # 10. BLOKOWANIE DOKUMENTU
            elif akcja == 'zablokuj':
                if not komentarz.strip():
                    raise ValidationError("Musisz podać powód zablokowania dokumentu (komentarz).")
                
                faktura.status_before_block = faktura.status
                faktura.status = DocumentStatus.BLOCKED
                faktura.blocked_by = request.user
                faktura.save()
                
                DocumentHistory.objects.create(
                    document=faktura, user=request.user, previous_status=faktura.status_before_block, new_status=faktura.status,
                    comment=f'<i class="bi bi-slash-circle-fill text-danger me-1"></i> <strong>Dokument wstrzymany.</strong><br><span class="text-muted small">Komentarz: {komentarz}</span>'
                )
                messages.warning(request, f"Dokument {faktura.document_number} został oznaczony jako zablokowany.")
                return redirect('moje_biurko')


            # 11. ODBLOKOWANIE DOKUMENTU
            elif akcja == 'odblokuj':
                if faktura.blocked_by != request.user and not is_chief_accountant:
                    raise ValidationError("Odmowa: Tylko osoba blokująca lub Księgowość może odblokować ten dokument.")
                if not komentarz.strip():
                    raise ValidationError("Musisz podać powód odblokowania dokumentu (komentarz).")
                    
                stary_status = faktura.status
                faktura.status = faktura.status_before_block or DocumentStatus.BUFFER
                faktura.blocked_by = None
                faktura.status_before_block = None
                faktura.save()
                
                DocumentHistory.objects.create(
                    document=faktura, user=request.user, previous_status=stary_status, new_status=faktura.status,
                    comment=f'<i class="bi bi-unlock-fill text-success me-1"></i> <strong>Zdjęto blokadę z dokumentu.</strong><br><span class="text-muted small">Komentarz: {komentarz}</span>'
                )
                messages.success(request, f"Dokument {faktura.document_number} powrócił do obiegu.")
                return redirect('szczegoly_faktury', faktura_id=faktura.id)
            
            # 12. DODANIE TYLKO KOMENTARZA
            elif akcja == 'tylko_komentarz':
                if not komentarz.strip():
                    raise ValidationError("Komentarz nie może być pusty.")
                
                # Dodajemy wpis do historii zachowując obecny status
                DocumentHistory.objects.create(
                    document=faktura, 
                    user=request.user, 
                    previous_status=faktura.status, 
                    new_status=faktura.status,
                    comment=f'<i class="bi bi-chat-text text-secondary me-1"></i> <strong>Dodano komentarz: </strong><br><span class="text-muted small">{komentarz}</span>'
                )
                
                messages.success(request, "Twój komentarz został pomyślnie dodany do historii obiegu.")
                return redirect('szczegoly_faktury', faktura_id=faktura.id)
            
            # 13. BIEŻĄCE GENEROWANIE HISTORII OBIEGU (W NOWEJ KARCIE)
            elif akcja == 'generuj_biezacy_pdf':
                from django.http import HttpResponse # Upewnij się, że masz to zaimportowane na górze pliku
                
                # 1. Zapisujemy log do historii PRZED wygenerowaniem, żeby ten wpis też był na PDF!
                DocumentHistory.objects.create(
                    document=faktura, 
                    user=request.user, 
                    previous_status=faktura.status, 
                    new_status=faktura.status,
                    comment='<i class="bi bi-printer text-info me-1"></i> Wydruk historii obiegu (PDF).'
                )
                
                # 2. Pobieramy pełną historię (włącznie z wpisem powyżej)
                historia_pelna = faktura.history.all().order_by('created_at')
                
                # 3. Definiujemy flagę informującą, czy dokument jest jeszcze procedowany
                w_obiegu = faktura.status not in [DocumentStatus.ARCHIVED]
                
                context = {
                    'faktura': faktura,
                    'historia': historia_pelna,
                    'wygenerowal': request.user,
                    'data_generacji': timezone.now(),
                    'w_obiegu': w_obiegu, # Przekazujemy to do szablonu PDF
                }
                
                html_string = render_to_string('dokumenty/raport_historii.html', context)
                
                wkhtmltopdf_path = r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe'
                pdf_config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
                options = {
                    'page-size': 'A4', 'margin-top': '15mm', 'margin-right': '8mm',
                    'margin-bottom': '15mm', 'margin-left': '8mm', 'encoding': "UTF-8", 'no-outline': None
                }
                
                pdf_bytes = pdfkit.from_string(html_string, False, configuration=pdf_config, options=options)
                
                # 4. Zwracamy wynik bezpośrednio jako wyświetlany dokument (nie zapisujemy go w bazie jako załącznik)
                response = HttpResponse(pdf_bytes, content_type='application/pdf')
                response['Content-Disposition'] = f'inline; filename="Historia_Biezaca_{faktura.document_number.replace("/", "_")}.pdf"'
                return response
            
            
        except ValidationError as e:
            if hasattr(e, 'message'):
                messages.error(request, e.message)
            else:
                messages.error(request, e.messages[0])

    historia = faktura.history.all()
    zalaczniki = faktura.attachments.all() 

    context = {
        'faktura': faktura,
        'historia': historia,
        'zalaczniki': zalaczniki, 
        'wszyscy_uzytkownicy': wszyscy_uzytkownicy,
        'dostepne_zamowienia': dostepne_zamowienia
    }
    return render(request, 'dokumenty/szczegoly_faktury.html', context)


@login_required
def ustawienia_profilu(request):
    # Pobieramy profil użytkownika (lub tworzymy, jeśli to stare konto założone przed dodaniem motywów)
    profil, created = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        wybrany_motyw = request.POST.get('theme')
        # NOWOŚĆ: Pobieramy login CORIM z formularza
        corim_user = request.POST.get('corim_username', '')
        
        # Zabezpieczenie: sprawdzamy, czy wybrany motyw znajduje się na naszej liście dozwolonych
        dostepne_motywy = dict(UserProfile.THEME_CHOICES).keys()
        if wybrany_motyw in dostepne_motywy:
            profil.theme = wybrany_motyw
            
            # NOWOŚĆ: Zapisujemy użytkownika CORIM, obcinamy do 8 znaków i wymuszamy wielkie litery
            if corim_user:
                profil.corim_username = corim_user.strip()[:8].upper()
            else:
                profil.corim_username = ""
                
            profil.save()
            messages.success(request, "Pomyślnie zaktualizowano ustawienia profilu!")
            return redirect('ustawienia_profilu')
        else:
            messages.error(request, "Wybrano nieprawidłowy motyw.")

    context = {
        'profil': profil,
        'motywy': UserProfile.THEME_CHOICES
    }
    return render(request, 'dokumenty/ustawienia_profilu.html', context)



def wyloguj_uzytkownika(request):
    if request.method == 'POST':
        logout(request)
        messages.success(request, "Zostałeś pomyślnie wylogowany.")
    
    # Po wylogowaniu próbujemy wejść na biurko - Django automatycznie 
    # przechwyci to i przekieruje Cię na ekran logowania
    return redirect('moje_biurko')


@login_required
def sprawdz_nowe_dokumenty(request):
    """Cichy endpoint sprawdzający, czy zmienił się stan dokumentów na biurku (liczba + ostatnia modyfikacja)"""
    
    warunek = Q(assigned_to=request.user)
    moje_statusy_domyslne = WorkflowRule.objects.filter(
        default_assignee=request.user
    ).values_list('target_status', flat=True)
        
    if moje_statusy_domyslne:
        warunek = warunek | Q(status__in=moje_statusy_domyslne, assigned_to__isnull=True)
            
    faktury = Document.objects.filter(warunek).exclude(status__in=[DocumentStatus.ARCHIVED, DocumentStatus.BLOCKED])
    
    ilosc = faktury.count()
    
    # Pobieramy datę ostatnio modyfikowanego dokumentu na tym biurku
    ostatnia_zmiana = faktury.order_by('-updated_at').values_list('updated_at', flat=True).first()
    
    # Zamieniamy datę na format tekstowy (timestamp), żeby JavaScript mógł ją łatwo porówać
    czas_zmiany = ostatnia_zmiana.timestamp() if ostatnia_zmiana else 0
    
    return JsonResponse({
        'ilosc': ilosc,
        'ostatnia_zmiana': czas_zmiany
    })


@login_required
def usun_fakture(request, faktura_id):
    if request.method == 'POST':
        # ZMIANA: Pobieramy fakturę tylko po ID, bez twardego wymuszania assigned_to w zapytaniu do bazy
        faktura = get_object_or_404(Document, id=faktura_id)
        
        # WERYFIKACJA UPRAWNIEŃ: sprawdzamy, czy dokument jest bezpośrednio Twój 
        # LUB czy nie ma właściciela (leży w ogólnym Buforze, do którego masz dostęp)
        ma_uprawnienia = (faktura.assigned_to == request.user) or (faktura.assigned_to is None)
        
        if not ma_uprawnienia:
            messages.error(request, "Odmowa dostępu: Nie możesz usunąć dokumentu, który znajduje się na biurku innej osoby.")
            return redirect('moje_biurko')
        
        # Ostatnia linia obrony: sprawdzamy, czy na pewno jest w buforze
        if faktura.status == DocumentStatus.BUFFER:
            faktura.delete()
            messages.success(request, f"Dokument został bezpowrotnie usunięty z systemu.")
        else:
            messages.error(request, "Odmowa: Można usuwać tylko dokumenty znajdujące się w Buforze!")
            
    return redirect('moje_biurko')


@login_required
def dodaj_dokument(request):
    if request.method == 'POST':
        typ_dokumentu = request.POST.get('typ_dokumentu')
        numer_dokumentu = request.POST.get('numer_dokumentu')
        kontrahent_nazwa = request.POST.get('kontrahent_nazwa')
        kontrahent_nip = request.POST.get('kontrahent_nip', 'BRAK')
        data_wystawienia = request.POST.get('data_wystawienia')
        kwota_netto = request.POST.get('kwota_netto')
        waluta = request.POST.get('waluta', 'PLN').upper()
        plik = request.FILES.get('plik_pdf')

        if plik and plik.name.lower().endswith('.pdf'):
            unikalny_id = f"MANUAL-{uuid.uuid4().hex[:12].upper()}"

            nowy_dokument = Document.objects.create(
                ksef_number=unikalny_id,
                document_number=numer_dokumentu,
                issue_date=data_wystawienia,
                contractor_nip=kontrahent_nip if kontrahent_nip else "BRAK",
                contractor_name=kontrahent_nazwa,
                net_amount=kwota_netto,
                gross_amount=kwota_netto, # Podpisujemy kwotę netto pod pole brutto, by model się zapisał
                currency=waluta,
                pdf_file=plik,
                typ_dokumentu=typ_dokumentu,
                status=DocumentStatus.BUFFER,
                assigned_to=request.user
            )

            DocumentHistory.objects.create(
                document=nowy_dokument,
                user=request.user,
                new_status=DocumentStatus.BUFFER,
                comment="📥 Wprowadzono ręcznie do systemu (dodano skan PDF)."
            )

            messages.success(request, f"Pomyślnie dodano {nowy_dokument.get_typ_dokumentu_display()} nr {numer_dokumentu}.")
        else:
            messages.error(request, "Wystąpił błąd. Upewnij się, że dodano plik w formacie PDF.")

    return redirect('moje_biurko')


@login_required
def archiwum_firmowe(request):
    """Archiwum firmowe - wszystkie zarchiwizowane FAKTURY w firmie."""
    query = request.GET.get('q', '')
    
    dokumenty = Document.objects.filter(
        typ_dokumentu='FAKTURA',
        status=DocumentStatus.ARCHIVED
    )
    
    # NOWOŚĆ: Logika wyszukiwarki
    if query:
        dokumenty = dokumenty.filter(
            Q(document_number__icontains=query) |
            Q(contractor_name__icontains=query) |
            Q(contractor_nip__icontains=query)
        )
        
    dokumenty = dokumenty.order_by('-updated_at')

    context = {
        'dokumenty': dokumenty,
        'tytul': 'Archiwum Firmowe',
        'opis': 'Scentralizowany rejestr wszystkich zaksięgowanych faktur w firmie.',
        'query': query # Przekazujemy zapytanie do szablonu, by nie zniknęło z paska
    }
    return render(request, 'dokumenty/archiwum.html', context)

@login_required
def archiwum_osobiste(request):
    """Archiwum osobiste - zarchiwizowane ZAMÓWIENIA dodane przez obecnego użytkownika."""
    query = request.GET.get('q', '')
    
    dokumenty = Document.objects.filter(
        typ_dokumentu='ZAMOWIENIE',
        status=DocumentStatus.ARCHIVED,
        history__user=request.user,
        history__new_status=DocumentStatus.BUFFER
    ).distinct()
    
    # NOWOŚĆ: Logika wyszukiwarki
    if query:
        dokumenty = dokumenty.filter(
            Q(document_number__icontains=query) |
            Q(contractor_name__icontains=query) |
            Q(contractor_nip__icontains=query)
        )

    dokumenty = dokumenty.order_by('-updated_at')

    context = {
        'dokumenty': dokumenty,
        'tytul': 'Moje Archiwum Osobiste',
        'opis': 'Teczka zawierająca Twoje zamknięte i zrealizowane zamówienia.',
        'query': query
    }
    return render(request, 'dokumenty/archiwum.html', context)


def get_corim_orders(username):
    if not username:
        return []
    
    # Zakładamy, że folder corim leży w głównym katalogu projektu
    plik_zamowienia = os.path.join(settings.BASE_DIR, "corim", "FECOMMAN.DBF")
    plik_dostawcy = os.path.join(settings.BASE_DIR, "corim", "FCFOURNI.DBF")
    
    if not os.path.exists(plik_zamowienia) or not os.path.exists(plik_dostawcy):
        return []
        
    try:
        df_zam = pd.DataFrame(iter(DBF(plik_zamowienia, encoding='ANSI')))
        df_dost = pd.DataFrame(iter(DBF(plik_dostawcy, encoding='ANSI')))
        
        df_zam['CODE_FOUR'] = df_zam['CODE_FOUR'].astype(str).str.strip()
        df_dost['CODE_FOUR'] = df_dost['CODE_FOUR'].astype(str).str.strip()

        wynik = pd.merge(df_zam, df_dost, on='CODE_FOUR', how='left')
        wynik['CODE_DEM'] = wynik['CODE_DEM'].astype(str).str.strip()
        
        wynik_przefiltrowany = wynik[wynik['CODE_DEM'] == username]
        wynik_posortowany = wynik_przefiltrowany.sort_values(by='DATE_CREA', ascending=False)
        
        dane = wynik_posortowany[['DATE_CREA', 'NUM_CMDE', 'CODE_FOUR', 'LIBE_FOUR', 'MONTANT', 'CODE_DEVI_x', 'LIBE_CMDE']].head(20)
        
        orders = []
        for _, row in dane.iterrows():
            orders.append({
                'data_utworzenia': row['DATE_CREA'].strftime('%Y-%m-%d') if pd.notnull(row['DATE_CREA']) else '',
                'numer': str(row['NUM_CMDE']).strip(),
                'kod_dostawcy': str(row['CODE_FOUR']).strip(),
                'nazwa_dostawcy': str(row['LIBE_FOUR']).strip(),
                'kwota': round(float(row['MONTANT']), 2) if pd.notnull(row['MONTANT']) else 0.00,
                'waluta': str(row['CODE_DEVI_x']).strip(),
                'opis': str(row['LIBE_CMDE']).strip()
            })
        return orders
    except Exception as e:
        print(f"Błąd CORIM: {e}")
        return []
    
@login_required
def zamowienia_corim(request):
    corim_user = request.user.profile.corim_username if hasattr(request.user, 'profile') else None
    zamowienia = get_corim_orders(corim_user) if corim_user else []
    
    # Przekazujemy listę kodów znanych dostawców do JS, żeby ukryć pole NIP, jeśli już go mamy
    znani_kontrahenci = list(Contractor.objects.values_list('code', flat=True))
    
    zaimportowane_numery = list(Document.objects.filter(
        typ_dokumentu='ZAMOWIENIE',
        ksef_number__startswith='CORIM-'
    ).values_list('document_number', flat=True))
    
    
    context = {
        'zamowienia': zamowienia,
        'corim_user': corim_user,
        'znani_kontrahenci': json.dumps(znani_kontrahenci),
        'zaimportowane_numery': zaimportowane_numery
    }
    return render(request, 'dokumenty/zamowienia_corim.html', context)


@login_required
def importuj_corim(request):
    if request.method == 'POST':
        numer = request.POST.get('numer')
        data = request.POST.get('data')
        kod_dostawcy = request.POST.get('kod_dostawcy')
        nazwa_dostawcy = request.POST.get('nazwa_dostawcy')
        kwota = request.POST.get('kwota')
        waluta = request.POST.get('waluta', 'PLN').upper()
        nip = request.POST.get('nip')
        plik_pdf = request.FILES.get('plik_pdf')

        if not plik_pdf:
            messages.error(request, "Plik PDF jest wymagany do zaimportowania zamówienia.")
            return redirect('zamowienia_corim')

        # 1. Obsługa kontrahenta
        contractor = Contractor.objects.filter(code=kod_dostawcy).first()
        if not contractor:
            if not nip:
                messages.error(request, "Dla nowych kontrahentów NIP jest wymagany.")
                return redirect('zamowienia_corim')
            contractor = Contractor.objects.create(
                code=kod_dostawcy,
                name=nazwa_dostawcy,
                nip=nip
            )

        # 2. Tworzenie dokumentu DMS
        unikalny_id = f"CORIM-{numer}"
        
        # Opcjonalne zabezpieczenie przed podwójnym importem
        if Document.objects.filter(ksef_number=unikalny_id).exists():
            messages.error(request, f"Zamówienie {numer} zostało już zaimportowane wcześniej.")
            return redirect('zamowienia_corim')

        nowy_dokument = Document.objects.create(
            ksef_number=unikalny_id,
            document_number=numer,
            issue_date=data,
            contractor_nip=contractor.nip,
            contractor_name=contractor.name,
            net_amount=kwota,
            gross_amount=kwota, # Podpisujemy obie kwoty wartością z zamówienia
            currency=waluta,
            pdf_file=plik_pdf,
            typ_dokumentu='ZAMOWIENIE',
            status=DocumentStatus.BUFFER,
            assigned_to=request.user
        )

        DocumentHistory.objects.create(
            document=nowy_dokument,
            user=request.user,
            new_status=DocumentStatus.BUFFER,
            comment=f"📥 Zaimportowano z systemu CORIM (Dostawca: {contractor.code})."
        )
        
        messages.success(request, f"Pomyślnie zaimportowano zamówienie {numer} z CORIM.")
        return redirect('moje_biurko')
    return redirect('zamowienia_corim')    


@login_required
def wyszukiwarka_ksef(request):
    
    has_ksef_access = WorkflowRule.objects.filter(
        target_status__in=[DocumentStatus.READY_FOR_ACCOUNTING, DocumentStatus.ACCOUNTANT_APPROVAL],
        default_assignee=request.user
    ).exists()
    
    if not has_ksef_access:
        messages.error(request, "Odmowa dostępu: Wyszukiwarka KSeF jest zarezerwowana wyłącznie dla działu Księgowości.")
        return redirect('moje_biurko')
    
    
    data_od = request.GET.get('data_od')
    data_do = request.GET.get('data_do')
    wyniki = []
    blad = None
    
    # Pobieramy numery KSeF faktur, które już są w naszej bazie, aby je oznaczyć
    istniejace_ksef = set(Document.objects.values_list('ksef_number', flat=True))

    if data_od and data_do:
        try:
            # KSeF API wymaga daty w formacie z odpowiednią strefą czasową[cite: 9]
            ksef_data_od = f"{data_od}T00:00:00+02:00"
            ksef_data_do = f"{data_do}T23:59:59+02:00"

            access_token = login.login2ksef()
            
            if not access_token:
                blad = "Nie udało się zalogować do KSeF (brak tokena)."
            else:
                headers_json = {
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
                
                # Zmieniono URL na ten z Twojego skryptu[cite: 9]
                base_url = "https://api-demo.ksef.mf.gov.pl/v2" 
                query_url = f"{base_url}/invoices/query/metadata"
                
                query_payload = {
                    "subjectType": "Subject2", # Szukamy faktur kosztowych (jako Nabywca)[cite: 9]
                    "dateRange": {
                        "dateType": "Invoicing",
                        "from": ksef_data_od,
                        "to": ksef_data_do
                    }
                }
                
                resp = requests.post(query_url, json=query_payload, headers=headers_json)
                
                if resp.status_code == 200:
                    data = resp.json()
                    faktury_ksef = data.get('items', data.get('content', data.get('invoices', [])))
                    
                    for inv in faktury_ksef:
                        nr_ksef = inv.get('ksefNumber', inv.get('ksefReferenceNumber'))
                        
                        if nr_ksef in istniejace_ksef:
                            continue
                        
                        # 1. Poprawka daty
                        surowa_data = str(inv.get('invoicingDate', 'Brak daty'))
                        data_wystawienia = surowa_data.split('T')[0] if 'T' in surowa_data else surowa_data[:10]
                        
                        # 2. OSTATECZNE ROZWIĄZANIE: Pobieramy dane z obiektu "seller"
                        seller = inv.get('seller', {})
                        nip_wystawcy = seller.get('nip', '')
                        nazwa_kontrahenta = seller.get('name', '').strip()
                        
                        # Zabezpieczenie: jeśli KSeF wyśle pustą nazwę, sprawdzamy po NIP w naszej bazie
                        if not nazwa_kontrahenta:
                            if nip_wystawcy:
                                znany_kontrahent = Contractor.objects.filter(nip=nip_wystawcy).first()
                                nazwa_kontrahenta = znany_kontrahent.name if znany_kontrahent else f"NIP: {nip_wystawcy}"
                            else:
                                nazwa_kontrahenta = "Brak danych wystawcy"
                                    
                        # 3. Wyciąganie waluty
                        waluta = inv.get('currency', inv.get('currencyCode', 'PLN'))
                            
                        wyniki.append({
                            'ksef_number': nr_ksef,
                            'invoice_number': inv.get('invoiceNumber', 'Brak nr'),
                            'issue_date': data_wystawienia,
                            'contractor_name': nazwa_kontrahenta,
                            'currency': waluta,
                            'net_amount': inv.get('net', inv.get('netAmount', 0.0)),
                            # 'is_downloaded': nr_ksef in istniejace_ksef
                        })
                else:
                    blad = f"Błąd API KSeF: {resp.status_code} - {resp.text}"
                    
        except Exception as e:
            blad = f"Wystąpił błąd komunikacji z KSeF: {str(e)}"

    numery_do_pobrania = ",".join([w['ksef_number'] for w in wyniki])
    
    context = {
        'wyniki': wyniki,
        'data_od': data_od,
        'data_do': data_do,
        'blad': blad,
        'numery_do_pobrania': numery_do_pobrania
    }
    return render(request, 'dokumenty/wyszukiwarka_ksef.html', context)

@login_required
def importuj_zbiorczo_ksef(request):
    has_ksef_access = WorkflowRule.objects.filter(
        target_status__in=[DocumentStatus.READY_FOR_ACCOUNTING, DocumentStatus.ACCOUNTANT_APPROVAL],
        default_assignee=request.user
    ).exists()
    
    if not has_ksef_access:
        messages.error(request, "Odmowa dostępu: Wyszukiwarka KSeF jest zarezerwowana wyłącznie dla działu Księgowości.")
        return redirect('moje_biurko')
    
    if request.method == 'POST':
        numery_ksef_str = request.POST.get('numery_ksef', '')
        numery_ksef = [n for n in numery_ksef_str.split(',') if n.strip()]
        
        if not numery_ksef:
            messages.warning(request, "Brak dokumentów do zaimportowania.")
            return redirect('wyszukiwarka_ksef')

        access_token = login.login2ksef()
        if not access_token:
            messages.error(request, "Błąd logowania do KSeF.")
            return redirect('wyszukiwarka_ksef')

        headers_xml = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/octet-stream"
        }
        
        # Konfiguracja środowiska jak w Twoim skrypcie
        base_url = "https://api-demo.ksef.mf.gov.pl/v2"
        baza_katalog = "Robocze_Wyszukiwarka"
        os.makedirs(f"{baza_katalog}/XML", exist_ok=True)
        os.makedirs(f"{baza_katalog}/HTML", exist_ok=True)
        os.makedirs(f"{baza_katalog}/PDF", exist_ok=True)
        
        try:
            xslt_root = etree.parse("FA3.xsl")
            transformator = etree.XSLT(xslt_root)
            wkhtmltopdf_path = r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe'
            pdf_config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
        except Exception as e:
            messages.error(request, f"Błąd inicjalizacji XSLT/PDFKit: {e}")
            return redirect('wyszukiwarka_ksef')
        
        zaimportowano = 0
        
        for ksef_num in numery_ksef:
            if Document.objects.filter(ksef_number=ksef_num).exists():
                continue
                
            sciezka_xml = f"{baza_katalog}/XML/{ksef_num}.xml"
            sciezka_html = f"{baza_katalog}/HTML/{ksef_num}.html"
            sciezka_pdf = f"{baza_katalog}/PDF/{ksef_num}.pdf"
            
            try:
                # 1. Pobieranie XML z KSeF
                resp_xml = requests.get(f"{base_url}/invoices/ksef/{ksef_num}", headers=headers_xml)
                if resp_xml.status_code == 200:
                    with open(sciezka_xml, "wb") as f:
                        f.write(resp_xml.content)
                else:
                    continue
                    
                # 2. Generowanie HTML i dodawanie kodu QR
                dom_xml = etree.parse(sciezka_xml)
                html_dom = transformator(dom_xml)
                with open(sciezka_html, "wb") as f:
                    f.write(etree.tostring(html_dom, pretty_print=True, encoding="utf-8"))
                    
                qr_code.dodaj_qrcode_do_faktury_v2(sciezka_xml, sciezka_html, sciezka_html, ksef_num)
                
                # 3. Konwersja do PDF
                pdfkit.from_file(sciezka_html, sciezka_pdf, configuration=pdf_config, options={
                    'page-size': 'A4', 'margin-top': '15mm', 'margin-right': '8mm', 
                    'margin-bottom': '15mm', 'margin-left': '8mm', 'encoding': "UTF-8", 'quiet': ''
                })
                
                # 4. Parsowanie danych do bazy
                def pobierz_z_xml(xpath_str):
                    el = dom_xml.xpath(xpath_str)
                    return el[0].text.strip() if el and el[0].text else None

                nr_faktury = pobierz_z_xml('//*[local-name()="P_2"]') or f"BRAK_NR_{ksef_num[-5:]}"
                data_wyst = pobierz_z_xml('//*[local-name()="P_1"]') or datetime.now().strftime("%Y-%m-%d")
                nip = pobierz_z_xml('//*[local-name()="Podmiot1"]//*[local-name()="NIP"]') or 'BRAK_NIP'
                nazwa = pobierz_z_xml('//*[local-name()="Podmiot1"]//*[local-name()="Nazwa"]') or \
                        pobierz_z_xml('//*[local-name()="Podmiot1"]//*[local-name()="PelnaNazwa"]') or 'BRAK_NAZWY'
                
                if nip != 'BRAK_NIP':
                    # Sprawdzamy, czy kontrahent o tym NIP już istnieje w naszym słowniku
                    if not Contractor.objects.filter(nip=nip).exists():
                        # Jeśli nie, tworzymy nowego z zastępczym kodem
                        Contractor.objects.create(
                            code=f"------{nip}",
                            name=nazwa[:255], # Zabezpieczenie przed zbyt długą nazwą z XML
                            nip=nip
                        )
                            
                try: brutto = float(pobierz_z_xml('//*[local-name()="P_15"]') or "0.0")
                except ValueError: brutto = 0.0
                
                try: netto = float(pobierz_z_xml('//*[local-name()="P_13_1"]') or "0.0")
                except ValueError: netto = 0.0
                
                waluta = pobierz_z_xml('//*[local-name()="KodWaluty"]') or "PLN"
                
                # Zapisujemy dokument w systemie
                nowa_faktura = Document(
                    ksef_number=ksef_num,
                    document_number=nr_faktury,
                    issue_date=data_wyst,
                    contractor_nip=nip,
                    contractor_name=nazwa,
                    net_amount=netto,
                    gross_amount=brutto,
                    currency=waluta,
                    typ_dokumentu='FAKTURA',
                    status=DocumentStatus.BUFFER,
                    assigned_to=request.user # Dokument trafia od razu na biurko osoby pobierającej
                )
                
                with open(sciezka_pdf, 'rb') as pdf_file:
                    nowa_faktura.pdf_file.save(f"{ksef_num}.pdf", File(pdf_file), save=False)
                    
                nowa_faktura.save()
                
                DocumentHistory.objects.create(
                    document=nowa_faktura,
                    user=request.user,
                    new_status=DocumentStatus.BUFFER,
                    comment="📥 Faktura pobrana na żądanie z Wyszukiwarki KSeF."
                )
                zaimportowano += 1
                
            except Exception as e:
                print(f"Błąd importu KSeF {ksef_num}: {e}")
                
        messages.success(request, f"Pomyślnie zaimportowano {zaimportowano} dokument(ów) na Twoje Biurko!")
        return redirect('moje_biurko')
    
    
@login_required
def dokumenty_zablokowane(request):
    """Widok listy dokumentów zablokowanych (wstrzymanych)"""
    query = request.GET.get('q', '')
    
    has_global_view = WorkflowRule.objects.filter(
        target_status__in=[
            DocumentStatus.READY_FOR_ACCOUNTING,
            DocumentStatus.ACCOUNTANT_APPROVAL,
            DocumentStatus.DIRECTOR_APPROVAL
        ],
        default_assignee=request.user
    ).exists()

    if has_global_view:
        # Główny Księgowy widzi wszystkie zablokowane
        faktury = Document.objects.filter(status=DocumentStatus.BLOCKED)
    else:
        # Zwykły użytkownik widzi tylko te, które sam zablokował
        faktury = Document.objects.filter(status=DocumentStatus.BLOCKED, blocked_by=request.user)

    if query:
        faktury = faktury.filter(
            Q(document_number__icontains=query) |
            Q(contractor_name__icontains=query) |
            Q(contractor_nip__icontains=query)
        )

    faktury = faktury.order_by('-updated_at')

    context = {
        'faktury': faktury,
        'query': query
    }
    return render(request, 'dokumenty/zablokowane.html', context)    