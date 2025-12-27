import logging
import time
from pathlib import Path
import argparse
from typing import Optional, List, Dict
from datetime import datetime, timezone # Importerat för /health endpoint
import sys
import os
from sql.database_manager import DatabaseManager
from sql.Sql_connection import DB_CONNECTION_STRING

# --- Lägg till projektets rotmapp i sys.path ---
# Detta gör det möjligt att hitta moduler i 'backend', 'bot', och 'sql' mapparna.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

# --- Importera Flask och CORS (om API ska serveras) ---
try:
    from flask import Flask, request, jsonify
    from flask_cors import CORS
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    # Ingen kritisk exit här, kontrolleras senare om --serve-api används

# --- Importera Rate Limiter ---
try:
    from utils.rate_limiter import SimpleRateLimiter, rate_limit
    RATE_LIMITER_AVAILABLE = True
except ImportError:
    RATE_LIMITER_AVAILABLE = False
    logging.warning("⚠️ Rate limiter not available. API will run without rate limiting.")

# --- DB Connection String (initialiseras till None först) ---
DB_CONNECTION_STRING: Optional[str] = None

# --- Kärnklasser (initialiseras till None först för att hantera importfel snyggare) ---
# Detta är en teknik för att undvika att programmet kraschar direkt vid start om en
# import misslyckas, och istället ge ett mer kontrollerat felmeddelande.
DatabaseManager = None
PDFProcessor = None
FaissIndexer = None
RAGChatBot = None

# --- Sökvägar och Mappfunktioner ---
BASE_DIR = Path(__file__).parent.resolve()
LOG_FILE_PATH = BASE_DIR / "chatbot_pipeline_run.log"
INPUT_FOLDER = BASE_DIR / "anvandarhandbocker"
CLEANED_FOLDER = BASE_DIR / "cleaned_output"
CHUNK_FOLDER = BASE_DIR / "chunk_output"
INDEX_ROOT_DIR = BASE_DIR / "rag_index_storage"
DOC_INDEX_SUBDIR = "document_index"
CHATLOG_INDEX_SUBDIR = "chatlog_index"
OUTPUT_DIRS = [CLEANED_FOLDER, CHUNK_FOLDER, INDEX_ROOT_DIR, INDEX_ROOT_DIR / DOC_INDEX_SUBDIR, INDEX_ROOT_DIR / CHATLOG_INDEX_SUBDIR]

# --- Global Logger Instans (konfigureras i main) ---
logger = logging.getLogger("PipelineRunner")

def setup_logging(level: str = "INFO"):
    """Konfigurerar global loggning för hela applikationen."""
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        print(f"Ogiltig loggnivå: {level}. Använder INFO.")
        numeric_level = logging.INFO

    # Ta bort befintliga handlers för att undvika dubbel loggning
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)-8s] %(name)-25s %(funcName)-25s L:%(lineno)-4d - %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE_PATH, mode="a", encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger.info(f"Loggning konfigurerad till nivå: {level.upper()}")


def load_and_validate_db_connection_string():
    """Laddar och validerar DB_CONNECTION_STRING från sql/Sql_connection.py."""
    global DB_CONNECTION_STRING
    try:
        from sql.Sql_connection import DB_CONNECTION_STRING as imported_db_string
        if not imported_db_string or not isinstance(imported_db_string, str) or \
           not all(k in imported_db_string.upper() for k in ["DRIVER", "SERVER", "DATABASE"]):
            logger.critical("!!! DB_CONNECTION_STRING är felaktig eller saknar nödvändiga delar (DRIVER, SERVER, DATABASE). Avslutar.")
            sys.exit(1)
        DB_CONNECTION_STRING = imported_db_string
        logger.info("✅ DB_CONNECTION_STRING laddad och validerad.")
    except ImportError:
        logger.critical("!!! Kunde inte importera DB_CONNECTION_STRING från sql.Sql_connection.py. Avslutar.")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"!!! Oväntat fel vid import av DB_CONNECTION_STRING: {e}. Avslutar.", exc_info=True)
        sys.exit(1)

