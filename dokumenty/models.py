from django.db import models, transaction
from django.conf import settings
from django.core.exceptions import ValidationError
import os
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class DocumentStatus(models.TextChoices):
    BUFFER = 'BUFFER', 'Bufor'
    DESCRIPTION = 'DESCRIPTION', 'Opis merytoryczny (Pracownik)'
    ACCOUNTANT_APPROVAL = 'ACCOUNTANT_APPROVAL', 'Akceptacja Głównej Księgowej'
    DIRECTOR_APPROVAL = 'DIRECTOR_APPROVAL', 'Akceptacja Dyrektora'
    READY_FOR_ACCOUNTING = 'READY_FOR_ACCOUNTING', 'Gotowe do księgowania'
    ARCHIVED = 'ARCHIVED', 'Zarchiwizowane'
    ORDER_ACCEPTED = 'ORDER_ACCEPTED', 'Zamówienie zaakceptowane'
    REJECTED = 'REJECTED', 'Odrzucona' 
    BLOCKED = 'BLOCKED', 'Zablokowane'

class DocumentType(models.TextChoices):
    FAKTURA = 'FAKTURA', 'Faktura'
    ZAMOWIENIE = 'ZAMOWIENIE', 'Zamówienie'


class Document(models.Model):
    # Dane podstawowe z KSeF
    ksef_number = models.CharField(max_length=255, unique=True, verbose_name="Numer KSeF")
    document_number = models.CharField(max_length=255, verbose_name="Numer dokumentu")
    issue_date = models.DateField(verbose_name="Data wystawienia")
    contractor_nip = models.CharField(max_length=20, verbose_name="NIP Kontrahenta")
    contractor_name = models.CharField(max_length=255, verbose_name="Nazwa Kontrahenta")
    net_amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Kwota netto")
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Kwota brutto")
    currency = models.CharField(max_length=3, default='PLN', verbose_name="Waluta")
    
    # Plik (worker KSeF generuje PDF - możemy przechowywać ścieżkę)
    pdf_file = models.FileField(upload_to='documents_pdfs/', null=True, blank=True, verbose_name="Plik PDF")
    
    # NOWOŚĆ: Relacja do zamówienia
    related_order = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={'typ_dokumentu': 'ZAMOWIENIE'},
        related_name='linked_invoices',
        verbose_name="Powiązane zamówienie"
    )

    paired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='paired_documents',
        verbose_name="Sparowano przez"
    )
    
    @property
    def is_fully_compliant(self):
        """Sprawdza, czy faktura ma podpięte zamówienie i kwoty netto są identyczne."""
        if self.related_order and self.typ_dokumentu == DocumentType.FAKTURA:
            kwota_zgodna = self.net_amount == self.related_order.net_amount
            waluta_zgodna = self.currency == self.related_order.currency
            
            return kwota_zgodna and waluta_zgodna
        return False

    typ_dokumentu = models.CharField(
        max_length=20,
        choices=DocumentType.choices,
        default=DocumentType.FAKTURA
    )

    # Workflow / Wirtualne Biurka
    status = models.CharField(
        max_length=50,
        choices=DocumentStatus.choices,
        default=DocumentStatus.BUFFER,
        verbose_name="Status obiegu"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, # Jeśli usuniemy usera, faktura nie znika
        null=True,
        blank=True,
        related_name='assigned_invoices',
        verbose_name="Przypisano do (Wirtualne biurko)"
    )
    
    blocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='blocked_documents',
        verbose_name="Zablokowane przez"
    )
    status_before_block = models.CharField(
        max_length=50,
        choices=DocumentStatus.choices,
        null=True,
        blank=True,
        verbose_name="Status przed blokadą"
    )

    # Metadane techniczne
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data utworzenia")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Data ostatniej zmiany")

    is_accountant_approved = models.BooleanField(default=False, verbose_name="Akceptacja Księgowości")
    is_director_approved = models.BooleanField(default=False, verbose_name="Akceptacja Dyrekcji")
    
    class Meta:
        verbose_name = "Faktura"
        verbose_name_plural = "Faktury"
        ordering = ['-created_at']

    def __str__(self):
        rodzaj = "Faktura" if self.typ_dokumentu == 'FAKTURA' else "Zamówienie"
        return f"{rodzaj} nr {self.document_number} ({self.get_status_display()})"
    
    def change_status(self, new_status, user, comment="", next_assignee=None):
        # 1. POPRAWKA: Przerywamy działanie TYLKO wtedy, gdy nie zmienia się ani status, ani adresat
        if self.status == new_status and self.assigned_to == next_assignee:
            return

        # Definicja dozwolonych ścieżek
        dozwolone_przejscia = {
            DocumentStatus.BUFFER: [DocumentStatus.DESCRIPTION, DocumentStatus.DIRECTOR_APPROVAL, DocumentStatus.REJECTED],
            DocumentStatus.DESCRIPTION: [DocumentStatus.ACCOUNTANT_APPROVAL, DocumentStatus.REJECTED],
            DocumentStatus.ACCOUNTANT_APPROVAL: [DocumentStatus.DIRECTOR_APPROVAL, DocumentStatus.READY_FOR_ACCOUNTING, DocumentStatus.REJECTED],
            DocumentStatus.DIRECTOR_APPROVAL: [DocumentStatus.READY_FOR_ACCOUNTING, DocumentStatus.ORDER_ACCEPTED, DocumentStatus.REJECTED],
            # NOWOŚĆ: z księgowości / akceptacji zamówienia możemy wysłać do archiwum
            DocumentStatus.READY_FOR_ACCOUNTING: [DocumentStatus.ARCHIVED, DocumentStatus.REJECTED],
            DocumentStatus.ORDER_ACCEPTED: [DocumentStatus.ARCHIVED, DocumentStatus.REJECTED], 
            DocumentStatus.REJECTED: [
                DocumentStatus.DESCRIPTION, 
                DocumentStatus.ACCOUNTANT_APPROVAL, 
                DocumentStatus.DIRECTOR_APPROVAL,
                DocumentStatus.REJECTED
            ],
        }

        # Zabezpieczenie przed błędnymi skokami
        if new_status not in dozwolone_przejscia.get(self.status, []):
            raise ValidationError(f"Niedozwolone przejście: nie można zmienić statusu z {self.status} na {new_status}")

        previous_status = self.status
        self.status = new_status
        if next_assignee:
            self.assigned_to = next_assignee
        self.save()

        # Tworzenie wpisu w historii
        DocumentHistory.objects.create(
            document=self,
            user=user,
            previous_status=previous_status,
            new_status=new_status,
            comment=comment
        )
        
    def evaluate_final_status(self, user):
        """
        Sprawdza, czy dokument zebrał wymagane zgody i jeśli tak, 
        automatycznie zmienia jego status główny na gotowy do księgowania.
        """
        if self.is_accountant_approved and self.is_director_approved and self.status != DocumentStatus.READY_FOR_ACCOUNTING:
            previous_status = self.status
            self.status = DocumentStatus.READY_FOR_ACCOUNTING
            self.save()
            
            # Dodajemy wpis do historii o automatycznej zmianie statusu
            DocumentHistory.objects.create(
                document=self,
                user=user,
                previous_status=previous_status,
                new_status=self.status,
                comment='<i class="bi bi-robot text-primary me-1"></i> <strong>System:</strong> Skompletowano wszystkie wymagane akceptacje. Dokument gotowy do zaksięgowania.'
            )

