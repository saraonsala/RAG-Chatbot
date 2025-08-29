# 📚 XXXXX RAG Chatbot Dokumentation (v2025-06-09)

En intelligent RAG-baserad (Retrieval-Augmented Generation) chatbot designad för att ge snabba och relevanta svar baserat på innehållet i lokala dokument (PDF och Excel). Projektet använder lokalt körda språkmodeller (via LM Studio), FAISS för vektorindexering och Sentence-BERT för att möjliggöra effektiv informationshämtning.

## 🚀 Funktioner

-   📄 **Multiformat Input:** Hanterar källdokument i både **PDF** och **Excel** (`.xlsx`, `.xls`).
-   🧹 **Automatisk Rensning:** Extraherar text och tar bort störande element som sidfötter, specifika headers och extra tomrader.
-   💾 **Robust Databaslagring (SQL Server):**
    *   **Dokumentdata:** Lagrar metadata om bearbetade dokument och deras text-chunks (`Documents`, `DocumentChunks`).
    *   **Konversationsdata:** Loggar användarfrågor, botsvar, och vilka chunks som användes för att generera svaret (`ChatLogs`, `UsedChunks`).
-   ✂️ **Token-baserad Chunking:** Delar upp rensad text i hanterbara "chunks" med **`tiktoken`**, med konfigurerbart överlapp. Metadata (källa, sida, titel, taggar, tidsstämpel) sparas per chunk.
-   🧠 **Dubbel RAG (Retrieval Pipeline):**
    *   **DB RAG (Svarscache):** Söker först i ett FAISS-index över *tidigare användarfrågor*. Om en tillräckligt liknande fråga hittas, hämtas det sparade svaret direkt från databasen för **extremt snabbt svar**.
    *   **Dokument RAG:** Om ingen bra match finns i historiken, söker den i ett FAISS-index över *dokument-chunks*. De mest relevanta chunksen hämtas.
-   ✨ **LLM-generering:** Den hämtade kontexten från dokument-chunks skickas tillsammans med användarens fråga till en lokalt körd språkmodell (via **LM Studio API**) för att generera ett kontextbaserat, människoliknande svar.
-   ⚡ **Vektorsökning (FAISS):** Använder två separata, effektiva FAISS-index för semantisk sökning: ett för dokument-chunks och ett för chatlog-frågor.
-   🇸🇪 **Svensk NLP:** Optimerad för svenska med embeddings från `KBLab/sentence-bert-swedish-cased` och systemprompt för svenska svar.
-   📚 **Källhänvisning:** Svar genererade från dokument inkluderar referenser till källdokument och eventuellt sida för transparens.
-   🔧 **Datahantering:** Inkluderar ett separat skript (`cleanup_updated_manual.py`) för att selektivt rensa databasen från data relaterad till gamla eller uppdaterade manualer.
-   ⚙️ **Flexibel Körning:** Kommandoradsflaggor i `run.py` för att styra hela databearbetningspipelinen.
-   🌐 **API-Server:** Möjlighet att starta en Flask-baserad API-server (`--serve-api`) för att exponera chatboten via HTTP-endpoints (`/chat`, `/health`).

## 🛠️ Teknisk översikt

-   **Backend & Orkestrering:** Python 3.9+
-   **Text Extraktion:** PyMuPDF (`fitz`) för PDF, Pandas för Excel
-   **Chunking/Tokenisering:** `tiktoken`
-   **Embeddings:** `sentence-transformers`
-   **Databashantering:** `pyodbc` för SQL Server
-   **Vektorsökning:** `faiss-cpu`
-   **LLM Interface:** Lokal LLM via LM Studio API (`requests`)
-   **Webb-API:** Flask, Flask-CORS

