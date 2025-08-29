import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, List, Any, Set
from sql.database_manager import DatabaseManager
from sql.Sql_connection import DB_CONNECTION_STRING

# --- Setup Sökvägar & Importer ---
# Gå upp en nivå från script-mappen för att hitta 'sql'-paketet
# Om detta skript ligger i roten, kan du använda Path(__file__).parent.
project_root = Path(__file__).resolve().parent.parent 
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

# Variabler för importer, initieras som None för att kunna ge bättre felmeddelanden
DatabaseManager = None
DB_CONNECTION_STRING = None

# --- Loggning Setup ---
LOG_FILE_CLEANUP = "cleanup_manual_data.log"
logger = logging.getLogger("ManualCleanupScript")

def setup_cleanup_logging():
    """Konfigurerar loggning specifikt för detta skript."""
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            handler.close()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)-25s - %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE_CLEANUP, mode="a", encoding="utf-8"), # 'a' för append
            logging.StreamHandler(sys.stdout)
        ]
    )
    # Återhämta logger-instansen efter basicConfig
    global logger
    logger = logging.getLogger("ManualCleanupScript")
    logger.info(f"Loggning konfigurerad. Loggar till: {LOG_FILE_CLEANUP}")


def find_document_id(db_manager: "DatabaseManager", file_name: str) -> Optional[str]:
    """Hittar DocumentID baserat på FileName."""
    logger.info(f"Söker efter DocumentID för FileName: '{file_name}'")
    query = 'SELECT DocumentID FROM "Documents" WHERE FileName = ?'
    try:
        result = db_manager.fetch_one(query, (file_name,))
        if result and result[0] is not None:
            doc_id = str(result[0])
            logger.info(f"Hittade DocumentID: {doc_id}")
            return doc_id
        else:
            logger.warning(f"Ingen post hittades i Documents för FileName: '{file_name}'")
            return None
    except Exception as e:
        logger.error(f"Fel vid sökning efter DocumentID för '{file_name}': {e}", exc_info=True)
        return None

def find_related_chat_log_ids(db_manager: "DatabaseManager", document_id: str) -> Set[Any]:
    """
    Hittar unika ChatLogIDs från UsedChunks som är kopplade till ett givet DocumentID.
    """
    logger.info(f"Söker efter ChatLogIDs kopplade till DocumentID: {document_id} (via UsedChunks)...")
    query = 'SELECT DISTINCT ChatLogID FROM "UsedChunks" WHERE DocumentID = ? AND ChatLogID IS NOT NULL'
    
    chat_log_ids: Set[Any] = set()
    try:
        results = db_manager.fetch_many(query, (document_id,))
        if results:
            chat_log_ids = {row[0] for row in results if row[0] is not None}
            logger.info(f"Hittade {len(chat_log_ids)} unika ChatLogIDs kopplade till DocumentID {document_id}.")
        else:
            logger.info(f"Inga ChatLogs hittades kopplade till DocumentID {document_id} via UsedChunks.")
    except Exception as e:
        logger.error(f"Fel vid sökning efter relaterade ChatLogIDs för DocumentID {document_id}: {e}", exc_info=True)
    return chat_log_ids

def delete_data_for_document(db_manager: "DatabaseManager", document_id: str, delete_related_chat_logs: bool) -> bool:
    """
    Tar bort all data relaterad till ett DocumentID, inklusive chunks, 
    och valfritt relaterade chattloggar (via UsedChunks).
    Utnyttjar ON DELETE CASCADE-constraints i databasen.
    """
    logger.info(f"Påbörjar borttagning av data relaterad till DocumentID: {document_id}")
    if delete_related_chat_logs:
        logger.warning(f"Flaggan --delete-logs är aktiv. Chattloggar kopplade till DocumentID {document_id} kommer också att tas bort.")

    try:
        with db_manager: # Använder DatabaseManagers kontext (__enter__/__exit__) för att hantera anslutning
            # Steg 1: (Valfritt) Radera relaterade ChatLogs
            # Detta kommer automatiskt att radera associerade rader i UsedChunks p.g.a. ON DELETE CASCADE.
            if delete_related_chat_logs:
                related_chat_log_ids = find_related_chat_log_ids(db_manager, document_id)
                if related_chat_log_ids:
                    placeholders = ','.join('?' * len(related_chat_log_ids))
                    sql_delete_chatlogs = f'DELETE FROM "ChatLogs" WHERE ChatLogID IN ({placeholders})'
                    
                    deleted_chatlogs_count = db_manager.execute_query(sql_delete_chatlogs, tuple(related_chat_log_ids))
                    if deleted_chatlogs_count is not None:
                        logger.info(f"Tog bort {deleted_chatlogs_count} rader från ChatLogs (och implicit från UsedChunks via CASCADE).")
                    else:
                        logger.error(f"Fel vid försök att radera från ChatLogs för DocumentID {document_id}.")
                        return False # Avbryt om detta steg misslyckades
                else:
                    logger.info(f"Inga ChatLogs hittades kopplade till DocumentID {document_id} att ta bort.")

            # Steg 2: Radera dokumentet från Documents-tabellen.
            # Detta kommer automatiskt att radera associerade rader i DocumentChunks p.g.a. ON DELETE CASCADE.
            logger.info(f"Försöker radera dokumentet och dess chunks för DocumentID: {document_id}")
            sql_delete_document = 'DELETE FROM "Documents" WHERE DocumentID = ?'
            deleted_doc_count = db_manager.execute_query(sql_delete_document, (document_id,))

            if deleted_doc_count is not None and deleted_doc_count > 0:
                logger.info(f"✅ Tog bort dokumentet (och implicit dess chunks) för DocumentID: {document_id}. Antal dokumentrader borttagna: {deleted_doc_count}.")
                return True
            elif deleted_doc_count == 0:
                logger.warning(f"Inga dokumentrader hittades eller togs bort för DocumentID: {document_id} (kan redan ha varit borttaget).")
                return True
            else: # deleted_doc_count is None (indikerar fel)
                logger.error(f"Fel vid försök att radera dokumentet för DocumentID {document_id}.")
                return False

    except Exception as e:
        logger.error(f"❌ Allvarligt fel under borttagningsprocessen för DocumentID {document_id}: {e}", exc_info=True)
        return False