def import_core_classes():
    """Importerar kärnklasser och hanterar importfel."""
    global DatabaseManager, PDFProcessor, FaissIndexer, RAGChatBot
    try:
        from sql.database_manager import DatabaseManager as ImportedDBManager
        from backend.PDFProcessor import PDFProcessor as ImportedPDFProcessor
        from backend.faiss_indexer import FaissIndexer as ImportedFaissIndexer
        from bot.RAGChatBot import RAGChatBot as ImportedRAGChatBot

        DatabaseManager = ImportedDBManager
        PDFProcessor = ImportedPDFProcessor
        FaissIndexer = ImportedFaissIndexer
        RAGChatBot = ImportedRAGChatBot
        logger.info("✅ Kärnklasser importerade.")
    except ImportError as e:
        logger.critical(f"!!! Kunde inte importera en eller flera kärnklasser: {e}. Avslutar.", exc_info=True)
        sys.exit(1)

def ensure_output_directories(dir_list: List[Path]):
    """Säkerställer att alla nödvändiga output-mappar finns."""
    logger.info("Säkerställer att output-mappar finns...")
    try:
        for dir_path in dir_list:
            dir_path.mkdir(parents=True, exist_ok=True)
        logger.info("✅ Alla nödvändiga output-mappar är tillgängliga.")
    except Exception as e:
        logger.critical(f"!!! Kritiskt fel vid skapande av output-mappar: {e}. Kontrollera behörigheter. Avslutar.", exc_info=True)
        sys.exit(1)

def clear_document_related_tables(db_mngr: "DatabaseManager"):
    """Rensar tabeller relaterade till dokumentbearbetning."""
    # Ordningen är viktig om FKs inte har ON DELETE CASCADE
    tables_to_clear = ["UsedChunks", "DocumentChunks", "Documents"]
    logger.info(f"Försöker rensa dokumentrelaterade databastabeller: {', '.join(tables_to_clear)}...")
    try:
        for table in tables_to_clear:
            try:
                # Använd TRUNCATE TABLE för att återställa IDENTITY, men den kan misslyckas om FKs finns. DELETE är säkrare.
                # ON DELETE CASCADE i schemat gör att vi egentligen bara behöver radera från 'Documents'.
                # Att radera från alla explicit är en extra säkerhet.
                logger.debug(f"Rensar tabell: {table}...")
                db_mngr.execute_query(f'DELETE FROM "{table}"')
            except Exception as te:
                logger.warning(f"⚠️ Fel vid försök att rensa tabell \"{table}\": {te}. Fortsätter...")
        logger.info("🧹 Rensning av dokumentrelaterade tabeller klar.")
    except Exception as e:
        logger.error(f"❌ Oväntat fel under databasrensning: {e}", exc_info=True)

def clear_chatlogs_table(db_mngr: "DatabaseManager"):
    """Rensar ChatLogs-tabellen. Detta triggar ON DELETE CASCADE för UsedChunks."""
    logger.info("Försöker rensa 'ChatLogs'-tabellen (vilket även rensar 'UsedChunks' via CASCADE)...")
    try:
        # Om vi rensar ChatLogs, behöver vi inte rensa UsedChunks separat
        db_mngr.execute_query('TRUNCATE TABLE "UsedChunks"') # Truncate denna först för säkerhets skull
        db_mngr.execute_query('DELETE FROM "ChatLogs"') # Använd DELETE pga självrefererande FK
        db_mngr.execute_query("DBCC CHECKIDENT ('ChatLogs', RESEED, 0)") # Återställ IDENTITY
        logger.info("🧹 ChatLogs och UsedChunks rensade.")
    except Exception as e:
        logger.error(f"❌ Fel vid rensning av ChatLogs-tabellen: {e}", exc_info=True)


