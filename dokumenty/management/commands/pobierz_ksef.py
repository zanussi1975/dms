import os
import requests
from lxml import etree
import pdfkit
from datetime import datetime, timedelta

# Importy środowiska Django
from django.core.management.base import BaseCommand
from django.core.files import File
from dokumenty.models import Document, DocumentHistory, DocumentStatus, DocumentType


# Założenie: pliki login.py oraz qr_code.py znajdują się w tym samym folderze,
# lub w głównym folderze projektu (skąd Python może je zaimportować).
import login 
import qr_code
 
class Command(BaseCommand):
    help = 'Pobiera faktury kosztowe z KSeF, generuje PDF i zapisuje do bazy DMS'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Konfiguracja z Twojego skryptu
        self.BASE_URL = "https://api-demo.ksef.mf.gov.pl/v2"
        self.XSLT_FILE_PATH = "FA3.xsl"
        self.WKHTMLTOPDF_PATH = r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe'

    def przygotuj_foldery(self, nazwa_okresu):
        """Tworzy strukturę katalogów dla danego miesiąca (pliki tymczasowe)."""
        foldery = [
            f"{nazwa_okresu}/XML",
            f"{nazwa_okresu}/HTML",
            f"{nazwa_okresu}/PDF"
        ]
        for folder in foldery:
            os.makedirs(folder, exist_ok=True)
        return nazwa_okresu

    def konwertuj_do_pdf(self, plik_html, plik_pdf):
        """Cicha konwersja HTML do PDF."""
        konfiguracja = pdfkit.configuration(wkhtmltopdf=self.WKHTMLTOPDF_PATH)
        opcje = {
            'page-size': 'A4',
            'margin-top': '15mm',
            'margin-right': '15mm',
            'margin-bottom': '15mm',
            'margin-left': '15mm',
            'encoding': "UTF-8",
            'quiet': ''
        }
        pdfkit.from_file(plik_html, plik_pdf, configuration=konfiguracja, options=opcje)

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING("Logowanie do KSeF..."))
        access_token = login.login2ksef()

        headers_json = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        headers_xml = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/octet-stream"
        }

        # Ustalanie ram czasowych (poprzedni miesiąc)
        dzisiaj = datetime.now()
        pierwszy_dzien_biezacego = dzisiaj.replace(day=1)
        ostatni_dzien_poprzedniego = pierwszy_dzien_biezacego - timedelta(days=1)
        pierwszy_dzien_poprzedniego = ostatni_dzien_poprzedniego.replace(day=1)
        
        data_od = pierwszy_dzien_poprzedniego.strftime("%Y-%m-%dT00:00:00+02:00")
        data_do = ostatni_dzien_poprzedniego.strftime("%Y-%m-%dT23:59:59+02:00")
        nazwa_folderu = f"Faktury_Robocze_{pierwszy_dzien_poprzedniego.strftime('%Y_%m')}"

        self.stdout.write(f"Okres: {data_od} do {data_do}")
        baza_katalog = self.przygotuj_foldery(nazwa_folderu)
        
        # Ładowanie XSLT
        try:
            xslt_root = etree.parse(self.XSLT_FILE_PATH)
            transformator = etree.XSLT(xslt_root)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Błąd ładowania pliku XSLT: {e}"))
            return

        # 1. Wyszukiwanie faktur (Nabywca)
        query_url = f"{self.BASE_URL}/invoices/query/metadata"
        query_payload = {
            "subjectType": "Subject2", 
            "dateRange": {
                "dateType": "Invoicing",
                "from": data_od,
                "to": data_do
            }
        }
        
        resp_query = requests.post(query_url, json=query_payload, headers=headers_json)
        if resp_query.status_code != 200:
            self.stdout.write(self.style.ERROR(f"Błąd API KSeF: {resp_query.text}"))
            return
            
        data = resp_query.json()
        invoice_list = data.get('items', data.get('content', data.get('invoices', [])))
        
        if not invoice_list:
            self.stdout.write(self.style.SUCCESS("Brak faktur w zadanym okresie."))
            return
            
        self.stdout.write(f"Znaleziono faktur: {len(invoice_list)}. Zaczynam przetwarzanie i zapis do bazy...\n")
        
        # 2. Pętla przetwarzająca
        for idx, invoice in enumerate(invoice_list, start=1):
            ksef_num = invoice.get('ksefNumber', invoice.get('ksefReferenceNumber'))
            
            # Sprawdzamy, czy faktura już jest w naszej bazie Django
            if Document.objects.filter(ksef_number=ksef_num).exists():
                self.stdout.write(f"[{idx}/{len(invoice_list)}] Pomijam {ksef_num} - już w bazie.")
                continue
                
            sciezka_xml = f"{baza_katalog}/XML/{ksef_num}.xml"
            sciezka_html = f"{baza_katalog}/HTML/{ksef_num}.html"
            sciezka_pdf = f"{baza_katalog}/PDF/{ksef_num}.pdf"
            
            self.stdout.write(f"[{idx}/{len(invoice_list)}] Pobieram XML i generuję PDF dla: {ksef_num}")
            
            try:
                # --- A. Pobieranie z KSeF ---
                url_pobierz = f"{self.BASE_URL}/invoices/ksef/{ksef_num}"
                resp_xml = requests.get(url_pobierz, headers=headers_xml)
                
                if resp_xml.status_code == 200:
                    with open(sciezka_xml, "wb") as f:
                        f.write(resp_xml.content)
                else:
                    self.stdout.write(self.style.ERROR(f"   -> Błąd pobierania XML: {resp_xml.status_code}"))
                    continue
                    
                # --- B. Konwersja i kody QR ---
                dom_xml = etree.parse(sciezka_xml)
                html_dom = transformator(dom_xml)
                with open(sciezka_html, "wb") as f:
                    f.write(etree.tostring(html_dom, pretty_print=True, encoding="utf-8"))
                
                qr_code.dodaj_qrcode_do_faktury_v2(sciezka_xml, sciezka_html, sciezka_html, ksef_num)
                self.konwertuj_do_pdf(sciezka_html, sciezka_pdf)
                
                # --- C. ZAPIS DO BAZY DJANGO ---
                
                # Zamiast zgadywać strukturę JSON, wyciągamy dane z oficjalnego XMLa (standard FA)
                def pobierz_z_xml(xpath_str):
                    elementy = dom_xml.xpath(xpath_str)
                    return elementy[0].text.strip() if elementy and elementy[0].text else None

                # P_2 to oficjalne pole KSeF na Numer Faktury, P_1 to Data wystawienia
                nr_faktury = pobierz_z_xml('//*[local-name()="P_2"]') or f"BRAK_NR_{idx}"
                data_wyst = pobierz_z_xml('//*[local-name()="P_1"]') or datetime.now().strftime("%Y-%m-%d")
                
                # Podmiot1 to Sprzedawca (Wystawca). Szukamy NIP i Nazwy
                nip = pobierz_z_xml('//*[local-name()="Podmiot1"]//*[local-name()="NIP"]') or 'BRAK_NIP'
                
                nazwa = pobierz_z_xml('//*[local-name()="Podmiot1"]//*[local-name()="Nazwa"]')
                if not nazwa:
                    # Czasami firmy wpisują się w tag <PelnaNazwa> zamiast <Nazwa>
                    nazwa = pobierz_z_xml('//*[local-name()="Podmiot1"]//*[local-name()="PelnaNazwa"]') or 'BRAK_NAZWY'

                # P_15 to Kwota Należności Ogółem Brutto
                brutto_str = pobierz_z_xml('//*[local-name()="P_15"]') or "0.0"
                try:
                    brutto = float(brutto_str)
                except ValueError:
                    brutto = 0.0
                    
                # Próbujemy pobrać Netto z JSON. Jeśli go brak -> próbujemy wyciągnąć stawkę główną (P_13_1) z XML
                netto = invoice.get('net', invoice.get('netAmount', 0.0))
                if float(netto) == 0.0:
                    netto_str = pobierz_z_xml('//*[local-name()="P_13_1"]') or "0.0"
                    try:
                        netto = float(netto_str)
                    except ValueError:
                        netto = 0.0
                        
                waluta_z_xml = pobierz_z_xml('//*[local-name()="KodWaluty"]') or "PLN"

                # Tworzymy obiekt Django
                nowa_faktura = Document(
                    ksef_number=ksef_num,
                    document_number=nr_faktury,
                    issue_date=data_wyst,
                    contractor_nip=nip,
                    contractor_name=nazwa,
                    net_amount=netto,
                    gross_amount=brutto,
                    currency=waluta_z_xml,
                    typ_dokumentu=DocumentType.FAKTURA,
                    status=DocumentStatus.BUFFER
                )
                
                # Zapisujemy wygenerowany plik PDF bezpośrednio do rekordu w Django
                with open(sciezka_pdf, 'rb') as plik_pdf_dla_django:
                    nowa_faktura.pdf_file.save(f"{ksef_num}.pdf", File(plik_pdf_dla_django), save=False)
                
                nowa_faktura.save()
                
                # Tworzymy ślad rewizyjny (Audit Trail)
                DocumentHistory.objects.create(
                    document=nowa_faktura,
                    user=None, 
                    previous_status=None,
                    new_status=DocumentStatus.BUFFER,
                    comment="Faktura pobrana automatycznie przez skrypt z KSeF."
                )

                self.stdout.write(self.style.SUCCESS(f"   -> Zapisano w bazie: {nr_faktury} | {nazwa} | {brutto} {waluta_z_xml}"))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"   -> Błąd przetwarzania faktury {ksef_num}: {e}"))

        self.stdout.write(self.style.SUCCESS("\nZakończono pobieranie i import do systemu obiegu!"))