# Säkerhetsdokumentation - RAG Chatbot

Denna fil beskriver säkerhetsförbättringar som implementerats i projektet och bästa praxis för säker drift.

## 📋 Innehållsförteckning

1. [Översikt av Säkerhetsförbättringar](#översikt-av-säkerhetsförbättringar)
2. [SQL Injection Skydd](#sql-injection-skydd)
3. [Säker Hantering av Credentials](#säker-hantering-av-credentials)
4. [Input Validering](#input-validering)
5. [API Rate Limiting](#api-rate-limiting)
6. [Konfigurationsinstruktioner](#konfigurationsinstruktioner)
7. [Säkerhetschecklista för Deployment](#säkerhetschecklista-för-deployment)

---

## Översikt av Säkerhetsförbättringar

Följande säkerhetsförbättringar har implementerats:

- ✅ **SQL Injection Skydd**: Validering av alla SQL-identifierare
- ✅ **Credentials Management**: Miljövariabler istället för hårdkodade värden
- ✅ **Input Validering**: Begränsningar på längd och innehåll
- ✅ **API Rate Limiting**: Skydd mot DoS-attacker
- ✅ **Förbättrad Felhantering**: Mock-klasser med bättre loggning

---

## SQL Injection Skydd

### Problem som Åtgärdats

Tidigare kunde dynamiska tabell- och kolumnnamn i SQL-queries utgöra en säkerhetsrisk:

```python
# FÖRE (osäkert)
query = f'SELECT "{self.chatlog_response_col}" FROM "{self.chatlog_table_name}"'
```

### Lösning

En ny säkerhetsmodul ([sql/sql_security.py](sql/sql_security.py)) validerar alla SQL-identifierare:

```python
# EFTER (säkert)
safe_table = self.db_manager._validate_identifier(self.chatlog_table_name, "table")
safe_col = self.db_manager._validate_identifier(self.chatlog_response_col, "column")
query = f'SELECT "{safe_col}" FROM "{safe_table}"'
```

### Whitelist-System

Alla tillåtna tabell- och kolumnnamn finns definierade i:
- `sql/sql_security.py` → `ALLOWED_TABLES` och `ALLOWED_COLUMNS`

För att lägga till nya tabeller/kolumner, uppdatera dessa whitelists.

### Funktioner

- `validate_table_name()`: Validerar tabellnamn
- `validate_column_name()`: Validerar kolumnnamn
- `build_safe_query()`: Bygger säkra queries med validerade identifierare
- `escape_like_pattern()`: Escapar LIKE-specialtecken

---

## Säker Hantering av Credentials

### .gitignore Uppdaterad

Filen [.gitignore](.gitignore) är konfigurerad för att **aldrig** commita känslig information:

```gitignore
# Databaskonfiguration
sql/Sql_connection.py

# Miljövariabler
.env
.env.local

# Output-filer med potentiell känslig data
chunk_output/
rag_index_storage/
```

### Miljövariabler (REKOMMENDERAT)

#### Steg 1: Skapa .env fil

Kopiera exemplet:
```bash
cp .env.example .env
```

#### Steg 2: Konfigurera miljövariabler

Redigera `.env` med dina värden:

```bash
# Windows Authentication
RAG_DB_DRIVER=ODBC Driver 17 for SQL Server
RAG_DB_SERVER=localhost
RAG_DB_DATABASE=ChatbotDB
RAG_DB_TRUSTED_CONNECTION=yes

# ELLER SQL Authentication
# RAG_DB_USERNAME=myuser
# RAG_DB_PASSWORD=mypassword
```

#### Steg 3: Använd python-dotenv (tillval)

Installera för automatisk laddning:
```bash
pip install python-dotenv
```

Lägg till i början av `run.py`:
```python
from dotenv import load_dotenv
load_dotenv()  # Laddar .env fil
```

### Alternativ: Hårdkodad Konfiguration

Om du inte kan använda miljövariabler:

1. Kopiera template-filen:
   ```bash
   cp sql/Sql_connection.py.example sql/Sql_connection.py
   ```

2. Redigera `sql/Sql_connection.py` och fyll i dina värden

3. **VIKTIGT**: Commit ALDRIG denna fil till Git!

---

## Input Validering

### User Input Begränsningar

Implementerat i [bot/RAGChatBot.py](bot/RAGChatBot.py):

```python
MAX_USER_INPUT_LENGTH = 5000  # Max tecken i frågor
MAX_CONTEXT_LENGTH = 50000     # Max total längd på kontext
```

### Vad Valideras

1. **Längd på användarfrågor**: Max 5000 tecken
   ```python
   if len(user_input) > MAX_USER_INPUT_LENGTH:
       return "⚠️ Frågan är för lång..."
   ```

2. **Kontext från dokument**: Max 50000 tecken
   - Förhindrar överdrivna API-kostnader
   - Skyddar mot minnesslut

3. **Tomma/ogiltiga inputs**: Automatisk avvisning

### Anpassa Gränser

Redigera konstanterna i `bot/RAGChatBot.py`:

```python
# Striktare gränser för produktion
MAX_USER_INPUT_LENGTH = 2000
MAX_CONTEXT_LENGTH = 30000
```

---

## API Rate Limiting

### Implementation

En enkel minnesbaserad rate limiter ([utils/rate_limiter.py](utils/rate_limiter.py)) skyddar API:et:

```python
# Default: 10 förfrågningar per minut per IP
api_rate_limiter = SimpleRateLimiter(
    max_requests=10,
    window_seconds=60
)
```

### Användning

API-endpoint `/chat` är automatiskt skyddad:

```python
@app.route('/chat', methods=['POST'])
@rate_limit(api_rate_limiter)
def chat_api_endpoint():
    # ...
```

### Konfigurera Rate Limit

Vid start av API-server:

```bash
# 20 förfrågningar per minut
python run.py --serve-api --api-rate-limit 20

# 5 förfrågningar per minut (strikt)
python run.py --serve-api --api-rate-limit 5
```

### Rate Limit Headers

API:et returnerar standardheaders:

```http
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 7
X-RateLimit-Reset: 1640000000
```

Vid överskridning:
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 42
```

### För Produktion

För distribuerade system, använd **Redis** istället för minnesbaserad lösning:

```bash
pip install redis flask-limiter
```

Exempel med Flask-Limiter:
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app,
    key_func=get_remote_address,
    storage_uri="redis://localhost:6379"
)
```

---

## Konfigurationsinstruktioner

### Snabbstart - Utvecklingsmiljö

1. **Installera beroenden:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Konfigurera databas:**
   ```bash
   # Skapa .env från template
   cp .env.example .env

   # Redigera .env med dina värden
   # ELLER

   # Använd template för Sql_connection.py
   cp sql/Sql_connection.py.example sql/Sql_connection.py
   # Redigera sql/Sql_connection.py
   ```

3. **Verifiera säkerhet:**
   ```bash
   # Kontrollera att känsliga filer INTE är versionshanterade
   git status
   # sql/Sql_connection.py och .env ska INTE visas
   ```

4. **Kör applikationen:**
   ```bash
   # Interaktiv chatt
   python run.py

   # API-server med rate limiting
   python run.py --serve-api --api-rate-limit 10
   ```

---

## Säkerhetschecklista för Deployment

### Före Deployment

- [ ] **Credentials**: Aldrig hårdkodade i kod
- [ ] **Miljövariabler**: Konfigurerade i deployment-miljön
- [ ] **Secrets Management**: Använd Azure Key Vault / AWS Secrets Manager
- [ ] **.gitignore**: Verifierad - inga känsliga filer committade
- [ ] **Rate Limiting**: Aktiverad och testad
- [ ] **Input Validation**: Verifierad för alla endpoints
- [ ] **SQL Whitelists**: Uppdaterade med alla nödvändiga tabeller/kolumner
- [ ] **HTTPS**: SSL/TLS certifikat konfigurerat
- [ ] **Logging**: Känslig data loggas INTE
- [ ] **Error Messages**: Inga stack traces exponeras till användare

### Rekommenderade Tilläggsåtgärder

1. **HTTPS/TLS**:
   ```python
   # Tvinga HTTPS i produktion
   from flask_tls import TLSify
   tls = TLSify(app)
   ```

2. **CORS Policy**:
   ```python
   # Begränsa till specifika domäner
   CORS(app, origins=["https://yourdomain.com"])
   ```

3. **Request Size Limit**:
   ```python
   app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB
   ```

4. **Security Headers**:
   ```python
   from flask_talisman import Talisman
   Talisman(app, content_security_policy=None)
   ```

5. **Logging & Monitoring**:
   - Använd strukturerad loggning (JSON)
   - Integrera med SIEM-system
   - Sätt upp alerts för säkerhetshändelser

6. **Secrets Rotation**:
   - Rotera DB-lösenord regelbundet
   - Använd korta access tokens
   - Implementera automatisk rotation

---

## Säkerhetsincidenter

### Rapportera Sårbarheter

**PUBLICERA INTE** säkerhetsproblem publikt i GitHub Issues!

Kontakta istället projektansvarig direkt via:
- Email: [DIN_EMAIL]
- Encrypted: [PGP Key om tillgänglig]

### Response Timeline

- **24h**: Initial bekräftelse
- **7 dagar**: Preliminär bedömning
- **30 dagar**: Fix implementerad och testad

---

## Revisionshistorik

| Datum | Version | Ändringar |
|-------|---------|-----------|
| 2024-XX-XX | 1.0 | Initial säkerhetshärdning implementerad |
| | | - SQL injection skydd |
| | | - Credentials management |
| | | - Input validering |
| | | - API rate limiting |

---

## Kontakt & Support

För säkerhetsfrågor eller support:
- GitHub Issues: [https://github.com/[DITT-REPO]/issues](https://github.com/[DITT-REPO]/issues)
- Dokumentation: Se [README.md](README.md)

---

**OBS**: Denna dokumentation uppdateras kontinuerligt. Kontrollera alltid senaste versionen i main branch.