## 📂 Projektstruktur
rag-chatbot/
├── backend/
│ ├── PDFProcessor.py # Hanterar fil -> text -> chunks -> DB
│ ├── faiss_indexer.py # Bygger FAISS-index + mappningar
│ └── init.py # Markerar som paket
├── bot/
│ ├── RAGChatBot.py # Kärnlogik för RAG och LLM-anrop
│ └── init.py # Markerar som paket
├── sql/
│ ├── database_manager.py # Klass för DB-interaktion
│ ├── Sql_connection.py # ENDAST definition av DB_CONNECTION_STRING
│ ├── schema.sql # SQL för att skapa/uppdatera databasschemat
│ └── init.py # Markerar som paket
├── scripts/
│ └── cleanup_updated_manual.py # Skript för att rensa data för gamla manualer
├── anvandarhandbocker/ # <INPUT> Lägg dina PDF/Excel-filer här
├── chunk_output/ # <GENERERAS> JSON-filer med chunks
├── rag_index_storage/ # <GENERERAS> Rotmapp för alla FAISS-index
├── templates/
│ └── index.html # Enkel HTML/JS-frontend för test av API
├── .venv/ # Virtuell miljö (rekommenderas)
├── run.py # Huvudskript för att köra pipelinen/boten/API:et
├── requirements.txt # Python-beroenden
├── README.md # Denna fil
└── README_SQL.md # Detaljerad beskrivning av databasschemat
## 🧪 Kom igång

### Förutsättningar
-   Python 3.9+
-   En tillgänglig SQL Server-instans.
-   LM Studio (eller annan OpenAI-kompatibel API-server) igång med en kompatibel GGUF-modell laddad.

### 1. Setup Miljö & Beroenden
Använd en virtuell miljö. `uv` rekommenderas för snabbare installation.

```bash
# Installera uv (om du inte har det)
pip install uv 

# Skapa och aktivera virtuell miljö
uv venv
source .venv/Scripts/activate # Windows
# source .venv/bin/activate   # Linux/macOS

# Installera beroenden
uv pip install -r requirements.txt
2. Databas-setup
Konfigurera Anslutning:
Öppna filen sql/Sql_connection.py och redigera DB_CONNECTION_STRING så att den pekar korrekt på din SQL Server. Detta är obligatoriskt.
Skapa/Uppdatera Tabeller:
Kör SQL-skriptet sql/schema.sql mot din databas (t.ex. via SQL Server Management Studio). Detta skapar de nödvändiga tabellerna och deras relationer.
Se README_SQL.md för detaljerad information om schemat.
3. Förbered Indata & Modell
Källdokument: Lägg alla dina PDF- och/eller Excel-filer i mappen anvandarhandbocker/.
LM Studio: Starta LM Studio, ladda en modell och starta den lokala servern. Se till att modellnamnet matchar det som anges i bot/RAGChatBot.py.
4. Kör Pipelinen & Starta Boten/API:et
Använd run.py från projektets rotmapp med olika flaggor för att styra processen.
# FULL KÖRNING: Rensar allt dokumentrelaterat i DB, processar filer, bygger om alla index,
# och startar sedan interaktiv chatt. Perfekt för första körningen.
python run.py

# STARTA BARA CHATTEN (snabbast): Förutsätter att all data redan är processad och indexerad.
python run.py --skip-pdf --skip-index

# STARTA API-SERVERN: Förutsätter att allt är processat och indexerat.
python run.py --serve-api --api-port 5001

# UPPDATERA INDEX: Om du har ändrat data i DB (t.ex. lagt till nya PDF-filer)
# men vill slippa köra om hela PDF-processeringen om den redan är gjord.
python run.py --skip-pdf

# UPPDATERA ENDAST CHATLOG-INDEX: Om du bara vill uppdatera indexet för tidigare frågor.
python run.py --reindex-chatlog-only
Se README.md i projektet eller kör python run.py --help för en fullständig lista över kommandoradsflaggor.
5. Hantera Uppdaterade Manualer
När en manual uppdateras, använd det dedikerade rensningsskriptet för att ta bort den gamla versionens data från databasen.
👩‍💻 För utvecklare
Klassansvar: Se projektstrukturen ovan för en snabb översikt av varje moduls ansvar.
Databas: All databaslogik är centraliserad i sql/database_manager.py.
Modellkompatibilitet: Systemprompten i bot/RAGChatBot.py kan behöva justeras för olika LLM-modeller.
Prestanda: DB RAG (svarscache) är mycket snabb. Svarstiden för Dokument RAG beror på din hårdvara och vald LLM.
---

### `README_SQL.md` (Uppdaterad Version)

Denna fil ger en detaljerad beskrivning av databasschemat och rensningsprocessen.

```markdown
# 📄 Databasschema för RAG Chatbot (v2025-06-09)

