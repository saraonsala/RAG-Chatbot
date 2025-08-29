# backend/faiss_indexer.py

import os
import json
import logging
from datetime import datetime, timezone
import faiss
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer
import time
import uuid
from typing import Optional, List, Any, Callable, Tuple, Dict
import sys

# --- Importera DatabaseManager (med mock som fallback) ---
try:
    from sql.database_manager import DatabaseManager
except ImportError:
    logging.warning("⚠️ FaissIndexer: DatabaseManager-modulen kunde inte importeras. Databasfunktioner kommer inte att fungera. En mock-klass används.")
    class DatabaseManager:
        def __init__(self, connection_string: Optional[str]):
            self.connection_string = connection_string
            if connection_string:
                 logging.warning("⚠️ FaissIndexer: DatabaseManager-mocken initierades med en anslutningssträng, men den riktiga modulen importerades inte.")
        def fetch_many(self, *args, **kwargs) -> List[Any]:
             if self.connection_string: logging.error("❌ FaissIndexer Mock DB: fetch_many anropad.")
             return []
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): return False


# --- Konfiguration & Loggning ---
logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = 'KBLab/sentence-bert-swedish-cased'
DEFAULT_EMBEDDING_BATCH_SIZE = 64
INDEX_ROOT_DIR = "rag_index_storage"
DEFAULT_CHUNK_FOLDER = "chunk_output"
DEFAULT_DOC_INDEX_SUBDIR = "document_index"
DEFAULT_DOC_INDEX_FILENAME = "vector_index.faiss"
DEFAULT_DOC_MAPPING_FILENAME = "index_to_metadata.json"
DEFAULT_CHATLOG_TABLE = "ChatLogs"
DEFAULT_CHATLOG_ID_COL = "ChatLogID"
DEFAULT_CHATLOG_Q_COL = "UserQuestion"
DEFAULT_CHATLOG_INDEX_SUBDIR = "chatlog_index"
DEFAULT_CHATLOG_INDEX_FILENAME = "chatlog_index.faiss"
DEFAULT_CHATLOG_MAPPING_FILENAME = "chatlog_mapping.json"


