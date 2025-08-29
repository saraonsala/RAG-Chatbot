# sql/database_manager.py

import pyodbc
import logging
from typing import Optional, List, Any, Tuple

# --- Loggning Setup ---
# Konfiguration (handlers, format, etc.) bör ske i applikationens entry point (t.ex. run.py).
logger = logging.getLogger(__name__)

class DatabaseManager:
    """
    En dedikerad klass för att hantera alla interaktioner med SQL-databasen via pyodbc.
    Hanterar anslutning, exekvering av frågor och felhantering.
    Designad för att användas som en kontextmanager med `with`-satsen.
    """
    def __init__(self, connection_string: Optional[str]):
        """
        Initierar DatabaseManager och försöker ansluta till databasen.

        Args:
            connection_string: ODBC-anslutningssträngen för databasen.

        Raises:
            ValueError: Om anslutningssträngen är tom eller None.
            pyodbc.Error: Om den initiala anslutningen till databasen misslyckas.
        """
        if not connection_string or not connection_string.strip():
            logger.critical("❌ DatabaseManager: Anslutningssträngen får inte vara tom eller None.")
            raise ValueError("Anslutningssträngen får inte vara tom.")
        
        self.connection_string = connection_string
        self.connection: Optional[pyodbc.Connection] = None
        
        try:
            self.connect()
        except pyodbc.Error as e:
            # Återkasta felet så att anropande kod vet att initiering misslyckades
            logger.critical(f"Kunde inte etablera initial databasanslutning under initiering: {e}")
            raise

    def connect(self):
        """Upprättar en anslutning till databasen om ingen aktiv anslutning finns."""
        if self.connection is None: # Försök bara ansluta om det inte redan finns en anslutning
            try:
                self.connection = pyodbc.connect(self.connection_string)
                logger.info("🔗 Databasanslutning upprättad.")
            except pyodbc.Error as ex:
                sqlstate = ex.args[0]
                logger.error(f"❌ Databasanslutningsfel (State: {sqlstate}): {ex}")
                self.connection = None # Säkerställ att connection är None vid fel
                raise # Återkasta för att signalera misslyckande

    def close(self):
        """Stänger den aktiva databasanslutningen om den finns."""
        if self.connection:
            try:
                self.connection.close()
                self.connection = None
                logger.info("🚪 Databasanslutning stängd.")
            except pyodbc.Error as e:
                logger.error(f"Fel vid stängning av databasanslutning: {e}")

    def __enter__(self):
        """Startar kontexthanteraren, säkerställer anslutning."""
        if self.connection is None:
            self.connect() # Försök ansluta om den inte redan är ansluten
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Avslutar kontexthanteraren, stänger anslutningen."""
        self.close()

    def _get_cursor(self) -> pyodbc.Cursor:
        """Hämtar en cursor från den aktiva anslutningen. Försöker återansluta vid behov."""
        if self.connection is None:
            logger.warning("⚠️ Ingen befintlig anslutning, försöker återansluta...")
            self.connect() # Detta kommer att raise Exception om det misslyckas
        
        # Om self.connection fortfarande är None efter connect() (bör inte hända om connect() kastar fel)
        if self.connection is None:
            logger.critical("❌ Kan inte skapa cursor, ingen giltig databasanslutning kunde upprättas.")
            raise ConnectionError("Ingen giltig databasanslutning för att skapa cursor.")
        
        try:
            return self.connection.cursor()
        except pyodbc.Error as e:
            # Detta kan hända om anslutningen har dött utan att `self.connection` är None
            logger.warning(f"Fel vid hämtning av cursor, anslutningen kan ha brutits: {e}. Försöker återansluta en gång till.")
            self.connection = None # Nollställ för att tvinga ny anslutning
            self.connect()
            if self.connection:
                return self.connection.cursor()
            else:
                raise ConnectionError("Misslyckades att återansluta och skapa cursor.")


    def execute_query(self, query: str, params: Optional[tuple] = None) -> Optional[int]:
        """Exekverar en fråga som inte förväntas returnera rader (INSERT, UPDATE, DELETE) och returnerar antalet påverkade rader."""
        try:
            with self._get_cursor() as cursor:
                cursor.execute(query, params if params is not None else ())
                self.connection.commit() # type: ignore
                logger.debug(f"Query exekverad: {query[:100]}..., Rader påverkade: {cursor.rowcount}")
                return cursor.rowcount
        except (pyodbc.Error, ConnectionError) as ex:
            logger.error(f"❌ DB-fel vid execute_query: {ex}. Query: {query[:100]}...", exc_info=True)
            return None # Indikerar fel
    
    def execute_insert_and_get_id(self, insert_query: str, params: Optional[tuple] = None) -> Optional[Any]:
        """
        Exekverar en INSERT-fråga och returnerar det auto-genererade ID:t.
        OBS: Denna implementation är specifik för SQL Server med SCOPE_IDENTITY().
        För andra databaser (t.ex. PostgreSQL, MySQL) behöver logiken anpassas.
        """
        try:
            with self._get_cursor() as cursor:
                # Steg 1: Exekvera INSERT-satsen
                cursor.execute(insert_query, params if params is not None else ())
                
                insert_rowcount = cursor.rowcount
                if insert_rowcount == 0 or insert_rowcount == -1:
                    logger.warning(f"INSERT-satsen påverkade {insert_rowcount} rader. Query: {insert_query[:100]}. SCOPE_IDENTITY() kan returnera NULL.")
                
                # Steg 2: Exekvera SELECT SCOPE_IDENTITY() som en separat fråga i samma transaktion
                cursor.execute("SELECT SCOPE_IDENTITY()")
                inserted_id_row = cursor.fetchone()
                
                new_id: Optional[Any] = None
                if inserted_id_row and inserted_id_row[0] is not None:
                    new_id = inserted_id_row[0]
                
                self.connection.commit() # type: ignore
                
                if new_id is not None:
                    logger.debug(f"INSERT lyckades. Nytt ID: {new_id}. (Ursprunglig INSERT rowcount: {insert_rowcount})")
                else:
                    logger.warning(f"SCOPE_IDENTITY() returnerade NULL. Kontrollera att tabellen har en IDENTITY-kolumn och att inga triggers stör.")
                
                return new_id
        except (pyodbc.Error, ConnectionError) as ex:
            logger.error(f"❌ DB-fel vid execute_insert_and_get_id: {ex}. Ursprunglig INSERT: {insert_query[:100]}...", exc_info=True)
            try:
                if self.connection: self.connection.rollback()
            except Exception as rb_err:
                logger.error(f"Fel vid rollback: {rb_err}")
            return None

    def fetch_one(self, query: str, params: Optional[tuple] = None) -> Optional[Tuple[Any, ...]]:
        """Hämtar en enskild rad från databasen."""
        try:
            with self._get_cursor() as cursor:
                cursor.execute(query, params if params is not None else ())
                row = cursor.fetchone()
                return row
        except (pyodbc.Error, ConnectionError) as ex:
            logger.error(f"❌ DB-fel vid fetch_one: {ex}. Query: {query[:100]}...", exc_info=True)
            return None

    def fetch_many(self, query: str, params: Optional[tuple] = None) -> List[Tuple[Any, ...]]:
        """Hämtar flera rader från databasen."""
        try:
            with self._get_cursor() as cursor:
                cursor.execute(query, params if params is not None else ())
                rows = cursor.fetchall()
                return rows
        except (pyodbc.Error, ConnectionError) as ex:
            logger.error(f"❌ DB-fel vid fetch_many: {ex}. Query: {query[:100]}...", exc_info=True)
            return []

    def execute_many(self, query: str, params_list: List[tuple]) -> Optional[int]:
        """Exekverar en fråga flera gånger med en lista av parametrar."""
        if not params_list:
            logger.debug("execute_many anropad med tom params_list. Ingen operation utförs.")
            return 0
        try:
            with self._get_cursor() as cursor:
                cursor.fast_executemany = True # Prestandaoptimering för pyodbc
                cursor.executemany(query, params_list)
                self.connection.commit() # type: ignore
                logger.debug(f"execute_many exekverad för {len(params_list)} rader. Query: {query[:100]}...")
                return len(params_list) # Returnerar antalet operationer som skickades
        except (pyodbc.Error, ConnectionError) as ex:
            logger.error(f"❌ DB-fel vid execute_many: {ex}. Query: {query[:100]}...", exc_info=True)
            return None