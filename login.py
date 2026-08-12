import time
from datetime import datetime, timezone
import requests
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from dotenv import load_dotenv
import os


load_dotenv()


# ==========================================
# 1. KONFIGURACJA ŚRODOWISKA KSeF 2.0
# ==========================================
BASE_URL = os.getenv("KSEF_BASE_URL")  # Nowy adres API v2
NIP = os.getenv("KSEF_NIP")
USER_TOKEN = os.getenv("KSEF_USER_TOKEN")

# Klucz publiczny dla API 2.0 (pobierany z GET /v2/security/public-key-certificates)
PUBLIC_KEY_PEM = os.getenv("KSEF_PUBLIC_KEY").encode("ascii")


def login_to_ksef_v2(nip: str, user_token: str, pem_key: bytes) -> str:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    
    # ---------------------------------------------------------
    # KROK 1: Pobranie wyzwania i użycie czasu z serwera KSeF
    # ---------------------------------------------------------
    print("1. Pobieranie challenge...")
    challenge_url = f"{BASE_URL}/auth/challenge"
    response_challenge = requests.post(challenge_url, headers=headers)
    response_challenge.raise_for_status()
    
    challenge_data = response_challenge.json()
    challenge = challenge_data['challenge']
    server_timestamp = challenge_data['timestamp']
    
    # Niezawodny konwerter czasu (bierzemy czas z KSeF i zamieniamy na Unix MS)
    if isinstance(server_timestamp, int) or (isinstance(server_timestamp, str) and str(server_timestamp).isdigit()):
        # Jeśli KSeF zwrócił już Unix Timestamp w milisekundach
        timestamp_ms = str(server_timestamp)
    else:
        # Jeśli KSeF zwrócił datę ISO (np. "2026-07-23T07:54:52.677Z")
        server_timestamp_str = str(server_timestamp)
        try:
            clean_iso = server_timestamp_str.replace('Z', '+00:00')
            # Ucinamy ułamki sekund do 6 cyfr (wymóg biblioteki datetime w Pythonie)
            import re
            clean_iso = re.sub(r'\.(\d+)', lambda m: '.' + m.group(1)[:6].ljust(6, '0'), clean_iso)
            
            dt = datetime.fromisoformat(clean_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
                
            timestamp_ms = str(int(dt.timestamp() * 1000))
        except Exception as e:
            print(f"Ostrzeżenie: Błąd konwersji daty ({e}). Używam czasu lokalnego.")
            import time
            timestamp_ms = str(int(time.time() * 1000))
            
    print(f"   Wygenerowano timestamp dla serwera: {timestamp_ms}")

    # ---------------------------------------------------------
    # KROK 2: Szyfrowanie (Token 2.0 + Czas z serwera)
    # ---------------------------------------------------------
    print("2. Szyfrowanie tokena algorytmem RSA-OAEP...")
    
    # Zgodnie z formatem KSeF łączymy token z czasem podanym przez sam serwer
    message = f"{user_token}|{timestamp_ms}".encode('utf-8')
    public_key = serialization.load_pem_public_key(pem_key)
    
    # Zostajemy przy pełnym SHA-256 (KSeF to zweryfikował i poprawnie odszyfrował)
    encrypted_bytes = public_key.encrypt(
        message,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    encrypted_token_b64 = base64.b64encode(encrypted_bytes).decode('utf-8')
    
    # ---------------------------------------------------------
    # KROK 3: Uwierzytelnienie za pomocą zaszyfrowanego tokena
    # ---------------------------------------------------------
    print("3. Wywołanie /auth/ksef-token...")
    ksef_token_url = f"{BASE_URL}/auth/ksef-token"
    auth_payload = {
        "contextIdentifier": {
            "type": "NIP",
            "value": nip
        },
        "challenge": challenge,
        "encryptedToken": encrypted_token_b64,
        # Wskazujemy konkretny klucz publiczny MF (zgodny z tym z Twojego certyfikatu)
        "publicKeyId": "IPbPM4CB49vtoR/x/3fEI+Y+Q6lK/bVVehQ7/NlPJoo="
    }
    
    response_auth = requests.post(ksef_token_url, json=auth_payload, headers=headers)
    response_auth.raise_for_status()
    
    auth_data = response_auth.json()
    
    # TUTAJ BYŁ BŁĄD W SKRYPCIE - brakowało przypisania zmiennych:
    authentication_token = auth_data['authenticationToken']['token']
    reference_number = auth_data['referenceNumber']
    
    # ---------------------------------------------------------
    # KROK 3.5: Oczekiwanie na asynchroniczne przetworzenie
    # ---------------------------------------------------------
    print(f"3.5. Oczekiwanie na asynchroniczne przetworzenie w KSeF (Ref: {reference_number})...")
    status_url = f"{BASE_URL}/auth/{reference_number}"
    
    auth_headers = headers.copy()
    auth_headers["Authorization"] = f"Bearer {authentication_token}"
    
    while True:
        status_response = requests.get(status_url, headers=auth_headers)
        status_response.raise_for_status()
        
        status_data = status_response.json()
        internal_code = status_data.get("status", {}).get("code", 0)
        
        if internal_code == 200:
            print("     Sukces! Serwer KSeF zakończył weryfikację.")
            break
        elif internal_code == 100:
            print("     Status 100: KSeF wciąż przetwarza żądanie... czekam 2 sekundy.")
            time.sleep(2)
        else:
            # Jeśli autoryzacja się nie powiedzie (np. kod 450 - zły token/zły NIP), pętla się zatrzyma
            print(f"     Uwaga! Zwrócono inny kod statusu: {internal_code}")
            print(status_data)
            break
            
    # ---------------------------------------------------------
    # KROK 4: Wymiana na token dostępowy JWT (Redeem)
    # ---------------------------------------------------------
    print("4. Wymiana tymczasowego tokena na AccessToken (token/redeem)...")
    redeem_url = f"{BASE_URL}/auth/token/redeem"
    
    response_redeem = requests.post(redeem_url, headers=auth_headers, json={})
    
    if response_redeem.status_code != 200:
        print(f"Błąd w kroku 4: {response_redeem.text}")
        response_redeem.raise_for_status()
    
    tokens = response_redeem.json()
    return tokens['accessToken']

def login2ksef() -> str:
    access_token_jwt = login_to_ksef_v2(NIP, USER_TOKEN, PUBLIC_KEY_PEM)
    return access_token_jwt.get('token')

# ==========================================
# URUCHOMIENIE
# ==========================================
if __name__ == "__main__":
    try:
        access_token_jwt = login_to_ksef_v2(NIP, USER_TOKEN, PUBLIC_KEY_PEM)
        print("\n=== SUKCES! ZALOGOWANO DO API 2.0 ===")
        print(f"Twój JWT AccessToken to:\n{access_token_jwt.get('token')}")
        
        # W kolejnych zapytaniach (np. pobieranie faktur) używasz nagłówka:
        # headers = {"Authorization": f"Bearer {access_token_jwt}"}
        
    except requests.exceptions.RequestException as e:
        print(f"\nBłąd komunikacji z API: {e}")
        if e.response is not None:
            print(f"Szczegóły: {e.response.text}")