class FaissIndexer:
    def __init__(self,
                    embedding_model_name: str = DEFAULT_EMBEDDING_MODEL,
                    embedding_batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
                    chunk_folder: str = DEFAULT_CHUNK_FOLDER,
                    index_root_dir: str = INDEX_ROOT_DIR,
                    doc_index_subdir: str = DEFAULT_DOC_INDEX_SUBDIR,
                    doc_index_filename: str = DEFAULT_DOC_INDEX_FILENAME,
                    doc_mapping_filename: str = DEFAULT_DOC_MAPPING_FILENAME,
                    db_manager: Optional[DatabaseManager] = None,
                    chatlog_table: str = DEFAULT_CHATLOG_TABLE,
                    chatlog_id_col: str = DEFAULT_CHATLOG_ID_COL,
                    chatlog_question_col: str = DEFAULT_CHATLOG_Q_COL,
                    chatlog_index_subdir: str = DEFAULT_CHATLOG_INDEX_SUBDIR,
                    chatlog_index_filename: str = DEFAULT_CHATLOG_INDEX_FILENAME,
                    chatlog_mapping_filename: str = DEFAULT_CHATLOG_MAPPING_FILENAME):

            self.embedding_model_name = embedding_model_name
            self.embedding_batch_size = embedding_batch_size
            self.model: Optional[SentenceTransformer] = None

            self.chunk_folder = Path(chunk_folder)
            self.index_root_dir = Path(index_root_dir)

            try:
                self.index_root_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"✅ Index-rotmapp säkerställd/skapad: {self.index_root_dir.resolve()}")
            except Exception as e:
                logger.critical(f"‼️ Kunde inte skapa/säkerställa index-rotmapp: {self.index_root_dir.resolve()}. Fel: {e}", exc_info=True)

            self.doc_index_dir = self.index_root_dir / doc_index_subdir
            self.doc_index_file = self.doc_index_dir / doc_index_filename
            self.doc_mapping_file = self.doc_index_dir / doc_mapping_filename

            self.db_manager = db_manager
            self.chatlog_table = chatlog_table
            self.chatlog_id_col = chatlog_id_col
            self.chatlog_question_col = chatlog_question_col
            self.chatlog_index_dir = self.index_root_dir / chatlog_index_subdir
            self.chatlog_index_file = self.chatlog_index_dir / chatlog_index_filename
            self.chatlog_mapping_file = self.chatlog_index_dir / chatlog_mapping_filename

            logger.info(f"FaissIndexer initierad. Dokumentindex: {self.doc_index_dir}, Chatlogindex: {self.chatlog_index_dir}")

            if not self.db_manager or not self.db_manager.connection_string:
                logger.warning("⚠️ Ingen DatabaseManager eller giltig anslutningssträng. Chatlog-indexering kommer inte vara tillgänglig.")

    def _load_embedding_model(self) -> bool:
        if self.model is None:
            logger.info(f"Laddar Sentence Transformer-modell: {self.embedding_model_name}...")
            try:
                start_time = time.time()
                self.model = SentenceTransformer(self.embedding_model_name)
                load_time = time.time() - start_time
                logger.info(f"✅ Modell '{self.embedding_model_name}' laddad på {load_time:.2f} sekunder.")
                return True
            except Exception as e:
                logger.error(f"❌ Kunde inte ladda modellen '{self.embedding_model_name}': {e}", exc_info=True)
                self.model = None
                return False
        return True

    def _generate_embeddings(self, texts: List[str]) -> Tuple[Optional[np.ndarray], Optional[List[int]]]:
        if not texts:
            logger.warning("⚠️ Inga texter att generera embeddings för.")
            return None, None
        if not self._load_embedding_model() or self.model is None:
            logger.error("❌ Kan inte generera embeddings, modellen kunde inte laddas.")
            return None, None

        valid_texts_with_indices = [(i, text) for i, text in enumerate(texts) if isinstance(text, str) and text.strip()]
        if not valid_texts_with_indices:
             logger.warning("⚠️ Inga giltiga texter (icke-tomma strängar) kvar efter filtrering för embedding.")
             return None, None

        original_indices = [item[0] for item in valid_texts_with_indices]
        valid_texts_for_model = [item[1] for item in valid_texts_with_indices]

        logger.info(f"🧠 Genererar embeddings för {len(valid_texts_for_model)} av {len(texts)} texter (Batch: {self.embedding_batch_size})...")
        try:
            embeddings = self.model.encode(valid_texts_for_model, show_progress_bar=True, batch_size=self.embedding_batch_size)
            logger.info(f"✅ Embeddings genererade. Shape: {embeddings.shape if embeddings is not None else 'N/A'}")
            return embeddings, original_indices
        except Exception as e:
            logger.error(f"❌ Fel under generering av embeddings: {e}", exc_info=True)
            return None, None

    def _create_and_save_index(self,
                                items: List[Any],
                                text_attribute: str,
                                output_index_file: Path,
                                output_mapping_file: Path,
                                metadata_extractor: Callable[[Any], Any],
                                index_type: str = "IndexFlatL2") -> bool:
            logger.debug(f"--- _create_and_save_index: Startar för {output_index_file.parent.name} (Text Attr: '{text_attribute}') ---")
            if not items:
                logger.warning(f"--- _create_and_save_index: 'items' är tomma. Ingen indexering utförs. ---")
                return False

            texts_for_embedding: List[str] = [item.get(text_attribute, "") if isinstance(item, dict) else getattr(item, text_attribute, "") for item in items]
            
            embeddings, original_indices = self._generate_embeddings(texts_for_embedding)
            
            if embeddings is None or not original_indices:
                logger.error(f"❌ Indexering avbruten för {output_index_file.parent.name}: Kunde inte generera embeddings.")
                return False
            
            logger.debug(f"--- _create_and_save_index: Fick {embeddings.shape[0]} embeddings för {len(original_indices)} original_indices ---")

            metadata_map = []
            for original_item_idx in original_indices:
                item = items[original_item_idx]
                full_text_content = texts_for_embedding[original_item_idx]
                extracted_metadata = metadata_extractor(item) 

                map_entry: Dict[str, Any] = {
                    "id": item.get("id") if isinstance(item, dict) else f"generated_id_{len(metadata_map)}",
                    "full_text": full_text_content,
                }
                
                if text_attribute == "text": # Dokument-chunks
                    map_entry["original_metadata"] = extracted_metadata
                elif text_attribute == "question": # Chatlogs
                    map_entry["chatlog_id"] = extracted_metadata
                    # Säkerställ att toppnivå-ID är samma som chatlog_id för konsistens
                    map_entry["id"] = extracted_metadata
                else:
                    map_entry["extracted_data"] = extracted_metadata

                metadata_map.append(map_entry)
            
            logger.debug(f"--- _create_and_save_index: Skapade metadata_map med {len(metadata_map)} element. ---")

            dimension = embeddings.shape[1]
            try:
                faiss_index = faiss.IndexFlatL2(dimension)
                faiss_index.add(embeddings.astype(np.float32))
                logger.info(f"✅ {faiss_index.ntotal} vektorer lades till i FAISS-indexet.")

                if faiss_index.ntotal != len(metadata_map):
                    logger.critical(f"‼️ KRITISKT FEL: Antal i FAISS-index ({faiss_index.ntotal}) != Antal i metadata_map ({len(metadata_map)}). AVBRYTER. ‼️")
                    return False
            except Exception as e:
                logger.error(f"❌ Fel vid skapande/population av FAISS-index: {e}", exc_info=True)
                return False

            try:
                output_index_file.parent.mkdir(parents=True, exist_ok=True)
                for f_path in [output_index_file, output_mapping_file]:
                    if f_path.exists(): f_path.unlink()

                faiss.write_index(faiss_index, str(output_index_file))
                
                mapping_data_to_save = {
                    "creation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "embedding_model_name": self.embedding_model_name,
                    "faiss_index_type_saved": index_type,
                    "mapping_item_count": len(metadata_map),
                    "mapping": metadata_map
                }
                with open(output_mapping_file, "w", encoding="utf-8") as f:
                    json.dump(mapping_data_to_save, f, ensure_ascii=False, indent=2)

                logger.info(f"✅ Index och mappning sparade för '{output_index_file.parent.name}'.")
                return True
            except Exception as e:
                logger.error(f"❌ Fel under sparning av index/mappningsfiler: {e}", exc_info=True)
                return False

    def _load_document_chunks(self) -> List[Dict[str, Any]]:
        logger.info(f"📥 Läser in dokument-chunks från JSON-filer i: {self.chunk_folder.resolve()}")
        if not self.chunk_folder.is_dir():
            logger.error(f"❌ Chunk-mappen finns inte: {self.chunk_folder}")
            return []

        all_chunks: List[Dict[str, Any]] = []
        json_files = list(self.chunk_folder.glob("*_chunks.json"))
        if not json_files:
            logger.warning(f"⚠️ Inga '*_chunks.json'-filer hittades i {self.chunk_folder}.")
            return []

        logger.info(f"🔍 Hittade {len(json_files)} JSON-filer för dokument-chunks.")
        for file_path in json_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f: data = json.load(f)
                
                if isinstance(data, dict) and "chunks" in data and isinstance(data["chunks"], list):
                    loaded_chunks = data["chunks"]
                    json_timestamp = data.get("creation_timestamp_utc", "N/A")
                    source_json_filename = file_path.name

                    for chunk in loaded_chunks:
                        if isinstance(chunk, dict):
                            if "metadata" not in chunk or not isinstance(chunk["metadata"], dict): chunk["metadata"] = {}
                            chunk["metadata"]["_source_json_file"] = source_json_filename
                            chunk["metadata"]["_source_json_creation_timestamp_utc"] = json_timestamp
                            if "text" not in chunk: chunk["text"] = ""
                            all_chunks.append(chunk)
                    logger.debug(f"✅ Laddade {len(loaded_chunks)} chunks från {file_path.name}")
                else:
                    logger.warning(f"⚠️ Ogiltigt format i {file_path.name}. Hoppar över.")
            except Exception as e:
                logger.error(f"❌ Fel vid läsning av {file_path.name}: {e}", exc_info=True)

        logger.info(f"📦 Totalt {len(all_chunks)} dokument-chunks laddade.")
        return all_chunks

    def create_document_index(self, index_type: str = "IndexFlatL2") -> bool:
        logger.info("\n--- Startar indexering av DOKUMENT-CHUNKS ---")
        loaded_chunks = self._load_document_chunks()
        if not loaded_chunks:
            logger.warning("Indexering av dokument: Inga chunks laddades. Inget index skapas.")
            return True # Inte ett fel, bara inget att göra

        def doc_metadata_extractor(chunk_item: Dict[str, Any]) -> Dict[str, Any]:
             return chunk_item.get("metadata", {}) 

        success = self._create_and_save_index(
            items=loaded_chunks,
            text_attribute="text",
            output_index_file=self.doc_index_file,
            output_mapping_file=self.doc_mapping_file,
            metadata_extractor=doc_metadata_extractor,
            index_type=index_type
        )

        if success: logger.info("--- ✅ Indexering av DOKUMENT-CHUNKS klar ---")
        else: logger.error("--- ❌ Indexering av DOKUMENT-CHUNKS misslyckades ---")
        return success

    def _load_chatlog_data(self) -> List[Dict[str, Any]]:
        if not self.db_manager or not self.db_manager.connection_string:
            logger.warning("Kan inte ladda chatlogs, DatabaseManager ej konfigurerad.")
            return []

        logger.info(f"📥 Läser in chatlog-data från DB: '{self.chatlog_table}'...")
        chat_logs: List[Dict[str, Any]] = []
        try:
            query = f'SELECT "{self.chatlog_id_col}", "{self.chatlog_question_col}" FROM "{self.chatlog_table}" WHERE "{self.chatlog_question_col}" IS NOT NULL AND LTRIM(RTRIM("{self.chatlog_question_col}")) <> \'\''
            logger.debug(f"Exekverar SQL för chattloggar: {query}")
            
            rows = self.db_manager.fetch_many(query)
            if rows:
                for row_tuple in rows:
                    if len(row_tuple) >= 2:
                        chat_logs.append({"id": row_tuple[0], "question": str(row_tuple[1])})
                    else:
                         logger.warning(f"Rad från chattloggar saknade förväntat antal kolumner: {row_tuple}")

            logger.info(f"📦 Hämtade {len(chat_logs)} giltiga rader från chatlog-tabellen.")
        except Exception as e:
             logger.error(f"❌ Fel vid hämtning av chatlogs från DB: {e}", exc_info=True)
        return chat_logs

    def create_chatlog_index(self, index_type: str = "IndexFlatL2") -> bool:
        logger.info("\n--- Startar indexering av CHATLOG-FRÅGOR ---")
        if not self.db_manager or not self.db_manager.connection_string:
             logger.error("❌ Indexering av chatlogs avbruten: DatabaseManager ej konfigurerad.")
             return False

        loaded_logs = self._load_chatlog_data()
        if not loaded_logs:
            logger.warning("Indexering av chatlogs: Ingen data att indexera. Inget index skapas.")
            return True # Inte ett fel, bara inget att göra

        def chatlog_metadata_extractor(log_item: Dict[str, Any]) -> Any:
            return log_item.get("id")

        success = self._create_and_save_index(
            items=loaded_logs,
            text_attribute="question",
            output_index_file=self.chatlog_index_file,
            output_mapping_file=self.chatlog_mapping_file,
            metadata_extractor=chatlog_metadata_extractor,
            index_type=index_type
        )

        if success: logger.info("--- ✅ Indexering av CHATLOG-FRÅGOR klar ---")
        else: logger.error("--- ❌ Indexering av CHATLOG-FRÅGOR misslyckades ---")
        return success