Denna dokumentation beskriver de databastabeller som används av RAG-chatboten i SQL Server. Systemet lagrar metadata om bearbetade källdokument, deras text-chunks, och loggar konversationer för analys och återanvändning av svar (DB RAG).

---

## 🗃️ Tabellöversikt

| Tabellnamn       | Syfte                                                                |
|------------------|----------------------------------------------------------------------|
| `Documents`      | Metadata för varje bearbetat källdokument (PDF, Excel etc.).         |
| `DocumentChunks` | Extraherade och rensade textfragment (chunks) från dokumenten.       |
| `ChatLogs`       | Loggar enskilda konversationsturer (fråga, svar, embedding).         |
| `UsedChunks`     | Kopplingstabell: Loggar vilka chunks som användes för ett specifikt svar. |

## 🔗 Relationer
Documents] 1--* [DocumentChunks]
|
*
| (via DocumentID)
[UsedChunks]
|
*
| (via ChatLogID)
[ChatLogs] 1--* [UsedChunks]
## 📑 Tabeller i detalj

### 🗂️ Documents

Lagrar en post för varje unikt källdokument som bearbetats.

| Kolumn         | Typ           | Info                                                | PK |
|----------------|---------------|-----------------------------------------------------|----|
| `DocumentID`   | NVARCHAR(100) | Unikt dokument-ID (UUID-sträng).                    | Ja |
| `FileName`     | NVARCHAR(255) | Namnet på originalfilen. **Används för rensning.**  |    |
| `FilePath`     | NVARCHAR(1024)| Absolut sökväg till filen vid bearbetning.          |    |
| `FileSize`     | BIGINT        | Filstorlek i bytes.                                 |    |
| `ExtractionDate`| DATETIME2(3)  | Tidsstämpel (UTC) när texten extraherades.          |    |
| `TotalChunks`  | INT           | Antal chunks dokumentet delades upp i.              |    |
| `Language`     | VARCHAR(10)   | Dokumentets språk (t.ex. 'sv').                     |    |

---

### 📄 DocumentChunks

Innehåller de faktiska textsegmenten (chunks) som är grunden för dokument-RAG.

| Kolumn            | Typ           | Info                                                  | PK/FK |
|-------------------|---------------|-------------------------------------------------------|-------|
| `ChunkID`         | NVARCHAR(100) | Unikt chunk-ID (UUID-sträng).                         | PK    |
| `DocumentID`      | NVARCHAR(100) | Referens till `Documents.DocumentID`. **ON DELETE CASCADE**.| FK    |
| `ChunkIndex`      | INT           | Ordningstalet för chunken inom sitt dokument.         |       |
| `ChunkText`       | NVARCHAR(MAX) | Textinnehållet för denna chunk.                       |       |
| `Source`          | NVARCHAR(255) | Källreferens (t.ex. rensad `.txt`-fil).               |       |
| `PageHint`        | INT           | Uppskattat sidnummer från källdokumentet.             |       |
| `SectionTitle`    | NVARCHAR(255) | Eventuell extraherad sektionstitel.                   |       |
| `TagsJson`        | NVARCHAR(MAX) | Extraherade taggar lagrade som en JSON-sträng.        |       |
| `TokenCount`      | INT           | Antal tokens i chunken.                               |       |
| `CreationTimestamp`| DATETIME2(3)  | Tidsstämpel (UTC) när chunken skapades.               |       |

---

### 💬 ChatLogs

Lagrar varje konversationstur för analys och för DB RAG (svarscache).

