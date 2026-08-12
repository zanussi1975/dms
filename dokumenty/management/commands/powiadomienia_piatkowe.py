from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth import get_user_model
from dokumenty.models import Document, DocumentStatus

User = get_user_model()

class Command(BaseCommand):
    help = 'Wysyła mailowe podsumowanie o zalegających fakturach na biurkach użytkowników'

    def handle(self, *args, **kwargs):
        # Pobieramy wszystkich aktywnych użytkowników, którzy mają podany adres email
        uzytkownicy = User.objects.filter(is_active=True).exclude(email='')
        
        wyslane = 0
        for user in uzytkownicy:
            # Liczymy dokumenty przypisane do tego użytkownika (z pominięciem archiwum i zablokowanych)
            zalegle_faktury = Document.objects.filter(
                assigned_to=user
            ).exclude(
                status__in=[DocumentStatus.ARCHIVED, DocumentStatus.BLOCKED]
            ).count()
            
            if zalegle_faktury > 0:
                temat = f"DMS Poland - Masz {zalegle_faktury} dokumenty do przetworzenia"
                tresc = (
                    f"Cześć {user.first_name},\n\n"
                    f"Przypominamy, że na Twoim Wirtualnym Biurku w systemie DMS Poland "
                    f"znajduje się obecnie {zalegle_faktury} dokumentów oczekujących na Twoją akcję.\n\n"
                    f"Zaloguj się do systemu, aby je przetworzyć.\n\n"
                    f"Pozdrawiamy,\nSystem DMS Poland"
                )
                
                try:
                    send_mail(
                        subject=temat,
                        message=tresc,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[user.email],
                        fail_silently=False,
                    )
                    wyslane += 1
                    self.stdout.write(self.style.SUCCESS(f'Wysłano przypomnienie do: {user.email}'))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'Błąd wysyłki do {user.email}: {e}'))
                    
        self.stdout.write(self.style.SUCCESS(f'Zakończono. Wysłano łącznie {wyslane} powiadomień.'))