if __name__ == "__main__":
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s [%(name)-20s] [%(levelname)-8s] %(funcName)-25s L:%(lineno)-4d - %(message)s',
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        logger.info("Loggning konfigurerad i FaissIndexer standalone (DEBUG).")

    logger.info("="*50)
    logger.info("      FAISS Indexer Demonstration (Standalone)    ")
    logger.info("="*50)

    DEMO_ROOT = Path("faiss_indexer_standalone_demo_output")
    DEMO_CHUNK_FOLDER = DEMO_ROOT / "chunk_data"
    DEMO_INDEX_STORAGE_ROOT = DEMO_ROOT / "index_storage"
    for p in [DEMO_ROOT, DEMO_CHUNK_FOLDER, DEMO_INDEX_STORAGE_ROOT]:
        p.mkdir(parents=True, exist_ok=True)

    dummy_chunk_file = DEMO_CHUNK_FOLDER / "doc1_chunks.json"
    dummy_data = {
        "creation_timestamp_utc": datetime.now(timezone.utc).isoformat(), "source_base_name": "doc1", "chunk_count": 2,
        "chunks": [
            {"id": str(uuid.uuid4()), "text": "Installationsprocessen för mjukvaran är enkel.", "metadata": {"document_id": "DOC001", "source": "install.pdf"}},
            {"id": str(uuid.uuid4()), "text": "För felsökning, se kapitel fem.", "metadata": {"document_id": "DOC001", "source": "install.pdf"}}
        ]
    }
    with open(dummy_chunk_file, "w", encoding="utf-8") as f: json.dump(dummy_data, f, indent=2)
    logger.info(f"Skapade dummy chunk-fil i: {DEMO_CHUNK_FOLDER.resolve()}")

    # För att testa DB-delen, sätt en giltig anslutningssträng nedan
    DEMO_DB_CONN_STR: Optional[str] = None 
    
    db_mngr: Optional[DatabaseManager] = None
    if DEMO_DB_CONN_STR:
        try:
            from sql.database_manager import DatabaseManager as RealDBManager
            db_mngr = RealDBManager(DEMO_DB_CONN_STR)
            logger.info("Riktig DatabaseManager instansierad för demo.")
        except ImportError:
            logger.warning("Använder mock DatabaseManager för demo.")
            db_mngr = DatabaseManager(DEMO_DB_CONN_STR) 
    else:
        logger.info("Ingen DB-anslutning för demo, använder mock DatabaseManager.")
        db_mngr = DatabaseManager(None)

    indexer = FaissIndexer(
        chunk_folder=str(DEMO_CHUNK_FOLDER),
        index_root_dir=str(DEMO_INDEX_STORAGE_ROOT),
        db_manager=db_mngr
    )

    logger.info("\n>>> Skapar DOKUMENT-index...")
    indexer.create_document_index()
    
    logger.info("\n>>> Skapar CHATLOG-index...")
    indexer.create_chatlog_index()

    logger.info("\n" + "="*50)
    logger.info(f"      Demonstration Avslutad. Resultat finns i: {DEMO_INDEX_STORAGE_ROOT.resolve()}")
    logger.info("="*50)