| Kolumn                | Typ           | Info                                                       | PK/FK        |
|-----------------------|---------------|------------------------------------------------------------|--------------|
| `ChatLogID`           | INT           | Unikt ID för denna loggpost.                               | PK, IDENTITY |
| `UserQuestion`        | NVARCHAR(MAX) | Användarens fråga.                                         |              |
| `BotResponse`         | NVARCHAR(MAX) | Botens slutliga svar (inkl. ev. fel/källor).               |              |
| `Timestamp`           | DATETIME2(3)  | Tidsstämpel när loggposten skapades (DB-default).          |              |
| `UserQuestionEmbedding`| VARBINARY(MAX)| Frågans embedding (vektor) lagrad som bytes.               |              |
| `CachedResponseID`    | INT           | Referens till `ChatLogID` för det svar som återanvändes.   | FK, NULL     |

---

### 🛠️ UsedChunks

Kopplingstabell som länkar en specifik chattloggpost (`ChatLogID`) till de dokument och chunks som användes för att generera svaret.

| Kolumn         | Typ           | Info                                                        | PK/FK        |
|----------------|---------------|-------------------------------------------------------------|--------------|
| `UsedChunkLinkID`| INT          | Unikt ID för denna kopplingspost.                           | PK, IDENTITY |
| `ChatLogID`    | INT           | Referens till den `ChatLogID` som använde denna chunk.    | FK, NOT NULL |
| `ChunkID`      | NVARCHAR(100) | ID för den dokument-chunk som användes.                     | NOT NULL     |
| `DocumentID`   | NVARCHAR(100) | ID för det dokument som den använda chunken tillhör.      | NOT NULL     |
| `LogTimestamp` | DATETIME2(3)  | Tidsstämpel när denna koppling loggades (DB-default).       |              |

**Foreign Key Constraints:**
-   `FK_UsedChunks_ChatLog` (`UsedChunks.ChatLogID` -> `ChatLogs.ChatLogID`) har **`ON DELETE CASCADE`**.
-   `FK_DocumentChunks_Document` (`DocumentChunks.DocumentID` -> `Documents.DocumentID`) har **`ON DELETE CASCADE`**.

---

## 🧹 Rensning av Data för Uppdaterade Manualer

När en manual uppdateras, behöver den gamla versionens data tas bort för att undvika att boten ger svar baserade på inaktuell information.

**Process för att hantera en uppdaterad manual:**

1.  **Identifiera och ta bort den gamla manualen:**
    *   Använd rensningsskriptet `scripts/cleanup_updated_manual.py` för att på ett säkert sätt ta bort all relaterad data.
    *   **Exempel:**
        ```bash
        # Ta bort data för "gammal_manual.pdf" OCH alla chattloggar som använde den.
        python scripts/cleanup_updated_manual.py "gammal_manual.pdf" --delete-logs
        ```
    *   **Vad skriptet gör:**
        1.  Hittar `DocumentID` baserat på filnamnet.
        2.  Om `--delete-logs` anges: Hittar alla `ChatLogID`n som är kopplade till dokumentet via `UsedChunks`-tabellen och raderar dessa från `ChatLogs`. Detta raderar även automatiskt de relevanta raderna i `UsedChunks` (`ON DELETE CASCADE`).
        3.  Raderar dokumentposten från `Documents`. Detta raderar även automatiskt alla chunks för dokumentet i `DocumentChunks` (`ON DELETE CASCADE`).

2.  **Bearbeta den nya manualen:**
    *   Placera den **nya versionen** av manualen i mappen `anvandarhandbocker/`.
    *   Se till att den **gamla versionen** är borttagen från samma mapp.
    *   Kör en fullständig uppdatering av pipelinen för att processa den nya filen och **bygga om FAISS-indexen**.
        ```bash
        # Detta rensar dokumenttabellerna igen (säkerhetsåtgärd),
        # processar den nya filen, och bygger om båda indexen.
        python run.py
        ```
    *   Att bygga om FAISS-indexen (`--skip-index` **får inte** användas) är **kritiskt** för att sökningen ska fungera med den nya datan.

---

Se `sql/schema.sql` för de faktiska `CREATE TABLE` och `ALTER TABLE`-kommandona som används för att skapa och underhålla detta schema.