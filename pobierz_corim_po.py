from dbfread import DBF
import pandas as pd
import os, sys

# 1. Ścieżki do plików (pamiętaj o pracy na kopiach!)

if getattr(sys, 'frozen', False):
    app_dir = os.path.dirname(sys.executable)
else:
    app_dir = os.path.dirname(__file__)
    
    
plik_zamowienia = os.path.join(app_dir,"corim/FECOMMAN.DBF")
plik_dostawcy = os.path.join(app_dir,"corim/FCFOURNI.DBF")

try:
    # 2. Wczytanie obu plików do pamięci (do obiektów DataFrame)
    print("Wczytywanie bazy zamówień...")
    df_zam = pd.DataFrame(iter(DBF(plik_zamowienia, encoding='ANSI')))
    
    print("Wczytywanie bazy dostawców...")
    df_dost = pd.DataFrame(iter(DBF(plik_dostawcy, encoding='ANSI')))
    
    kolumna_kod_w_zamowieniach = 'CODE_FOUR' 
    kolumna_kod_w_dostawcach = 'CODE_FOUR'
    
    df_zam[kolumna_kod_w_zamowieniach] = df_zam[kolumna_kod_w_zamowieniach].astype(str).str.strip()
    df_dost[kolumna_kod_w_dostawcach] = df_dost[kolumna_kod_w_dostawcach].astype(str).str.strip()

    wynik = pd.merge(
        df_zam, 
        df_dost, 
        left_on=kolumna_kod_w_zamowieniach, 
        right_on=kolumna_kod_w_dostawcach, 
        how='left'
    )
    
    wynik['CODE_DEM'] = wynik['CODE_DEM'].astype(str).str.strip()
    
    uzytkownik = 'TZ'
    wynik_przefiltrowany = wynik[wynik['CODE_DEM'] == uzytkownik]
    wynik_posortowany = wynik_przefiltrowany.sort_values(by='DATE_CREA', ascending=False)
    
    dane = wynik_posortowany[['DATE_CREA', 'NUM_CMDE', kolumna_kod_w_zamowieniach, 'LIBE_FOUR', 'MONTANT', 'CODE_DEVI_x', 'LIBE_CMDE']].head(20)
    

except Exception as e:
    print(f"Wystąpił błąd: {e}")