class DocumentHistory(models.Model):
    """Audit Trail - ślad rewizyjny dla każdej zmiany statusu"""
    document = models.ForeignKey(
        Document, 
        on_delete=models.CASCADE, 
        related_name='history', 
        verbose_name="Dokument"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        verbose_name="Użytkownik"
    )
    previous_status = models.CharField(
        max_length=50, 
        choices=DocumentStatus.choices, 
        verbose_name="Poprzedni status",
        null=True, # Null dla momentu wpadnięcia do Bufora
        blank=True
    )
    new_status = models.CharField(
        max_length=50, 
        choices=DocumentStatus.choices, 
        verbose_name="Nowy status"
    )
    comment = models.TextField(
        blank=True, 
        verbose_name="Komentarz / Uzasadnienie odrzucenia"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data zmiany")

    class Meta:
        verbose_name = "Historia zmian dokumentu"
        verbose_name_plural = "Historie zmian dokumentu"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.document.document_number}: {self.previous_status} -> {self.new_status}"
    
    
class WorkflowRule(models.Model):
    """Konfiguracja domyślnych ścieżek akceptacji zarządzana przez Admina"""
    target_status = models.CharField(
        max_length=50, 
        choices=DocumentStatus.choices, 
        unique=True, 
        verbose_name="Status docelowy"
    )
    default_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Domyślny pracownik"
    )

    class Meta:
        verbose_name = "Reguła obiegu"
        verbose_name_plural = "Reguły obiegu"

    def __str__(self):
        return f"Domyślny dla {self.get_target_status_display()}: {self.default_assignee}"
    
    