def run_pipeline(args: argparse.Namespace, db_mngr: "DatabaseManager"):
    """Kör den huvudsakliga databehandlingspipelinen."""
    if args.reindex_chatlog_only:
        logger.info("--- Startar ENDAST omindexering av Chatlog ---")
        try:
            indexer = FaissIndexer(db_manager=db_mngr, index_root_dir=str(INDEX_ROOT_DIR))
            indexer.create_chatlog_index()
        except Exception as e:
            logger.critical(f"!!! Kritiskt fel under chatlog-indexering: {e}. Avslutar.", exc_info=True)
            sys.exit(1)
        return

    if not args.skip_pdf:
        logger.info("\n--- Steg 1: Dokumentprocessering & Databasrensning ---")
        if args.clear_chatlogs:
            clear_chatlogs_table(db_mngr)
        # Dokumentrelaterade tabeller rensas alltid om inte --skip-pdf används
        clear_document_related_tables(db_mngr)

        logger.info("Startar PDFProcessor...")
        try:
            processor = PDFProcessor(input_folder=str(INPUT_FOLDER), cleaned_folder=str(CLEANED_FOLDER), chunk_folder=str(CHUNK_FOLDER), db_manager=db_mngr)
            processor.process_all()
            logger.info("--- ✅ Dokumentprocessering klar ---")
        except Exception as proc_err:
            logger.critical(f"!!! Kritiskt fel under PDFProcessor: {proc_err}. Avslutar.", exc_info=True)
            sys.exit(1)
    else:
        logger.info("\n⏩ Hoppar över dokumentprocessering och databasrensning (--skip-pdf).")

    if not args.skip_index:
        logger.info("\n--- Steg 2: Skapande av FAISS Index ---")
        try:
            indexer = FaissIndexer(chunk_folder=str(CHUNK_FOLDER), index_root_dir=str(INDEX_ROOT_DIR), db_manager=db_mngr)
            
            logger.info("--- Indexerar Dokument ---")
            indexer.create_document_index()
            
            logger.info("--- Indexerar Chatlogs ---")
            indexer.create_chatlog_index()

            logger.info("--- ✅ FAISS Indexering klar ---")
        except Exception as index_err:
            logger.critical(f"!!! Kritiskt fel under FaissIndexer: {index_err}. Avslutar.", exc_info=True)
            sys.exit(1)
    else:
        logger.info("\n⏩ Hoppar över FAISS-indexskapande (--skip-index).")


def run_interactive_chat(args: argparse.Namespace, db_mngr: "DatabaseManager"):
    """Startar den interaktiva chattboten."""
    logger.info("\n--- Steg 3: Startar Interaktiv Chatbot ---")
    try:
        interactive_bot = RAGChatBot(
            index_root_dir=str(INDEX_ROOT_DIR),
            db_manager=db_mngr,
            doc_search_top_k=args.doc_top_k,
            db_similarity_threshold=args.db_threshold
        )
        if not interactive_bot.embedding_model: logger.error("Bot-initiering: Embedding-modell ej laddad.")
        if not interactive_bot.doc_index: logger.warning("Bot-initiering: Dokumentindex ej laddat.")
    except Exception as init_err:
        logger.critical(f"!!! Kunde inte initialisera Interaktiv RAGChatBot: {init_err}. Avslutar.", exc_info=True)
        sys.exit(1)

    print("\n===================================")
    print("🧠 Välkommen till RAG Chatbot (Interaktivt Läge)!")
    print(f"   (Använder DB RAG tröskel: {args.db_threshold}, Dokument-chunks: {args.doc_top_k})")
    print("   Skriv en fråga eller 'avsluta' för att sluta.")
    print("===================================")
    while True:
        try:
            user_input = input("\nDu: ").strip()
            if user_input.lower() in ["avsluta", "exit", "quit"]:
                print("\n👋 Hejdå!")
                break
            if not user_input: continue
            
            print("Bot: ⏳ Bearbetar...")
            start_t = time.time()
            response = interactive_bot.generate_response(user_input)
            end_t = time.time()
            
            print(f"\nBot svarar: (Tid: {end_t - start_t:.2f}s)")
            print("-" * 60)
            print(response)
            print("-" * 60)
        except KeyboardInterrupt:
            print("\n🛑 Avbrutet av användaren.")
            break
        except Exception as loop_err:
            logger.error(f"🚨 Fel i konversationsloopen: {loop_err}", exc_info=True)
            print("\n🚨 Ett oväntat fel uppstod. Kontrollera loggfilen.")

