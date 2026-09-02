import qrcode
import base64
import hashlib
from io import BytesIO
from lxml import etree
from datetime import datetime
from dotenv import load_dotenv
import os


load_dotenv()

QR_URL = os.getenv("KSEF_QR_URL") 

def wylicz_skrot_ksef_2_0(sciezka_xml):
    """Wylicza skrót weryfikacyjny XML (Base64URL z SHA-256)"""
    with open(sciezka_xml, "rb") as f:
        zawartosc_xml = f.read()
        
    hash_sha256 = hashlib.sha256(zawartosc_xml).digest()
    b64_hash = base64.b64encode(hash_sha256).decode("utf-8")
    skrot = b64_hash.replace('+', '-').replace('/', '_').rstrip('=')
    
    return skrot

def wyciagnij_dane_do_linku(sciezka_xml):
    """Wyciąga NIP i Datę wystawienia, ignorując problem przestrzeni nazw (namespaces),
       oraz konwertuje datę do formatu wymaganego przez QR KSeF (DD-MM-YYYY)"""
    drzewo = etree.parse(sciezka_xml)
    
    # 1. Pobieranie NIPu
    nip_lista = drzewo.xpath("//*[local-name()='Podmiot1']//*[local-name()='NIP']")
    nip = nip_lista[0].text if nip_lista else "BRAK_NIP"
    
    # 2. Pobieranie Daty wystawienia
    data_lista = drzewo.xpath("//*[local-name()='P_1']")
    data_wyst_surowa = data_lista[0].text if data_lista else "BRAK_DATY"
    
    # 3. Konwersja daty z YYYY-MM-DD na DD-MM-YYYY
    data_wyst_formatowana = "BRAK_DATY"
    if data_wyst_surowa != "BRAK_DATY":
        try:
            # Wczytujemy datę z formatu komputerowego
            data_obj = datetime.strptime(data_wyst_surowa, "%Y-%m-%d")
            # Wypisujemy datę w formacie dla linku weryfikacyjnego QR
            data_wyst_formatowana = data_obj.strftime("%d-%m-%Y")
        except ValueError:
            # Zabezpieczenie, gdyby XML z jakiegoś powodu zawierał inny format daty
            print(f"Uwaga: Nie udało się sformatować daty: {data_wyst_surowa}")
            data_wyst_formatowana = data_wyst_surowa
            
    return nip, data_wyst_formatowana


def dodaj_qrcode_do_faktury_v2(sciezka_xml, sciezka_html, sciezka_html_wyjscie, numer_ksef):
    # 1. Pobieramy potrzebne elementy
    skrot = wylicz_skrot_ksef_2_0(sciezka_xml)
    nip, data_wystawienia = wyciagnij_dane_do_linku(sciezka_xml)
    
    # 2. Prawidłowy link QR dla KSeF 2.0
    url_qr = f"{QR_URL}/{nip}/{data_wystawienia}/{skrot}"
    
    # 3. Generowanie kodu QR
    qr = qrcode.QRCode(version=1, box_size=4, border=2)
    qr.add_data(url_qr)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    bufor = BytesIO()
    img.save(bufor, format="PNG")
    qr_b64 = base64.b64encode(bufor.getvalue()).decode("utf-8")
    src_obrazka = f"data:image/png;base64,{qr_b64}"
    
    # 4. Wstrzykiwanie do HTML za pomocą układu tabelarycznego
    parser = etree.HTMLParser()
    drzewo = etree.parse(sciezka_html, parser)
    body = drzewo.find('.//body')
    
    if body is not None:
        # Główny pancerny kontener
        div_qr = etree.Element("div")
        styl_kontenera = (
            "clear: both; margin-top: 50px; padding-top: 20px; "
            "border-top: 1px solid #ccc; page-break-inside: avoid; "
            "width: 100%; font-family: 'Montserrat', sans-serif;"
        )
        div_qr.set("style", styl_kontenera)
        
        # Tworzymy tabelę do idealnego pozycjonowania (Lewa - Prawa)
        tabela = etree.Element("table")
        tabela.set("style", "width: 100%; border-collapse: collapse;")
        tr = etree.Element("tr")
        
        # --- LEWA KOLUMNA (QR + Numer KSeF) ---
        td_left = etree.Element("td")
        td_left.set("style", "width: 140px; vertical-align: top;")
        
        img_tag = etree.Element("img")
        img_tag.set("src", src_obrazka)
        img_tag.set("style", "width: 120px; height: 120px; display: block;")
        
        nr_tag = etree.Element("div")
        nr_tag.text = f"Nr KSeF: {numer_ksef}"
        # Używamy word-wrap, aby długi numer ładnie zawinął się pod kodem QR
        nr_tag.set("style", "font-size: 9px; text-align: center; width: 120px; word-wrap: break-word; margin-top: 5px;")
        
        td_left.append(img_tag)
        td_left.append(nr_tag)
        
        # --- PRAWA KOLUMNA (Tekst + Link) ---
        td_right = etree.Element("td")
        td_right.set("style", "vertical-align: middle; padding-left: 20px;")
        
        info = etree.Element("p")
        info.text = "Nie możesz zeskanować kodu z obrazka? Kliknij w link weryfikacyjny i przejdź do weryfikacji faktury!"
        info.set("style", "font-size: 12px; font-weight: bold; margin-bottom: 5px; margin-top: 0;")
        
        link = etree.Element("a")
        link.text = url_qr
        link.set("href", url_qr)
        # Link na niebiesko, bez podkreślenia i zawijający się, jeśli jest bardzo długi
        link.set("style", "font-size: 10px; color: #0056b3; word-break: break-all; text-decoration: none;")
        
        td_right.append(info)
        td_right.append(link)
        
        # Składamy całość jak klocki
        tr.append(td_left)
        tr.append(td_right)
        tabela.append(tr)
        div_qr.append(tabela)
        
        body.append(div_qr)
        
    with open(sciezka_html_wyjscie, "wb") as f:
        f.write(etree.tostring(drzewo, method="html", encoding="utf-8", pretty_print=True))
                
    print(f"Dodano kod QR! Zapisano jako: {sciezka_html_wyjscie}")

if __name__ == "__main__":
    # ==========================================
    # UŻYCIE W TWOIM PROCESIE
    # ==========================================
    # Zakładając, że wcześniej wygenerowałeś plik z użyciem XSLT:
    # plik_html_z_xslt = "faktura_czytelna.html"

    dodaj_qrcode_do_faktury_v2(
        sciezka_xml="fa.xml", 
        sciezka_html="fa.html", 
        sciezka_html_wyjscie="fa_z_qr.html",
        numer_ksef="sdfsadfasdfsadfsadf"
    )

    # Po tym kroku wywołujesz pdfkit na nowym pliku "faktura_z_qr.html"