# --- Huvudlogik ---
def main():
    setup_cleanup_logging()

    try:
        global DatabaseManager, DB_CONNECTION_STRING
        from sql.database_manager import DatabaseManager
        from sql.Sql_connection import DB_CONNECTION_STRING
    except ImportError as e_imp:
        logger.critical(f"!!! Kunde inte importera DatabaseManager eller DB_CONNECTION_STRING: {e_imp}. Avslutar. !!!")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Rensar databasdata relaterad till specifika gamla/uppdaterade manualer. Kräver att FAISS-index byggs om efteråt.",
        epilog="Exempel: python cleanup_updated_manual.py \"gammal_manual_v1.pdf\" --delete-logs"
    )
    parser.add_argument("filenames", nargs='+', help="Filnamn (t.ex. 'manual_v1.pdf') på den/de gamla manualerna vars data ska rensas.")
    parser.add_argument("--delete-logs", action="store_true", help="Ta även bort ChatLogs-poster som är kopplade till de specificerade gamla manualerna (via UsedChunks).")
    parser.add_argument("--connection-string", default=None, help="Valfri databasanslutningssträng (överskrider den från sql/Sql_connection.py)")

    args = parser.parse_args()

    conn_string_to_use = args.connection_string or DB_CONNECTION_STRING

    if not conn_string_to_use:
        logger.critical("!!! Ingen giltig databasanslutningssträng tillgänglig. Avslutar. !!!")
        sys.exit(1)

    try:
        db_manager_instance = DatabaseManager(conn_string_to_use)
        logger.info("DatabaseManager-instans skapad.")
    except Exception as e:
        logger.critical(f"!!! Kunde inte initiera DatabaseManager med den angivna anslutningssträngen: {e}. Avslutar. !!!", exc_info=True)
        sys.exit(1)

    logger.info(f"Startar rensning för {len(args.filenames)} fil(er)...")

    overall_success = True
    for filename in args.filenames:
        logger.info(f"\n--- Bearbetar för rensning: '{filename}' ---")
        
        document_id_to_delete = find_document_id(db_manager_instance, filename)

        if document_id_to_delete:
            if not delete_data_for_document(db_manager_instance, document_id_to_delete, args.delete_logs):
                logger.error(f"Misslyckades med att fullständigt rensa data för dokumentet: '{filename}' (ID: {document_id_to_delete}).")
                overall_success = False
            else:
                logger.info(f"Data relaterad till '{filename}' har bearbetats för borttagning.")
        else:
            logger.warning(f"Kunde inte hitta ett DocumentID för '{filename}'. Ingen data rensades för denna fil.")
            overall_success = False

    logger.info("\n" + "="*60)
    logger.info("--- Rensningsprocess Slutförd ---")
    if overall_success:
        logger.info("✅ Alla specificerade filer har bearbetats för rensning (se logg för detaljer).")
    else:
        logger.warning("⚠️ Vissa problem uppstod under rensningsprocessen. Se logg ovan.")

    logger.warning("\n********************************************************************************")
    logger.warning("*** VIKTIGT: Databasen har modifierats.                                      ***")
    logger.warning("*** OM DOKUMENTDATA HAR TAGITS BORT, MÅSTE DU NU BYGGA OM FAISS-INDEXEN!     ***")
    logger.warning("*** Kör: python run.py (utan --skip-index) för att synkronisera indexen.   ***")
    logger.warning("********************************************************************************")

if __name__ == "__main__":
    main()