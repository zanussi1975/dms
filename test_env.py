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
PUBLIC_KEY_PEM = os.getenv("KSEF_PUBLIC_KEY")


print(f"BASE URL = {BASE_URL}")
print(f"NIP URL= {NIP}")
print(f"USER TOKEN = {USER_TOKEN}")
print(f"PUBLIC KEY PEM = {PUBLIC_KEY_PEM}")