# --- Globala Variabler för Flask Appen ---
flask_app_instance: Optional["Flask"] = None
api_bot_instance: Optional["RAGChatBot"] = None
api_initialization_error: Optional[str] = None
api_rate_limiter: Optional["SimpleRateLimiter"] = None

def initialize_and_run_api_server(args: argparse.Namespace, db_mngr: "DatabaseManager"):
    """Initialiserar och startar Flask API-servern."""
    global flask_app_instance, api_bot_instance, api_initialization_error, api_rate_limiter

    if not FLASK_AVAILABLE:
        logger.critical("!!! Flask/Flask-CORS ej installerat. Installera med 'pip install Flask Flask-CORS'. Avslutar.")
        sys.exit(1)

    # Initiera rate limiter
    if RATE_LIMITER_AVAILABLE:
        api_rate_limiter = SimpleRateLimiter(
            max_requests=args.api_rate_limit,
            window_seconds=60  # 60 sekunder fönster
        )
        logger.info(f"✅ Rate limiter aktiverad: {args.api_rate_limit} förfrågningar per minut")
    else:
        logger.warning("⚠️ Rate limiter ej tillgänglig. API körs utan rate limiting!")

    logger.info("Förbereder API-server. Initierar RAGChatBot för API...")
    try:
        api_bot_instance = RAGChatBot(
            index_root_dir=str(INDEX_ROOT_DIR),
            db_manager=db_mngr,
            doc_search_top_k=args.doc_top_k,
            db_similarity_threshold=args.db_threshold
        )
        if not api_bot_instance.embedding_model:
            api_initialization_error = "Embedding-modell kunde inte laddas."
        elif not api_bot_instance.doc_index:
            api_initialization_error = "Dokumentindex kunde inte laddas."
        if api_initialization_error:
            logger.error(f"Problem under initiering av API-bot: {api_initialization_error}")
    except Exception as init_err:
        api_initialization_error = f"Kritiskt initialiseringsfel: {str(init_err)}"
        logger.critical(f"!!! Allvarligt fel vid initialisering av RAGChatBot för API: {init_err}. API kan inte starta funktionellt.", exc_info=True)
        api_bot_instance = None

    flask_app_instance = Flask(__name__)
    CORS(flask_app_instance)

    @flask_app_instance.route('/chat', methods=['POST'])
    @rate_limit(api_rate_limiter) if RATE_LIMITER_AVAILABLE and api_rate_limiter else lambda f: f
    def chat_api_endpoint():
        if not api_bot_instance or api_initialization_error:
            err_msg = api_initialization_error or "Okänt uppstartsfel."
            logger.error(f"/chat anropad men API-boten är inte korrekt initierad: {err_msg}")
            return jsonify({"error": f"Chatbot API är inte tillgänglig: {err_msg}"}), 503
        try:
            data = request.get_json()
            if not data or 'message' not in data or not isinstance(data['message'], str) or not data['message'].strip():
                return jsonify({"error": "Förfrågan måste innehålla ett icke-tomt 'message' i JSON body."}), 400
            
            user_input = data['message']
            logger.info(f"API mottog fråga: '{user_input[:100]}...'")
            start_t = time.time()
            bot_response = api_bot_instance.generate_response(user_input)
            duration = time.time() - start_t
            logger.info(f"API svarade på '{user_input[:100]}...' på {duration:.2f} sekunder.")
            return jsonify({"response": bot_response, "processing_time_seconds": round(duration, 2)}), 200
        except Exception as e:
            logger.error(f"❌ Oväntat fel i /chat API endpoint: {e}", exc_info=True)
            return jsonify({"error": "Internt serverfel vid bearbetning av API-fråga."}), 500

    @flask_app_instance.route('/health', methods=['GET'])
    def health_check_api_endpoint():
        status_code = 200
        status_info = {"status": "OK", "message": "Chatbot API service is running.", "timestamp": datetime.now(timezone.utc).isoformat()}
        
        if api_initialization_error:
            status_info["status"] = "ERROR"
            status_info["message"] = f"Chatbot API har initialiseringsfel: {api_initialization_error}"
            status_code = 503
        elif api_bot_instance:
            if not api_bot_instance.doc_index and not api_bot_instance.chatlog_index:
                 status_info["status"] = "WARNING"
                 status_info["message"] += " Varning: Inga sökindex (dokument eller chatlog) laddade."

        return jsonify(status_info), status_code

    logger.info(f"🚀 Startar Flask API server på http://0.0.0.0:{args.api_port} (Använd CTRL+C för att stoppa)")
    try:
        # use_reloader=False är viktigt för att undvika att tunga modeller initieras två gånger
        flask_app_instance.run(host='0.0.0.0', port=args.api_port, debug=False, use_reloader=False)
    except Exception as flask_err:
        logger.critical(f"!!! Fel vid start av Flask server: {flask_err}", exc_info=True)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Kör RAG-chatbot pipeline, interaktiv chatt eller API-server.")
    parser.add_argument("--skip-pdf", action="store_true", help="Hoppa över dokumentprocessering och DB-rensning.")
    parser.add_argument("--skip-index", action="store_true", help="Hoppa över skapande av FAISS-index.")
    parser.add_argument("--reindex-chatlog-only", action="store_true", help="Uppdaterar ENDAST chatlog-indexet och avslutar.")
    parser.add_argument("--clear-chatlogs", action="store_true", help="Rensar ChatLogs- och UsedChunks-tabellerna.")
    parser.add_argument("--db-threshold", type=float, default=0.75, help="Likhetströskel (0.0-1.0) för DB RAG. Default: 0.75")
    parser.add_argument("--doc-top-k", type=int, default=1, help="Antal toppdokument-chunks att hämta för RAG. Default: 1")
    parser.add_argument("--serve-api", action="store_true", help="Startar Flask API-servern.")
    parser.add_argument("--api-port", type=int, default=5000, help="Port för Flask API-servern. Default: 5000")
    parser.add_argument("--api-rate-limit", type=int, default=10, help="Max antal API-förfrågningar per minut per IP. Default: 10")
    parser.add_argument("--loglevel", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Sätt loggnivå. Default: INFO")
    args = parser.parse_args()

    setup_logging(args.loglevel)

    logger.info("===================================")
    logger.info(f"      Startar RAG Chatbot (Run Mode: {'API Server' if args.serve_api else 'Pipeline/Interactive'})")
    logger.info(f"      Loggnivå: {args.loglevel.upper()}")
    logger.info("===================================")

    load_and_validate_db_connection_string()
    import_core_classes()
    ensure_output_directories(OUTPUT_DIRS)

    db_mngr_instance: Optional["DatabaseManager"] = None
    try:
        if DB_CONNECTION_STRING:
            db_mngr_instance = DatabaseManager(DB_CONNECTION_STRING)
            logger.info("✅ DatabaseManager-instans skapad.")
        else: # Bör inte hända p.g.a. validering, men som en extra säkerhet
            raise ValueError("DB_CONNECTION_STRING är inte satt.")
    except Exception as e:
        logger.critical(f"!!! Kunde inte initiera DatabaseManager: {e}. Avslutar.", exc_info=True)
        sys.exit(1)

    # Välj körläge baserat på argument
    if args.serve_api:
        initialize_and_run_api_server(args, db_mngr_instance)
    elif args.reindex_chatlog_only:
        run_pipeline(args, db_mngr_instance)
        logger.info("--- Omindexering av Chatlog slutförd. Programmet avslutas. ---")
    else:
        # Full pipeline-körning (med eventuella --skip-flaggor) följt av interaktiv chatt
        run_pipeline(args, db_mngr_instance)
        run_interactive_chat(args, db_mngr_instance)

    logger.info("===================================")
    logger.info("      RAG Chatbot Pipeline Avslutad")
    logger.info("===================================")

if __name__ == "__main__":
    main()