class DocumentAttachment(models.Model):
    """Model przechowujący załączniki do faktury"""
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='attachments', verbose_name="Dokument")
    file = models.FileField(upload_to='documents_attachments/', verbose_name="Plik PDF")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    filename = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Załącznik"
        verbose_name_plural = "Załączniki"
        ordering = ['-uploaded_at']

    def save(self, *args, **kwargs):
        # Automatyczne wyciąganie nazwy pliku z wgrywanego pliku
        if self.file and not self.filename:
            self.filename = os.path.basename(self.file.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Załącznik: {self.filename}"
    
    


class UserProfile(models.Model):
    """Profil użytkownika przechowujący preferencje (np. motyw graficzny)"""
    THEME_CHOICES = [
        ('mint', 'Miętowa Świeżość (Domyślny)'),
        ('corporate', 'Korporacyjny Błękit'),
        ('warm', 'Ciepły Minimalizm'),
        ('dark', 'Soft Dark (Ciemny)'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    theme = models.CharField(max_length=20, choices=THEME_CHOICES, default='mint')

    corim_username = models.CharField(
        max_length=8, 
        blank=True, 
        null=True, 
        verbose_name="Użytkownik CORIM"
    )

    can_approve_accountant = models.BooleanField(default=False, verbose_name="Może akceptować jako Księgowość")
    can_approve_director = models.BooleanField(default=False, verbose_name="Może akceptować jako Dyrekcja")
    
    def __str__(self):
        return f"Profil: {self.user.username} [Motyw: {self.theme}]"

# Automatyczne tworzenie profilu dla każdego nowo powstałego użytkownika
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance, theme='mint')

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    # Bezpieczne zapisywanie profilu, jeśli istnieje
    if hasattr(instance, 'profile'):
        instance.profile.save()
    
    
class Contractor(models.Model):
    """Słownik kontrahentów z zewnętrznego systemu CORIM"""
    code = models.CharField(max_length=50, unique=True, verbose_name="Kod dostawcy (CORIM)")
    name = models.CharField(max_length=255, verbose_name="Nazwa dostawcy")
    nip = models.CharField(max_length=20, verbose_name="NIP")

    class Meta:
        verbose_name = "Kontrahent"
        verbose_name_plural = "Kontrahenci"

    def __str__(self):
        return f"{self.name} ({self.nip})"    


class SystemNote(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        verbose_name="Zgłaszający"
    )
    content = models.TextField(verbose_name="Treść uwagi / pomysłu")
    admin_response = models.TextField(blank=True, null=True, verbose_name="Odpowiedź administratora")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data zgłoszenia")
    responded_at = models.DateTimeField(blank=True, null=True, verbose_name="Data odpowiedzi")
    is_resolved = models.BooleanField(default=False, verbose_name="Rozwiązane")

    class Meta:
        verbose_name = "Uwaga do systemu"
        verbose_name_plural = "Uwagi do systemu"
        ordering = ['-created_at']

    def __str__(self):
        return f"Zgłoszenie od {self.user.username} ({self.created_at.strftime('%Y-%m-%d')})"