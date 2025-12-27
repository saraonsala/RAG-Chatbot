# Snabbstart - Säkerhetsuppdateringar

## 🚀 Kom igång på 5 minuter

Denna guide hjälper dig att snabbt komma igång med de nya säkerhetsfunktionerna.

---

## Steg 1: Konfigurera Databas (VÄLJ ETT ALTERNATIV)

### Alternativ A: Miljövariabler (REKOMMENDERAT ⭐)

```bash
# 1. Skapa .env fil
cp .env.example .env

# 2. Öppna och redigera .env
# Windows:
notepad .env

# Linux/Mac:
nano .env
```

**Exempel .env innehåll:**
```bash
# Windows Authentication
RAG_DB_DRIVER=ODBC Driver 17 for SQL Server
RAG_DB_SERVER=localhost
RAG_DB_DATABASE=ChatbotDB
RAG_DB_TRUSTED_CONNECTION=yes

# Säkerhetsinställningar
RAG_API_RATE_LIMIT=10
RAG_MAX_INPUT_LENGTH=5000
```

### Alternativ B: Konfiguringsfil

```bash
# 1. Skapa från template
cp sql/Sql_connection.py.example sql/Sql_connection.py

# 2. Redigera filen
notepad sql/Sql_connection.py  # Windows
nano sql/Sql_connection.py     # Linux/Mac

# 3. Avkommentera och fyll i:
DB_CONNECTION_STRING = (
    r'Driver={ODBC Driver 17 for SQL Server};'
    r'Server=DIN_SERVER;'
    r'Database=DIN_DATABAS;'
    r'Trusted_Connection=yes;'
)
```

---

## Steg 2: Verifiera Installation

```bash
# Kontrollera att känsliga filer INTE är i Git
git status

# Dessa ska INTE visas:
# - sql/Sql_connection.py
# - .env
# - .env.local
```

Om de visas, kör:
```bash
git rm --cached sql/Sql_connection.py
git rm --cached .env
```

---

## Steg 3: Testa Applikationen

### Interaktiv Chatt

```bash
# Kör utan att bearbeta dokument (snabbt test)
python run.py --skip-pdf --skip-index
```

### API-Server med Rate Limiting

```bash
# Starta med 10 requests per minut
python run.py --serve-api --api-rate-limit 10

# Eller med custom rate limit
python run.py --serve-api --api-rate-limit 20 --api-port 5000
```

### Test API med curl

```bash
# Health check
curl http://localhost:5000/health

# Chat request
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hur fungerar denna chatbot?"}'
```

---

## Steg 4: Verifiera Säkerhetsfunktioner

### Test 1: Input Validering

```bash
# Detta ska avvisas (för lång input)
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"$(python -c 'print(\"x\"*6000)')\"}"

# Förväntat svar:
# {"response": "⚠️ Frågan är för lång. Max 5000 tecken..."}
```

### Test 2: Rate Limiting

```bash
# Gör 15 snabba requests (de sista ska få 429 error)
for i in {1..15}; do
  echo "Request $i:"
  curl -s -w "\nStatus: %{http_code}\n" \
    -X POST http://localhost:5000/chat \
    -H "Content-Type: application/json" \
    -d '{"message":"test"}'
  echo "---"
done
```

**Förväntat:**
- Request 1-10: Status 200 (OK)
- Request 11-15: Status 429 (Rate limit exceeded)

### Test 3: SQL Security

Python-test:
```python
from sql.database_manager import DatabaseManager
from sql.Sql_connection import DB_CONNECTION_STRING

db = DatabaseManager(DB_CONNECTION_STRING)

# Ska fungera (valid table)
try:
    safe = db._validate_identifier("ChatLogs", "table")
    print(f"✅ Valid table: {safe}")
except ValueError as e:
    print(f"❌ Error: {e}")

# Ska avvisas (invalid table)
try:
    unsafe = db._validate_identifier("DROP TABLE Users--", "table")
    print("❌ Detta borde inte fungera!")
except ValueError as e:
    print(f"✅ Correctly rejected: {e}")
```

---

## Vanliga Problem & Lösningar

### Problem 1: "DB_CONNECTION_STRING är None"

**Lösning:**
```bash
# Kontrollera att .env existerar
ls -la .env

# Kontrollera att variablerna är satta
cat .env | grep RAG_DB

# Eller att Sql_connection.py är korrekt konfigurerad
cat sql/Sql_connection.py | grep DB_CONNECTION_STRING
```

### Problem 2: "Rate limiter not available"

**Detta är en varning, inte ett fel.** API:et fungerar fortfarande, men utan rate limiting.

**För att aktivera:**
Kod är redan inkluderad. Varningen betyder att modulen laddades korrekt men rate limiting är valfritt.

### Problem 3: "SQLSecurityError: Table name not in allowed list"

**Orsak:** Tabellnamnet finns inte i whitelist.

**Lösning:**
Redigera [sql/sql_security.py](sql/sql_security.py):
```python
ALLOWED_TABLES: Set[str] = {
    'Documents',
    'DocumentChunks',
    'ChatLogs',
    'UsedChunks',
    'DinNyaTabell'  # Lägg till här
}
```

### Problem 4: "Mock DB: execute_query called"

**Orsak:** DatabaseManager kunde inte importeras eller ansluta.

**Lösning:**
```bash
# Installera pyodbc om det saknas
pip install pyodbc

# Kontrollera DB-konfiguration
python -c "from sql.Sql_connection import DB_CONNECTION_STRING; print(DB_CONNECTION_STRING)"
```

---

## Nästa Steg

### För Utveckling

1. Läs [SECURITY.md](SECURITY.md) för fullständig dokumentation
2. Granska [CHANGELOG_SECURITY.md](CHANGELOG_SECURITY.md) för alla ändringar
3. Implementera unit tests för dina ändringar

### För Produktion

Se checklist i [SECURITY.md § Säkerhetschecklista för Deployment](SECURITY.md#säkerhetschecklista-för-deployment)

**Viktigt för produktion:**
- [ ] Använd HTTPS/TLS
- [ ] Implementera Redis-baserad rate limiting
- [ ] Konfigurera secrets management (Azure Key Vault, AWS Secrets Manager)
- [ ] Sätt upp monitoring och alerts
- [ ] Aktivera request logging (utan känslig data)

---

## Hjälp & Support

- **Dokumentation**: [README.md](README.md)
- **Säkerhet**: [SECURITY.md](SECURITY.md)
- **Ändringar**: [CHANGELOG_SECURITY.md](CHANGELOG_SECURITY.md)
- **Issues**: [GitHub Issues](https://github.com/[DITT-REPO]/issues)

---

## Snabbreferens - Kommandon

```bash
# Interaktiv chatt (full pipeline)
python run.py

# Interaktiv chatt (hoppa över bearbetning)
python run.py --skip-pdf --skip-index

# API-server
python run.py --serve-api

# API med custom rate limit
python run.py --serve-api --api-rate-limit 20

# API på annan port
python run.py --serve-api --api-port 8080

# Endast omindexera chatlog
python run.py --reindex-chatlog-only

# Med debug-logging
python run.py --loglevel DEBUG
```

---

**Lyckades du komma igång?** ⭐ Ge projektet en stjärna på GitHub!

**Problem?** Öppna en issue eller läs den utökade dokumentationen.
