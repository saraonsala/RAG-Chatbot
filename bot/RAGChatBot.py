# bot/RAGChatBot.py

import os
import json
import logging
import numpy as np
import faiss
import requests
import time
from sentence_transformers import SentenceTransformer
from pathlib import Path
import uuid
from typing import Optional, Any, List, Tuple, Dict

# --- Importera DatabaseManager (med mock som fallback) ---
try:
    from sql.database_manager import DatabaseManager
except ImportError:
    logging.warning("⚠️ RAGChatBot: DatabaseManager-modulen kunde inte importeras. Databasfunktioner kommer inte att fungera. En mock-klass används.")

    class DatabaseManager:
        """Mock-klass för DatabaseManager när den riktiga modulen inte kan importeras."""

        def __init__(self, connection_string: Optional[str]):
            self.connection_string = connection_string
            self._logged_warning = False
            if connection_string:
                logging.warning(
                    "⚠️ RAGChatBot: DatabaseManager-mock initierad med anslutningssträng, "
                    "men den riktiga modulen saknas. Alla DB-operationer kommer att misslyckas."
                )

        def _log_mock_call(self, method_name: str):
            """Loggar varning första gången en mock-metod anropas."""
            if not self._logged_warning:
                logging.error(
                    f"❌ RAGChatBot Mock DB: {method_name} anropad men DatabaseManager är en mock. "
                    f"Installera 'pyodbc' och konfigurera DB_CONNECTION_STRING."
                )
                self._logged_warning = True

        def execute_query(self, *args, **kwargs) -> Optional[int]:
            self._log_mock_call("execute_query")
            return None

        def execute_insert_and_get_id(self, *args, **kwargs) -> Optional[Any]:
            self._log_mock_call("execute_insert_and_get_id")
            return None

        def fetch_many(self, *args, **kwargs) -> List[Any]:
            self._log_mock_call("fetch_many")
            return []

        def fetch_one(self, *args, **kwargs) -> Optional[Any]:
            self._log_mock_call("fetch_one")
            return None

        def execute_many(self, *args, **kwargs) -> Optional[int]:
            self._log_mock_call("execute_many")
            return None

        def _validate_identifier(self, identifier: str, identifier_type: str = "identifier") -> str:
            """Mock-version av identifier validation."""
            return identifier

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False


# --- Konfiguration & Loggning ---
INDEX_ROOT_DIR = "rag_index_storage"
LOG_FILE = "rag_chatbot.log"

# Security & Validation Constants
MAX_USER_INPUT_LENGTH = 5000  # Max antal tecken i användarfrågor
MAX_CONTEXT_LENGTH = 50000     # Max total längd på kontext från dokument

DOCUMENT_INDEX_SUBDIR = "document_index"
DOC_INDEX_FILENAME = "vector_index.faiss"
DOC_MAPPING_FILENAME = "index_to_metadata.json"

CHATLOG_INDEX_SUBDIR = "chatlog_index"
CHATLOG_INDEX_FILENAME = "chatlog_index.faiss"
CHATLOG_MAPPING_FILENAME = "chatlog_mapping.json"
DEFAULT_CHATLOG_TABLE = "ChatLogs"
DEFAULT_CHATLOG_ID_COL = "ChatLogID"
DEFAULT_CHATLOG_RESPONSE_COL = "BotResponse"

SYSTEM_PROMPT = """Du är en hjälpsam AI-assistent som svarar på svenska.
Använd enbart informationen som tillhandahålls i 'Kontext' för att svara på frågan.
Kontexten kan innehålla tekniska data i format som NYCKEL=värde1|värde2... eller liknande. Parsa dessa noggrant om frågan kräver det.
Om svaret inte finns i kontexten, svara tydligt att du inte kunde hitta informationen. Om användaren frågar om något är möjligt ('Kan man...'), och du hittar information som bekräftar det, försök också att inkludera en kort sammanfattning av hur det görs, om den informationen finns i den tillhandahållna kontexten.
Svara direkt på frågan utan onödiga inledningar.
Presentera informationen på ett lättförståeligt sätt, och kom ihåg att alltid svara på svenska.
"""

logger = logging.getLogger(__name__)

class RAGChatBot:
    def __init__(self,
                 api_base: str = "http://localhost:1234/v1/chat/completions",
                 embedding_model_name: str = 'KBLab/sentence-bert-swedish-cased',
                 llm_model_name: str = 'lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF',
                 index_root_dir: str = INDEX_ROOT_DIR,
                 document_index_subdir: str = DOCUMENT_INDEX_SUBDIR,
                 doc_index_filename: str = DOC_INDEX_FILENAME,
                 doc_mapping_filename: str = DOC_MAPPING_FILENAME,
                 chatlog_index_subdir: str = CHATLOG_INDEX_SUBDIR,
                 chatlog_index_filename: str = CHATLOG_INDEX_FILENAME,
                 chatlog_mapping_filename: str = CHATLOG_MAPPING_FILENAME,
                 db_manager: Optional[DatabaseManager] = None,
                 db_similarity_threshold: float = 0.75,
                 chatlog_table_name: str = DEFAULT_CHATLOG_TABLE,
                 chatlog_id_col_name: str = DEFAULT_CHATLOG_ID_COL,
                 chatlog_response_col_name: str = DEFAULT_CHATLOG_RESPONSE_COL,
                 max_tokens: int = 1200,
                 temperature: float = 0.2,
                 doc_search_top_k: int = 1
                 ):

        self.api_base = api_base
        self.llm_model_name = llm_model_name
        self.max_tokens = max_tokens
        self.temperature = temperature

        self.db_manager = db_manager
        self.db_similarity_threshold = db_similarity_threshold
        self.chatlog_table_name = chatlog_table_name
        self.chatlog_id_col = chatlog_id_col_name
        self.chatlog_response_col = chatlog_response_col_name

        self.embedding_model: Optional[SentenceTransformer] = None
        self.embedding_model_name = embedding_model_name

        self.index_root_dir = Path(index_root_dir)
        self.doc_index_dir = self.index_root_dir / document_index_subdir
        self.doc_index_file = self.doc_index_dir / doc_index_filename
        self.doc_mapping_file = self.doc_index_dir / doc_mapping_filename
        self.chatlog_index_dir = self.index_root_dir / chatlog_index_subdir
        self.chatlog_index_file = self.chatlog_index_dir / chatlog_index_filename
        self.chatlog_mapping_file = self.chatlog_index_dir / chatlog_mapping_filename

        self.doc_index: Optional[faiss.Index] = None
        self.doc_index_to_metadata_map: List[Dict[str, Any]] = []
        self.chatlog_index: Optional[faiss.Index] = None
        self.chatlog_index_to_metadata_map: List[Dict[str, Any]] = []

        self.doc_search_top_k = doc_search_top_k

        logger.info("RAGChatBot initierad.")
        if not self.db_manager or not self.db_manager.connection_string:
             logger.warning("DBManager ej konfigurerad. Databasfunktioner (Chatlog RAG/Sparning) är inaktiva.")

        self._load_embedding_model()
        self._load_document_index()
        self._load_chatlog_index()

    def _load_embedding_model(self) -> bool:
        if self.embedding_model is not None:
            return True
        logger.info(f"Laddar embedding-modell: {self.embedding_model_name}...")
        try:
            start_time = time.time()
            self.embedding_model = SentenceTransformer(self.embedding_model_name)
            load_time = time.time() - start_time
            logger.info(f"✅ Embedding-modell '{self.embedding_model_name}' laddad på {load_time:.2f} sekunder.")
            return True
        except Exception as e:
            logger.error(f"❌ Kunde inte ladda embedding-modellen '{self.embedding_model_name}': {e}", exc_info=True)
            self.embedding_model = None
            return False

    def _load_index_and_mapping(self, index_file: Path, mapping_file: Path, index_type_name: str) -> Tuple[Optional[faiss.Index], List[Dict[str, Any]]]:
        logger.info(f"Försöker ladda {index_type_name}-index från: {index_file.parent.resolve()}")
        loaded_map_list: List[Dict[str, Any]] = []

        if not index_file.exists() or not mapping_file.exists():
            logger.warning(f"⚠️ Filer för {index_type_name}-index saknas. Index: '{index_file.name}' ({index_file.exists()}), Mapping: '{mapping_file.name}' ({mapping_file.exists()})")
            return None, loaded_map_list

        try:
            faiss_idx = faiss.read_index(str(index_file))
            logger.debug(f"FAISS-index '{index_file.name}' laddat med {faiss_idx.ntotal} vektorer.")
        except Exception as e:
            logger.error(f"❌ Fel vid laddning av FAISS-index {index_file.name}: {e}", exc_info=True)
            return None, loaded_map_list

        try:
            with open(mapping_file, "r", encoding="utf-8") as f:
                loaded_raw_data = json.load(f)
            if isinstance(loaded_raw_data, dict) and "mapping" in loaded_raw_data:
                loaded_map_list = loaded_raw_data["mapping"]
                logger.info(f"✅ Mappningsdata ({len(loaded_map_list)} element) laddad från {mapping_file.name}.")
            else:
                logger.error(f"❌ Ogiltigt format i mappningsfil {mapping_file.name}. Förväntade dict med 'mapping'-nyckel.")
                return faiss_idx, []
        except Exception as e:
            logger.error(f"❌ Oväntat fel vid laddning av mappningsfil {mapping_file.name}: {e}", exc_info=True)
            return faiss_idx, []

        if faiss_idx and faiss_idx.ntotal != len(loaded_map_list):
             logger.error(f"❌ Mismatch! {index_type_name}-index ({faiss_idx.ntotal} vektorer) och mappning ({len(loaded_map_list)} element) har olika storlek.")
             return None, []

        return faiss_idx, loaded_map_list

    def _load_document_index(self) -> bool:
        self.doc_index, self.doc_index_to_metadata_map = self._load_index_and_mapping(
            self.doc_index_file, self.doc_mapping_file, "dokument"
        )
        return self.doc_index is not None

    def _load_chatlog_index(self) -> bool:
        self.chatlog_index, self.chatlog_index_to_metadata_map = self._load_index_and_mapping(
            self.chatlog_index_file, self.chatlog_mapping_file, "chatlog"
        )
        return self.chatlog_index is not None

    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        if not self.embedding_model:
            logger.error("❌ Embedding-modellen är inte tillgänglig.")
            return None
        if not isinstance(text, str) or not text.strip():
            logger.warning(f"⚠️ Försöker generera embedding för tom/ogiltig text.")
            return None
        try:
            embedding_batch = self.embedding_model.encode([text], convert_to_numpy=True)
            if isinstance(embedding_batch, np.ndarray) and embedding_batch.ndim == 2:
                return embedding_batch[0]
            logger.error(f"❌ Oväntat format från embedding-modell.")
            return None
        except Exception as e:
            logger.error(f"❌ Fel vid generering av embedding: {e}", exc_info=True)
            return None

    def _search_document_index(self, query_embedding: np.ndarray, top_k: int) -> List[Dict[str, Any]]:
        if self.doc_index is None or not self.doc_index_to_metadata_map:
            logger.warning("Dokumentindex ej laddat. Hoppar över dokumentsökning.")
            return []
        if query_embedding is None or query_embedding.ndim == 0:
             logger.error("Kan inte söka med tom query embedding.")
             return []

        logger.info(f"🔍 Söker i DOKUMENT-index (top_k={top_k})...")
        try:
            distances, indices = self.doc_index.search(query_embedding.reshape(1, -1).astype(np.float32), top_k)
            results: List[Dict[str, Any]] = []
            if indices is not None and len(indices[0]) > 0:
                 for i, faiss_idx_pos in enumerate(indices[0]):
                     if 0 <= faiss_idx_pos < len(self.doc_index_to_metadata_map):
                         distance = float(distances[0][i])
                         retrieved_map_entry = self.doc_index_to_metadata_map[faiss_idx_pos]
                         
                         results.append({
                             "id": retrieved_map_entry.get("id"),
                             "full_text": retrieved_map_entry.get("full_text"),
                             "original_metadata": retrieved_map_entry.get("original_metadata", {}),
                             "distance": distance
                         })
                     else:
                          logger.warning(f"⚠️ Ogiltigt FAISS-index {faiss_idx_pos} från dokumentsökning.")
            logger.info(f"✅ Hittade {len(results)} resultat i dokumentindex.")
            results.sort(key=lambda x: x["distance"])
            return results
        except Exception as e:
            logger.error(f"❌ Fel vid sökning i dokumentindex: {e}", exc_info=True)
            return []

    def _build_context_from_docs(self, doc_search_results: List[Dict[str, Any]]) -> Tuple[str, List[str], List[Dict[str, Any]]]:
        samlad_text = ""
        kallor: List[str] = []
        used_chunks_for_log: List[Dict[str, Any]] = []
        seen_chunk_ids = set()

        if not doc_search_results:
            return samlad_text, kallor, used_chunks_for_log

        for result_item in doc_search_results:
            full_text_content = result_item.get("full_text")
            pdf_processor_metadata = result_item.get("original_metadata", {}) 
            chunk_id_from_result = result_item.get("id")

            if not full_text_content or not chunk_id_from_result or chunk_id_from_result in seen_chunk_ids:
                continue

            samlad_text += full_text_content.strip() + "\n\n---\n\n"
            
            source = pdf_processor_metadata.get("source", "Okänd källa")
            page_hint = pdf_processor_metadata.get("page_hint")
            source_ref = f"{source}"
            if page_hint is not None:
                source_ref += f" (sida {page_hint})"
            kallor.append(source_ref)

            document_id_for_log = pdf_processor_metadata.get('document_id', 'Okänt DokID')
            used_chunks_for_log.append({'chunk_id': chunk_id_from_result, 'document_id': document_id_for_log})
            seen_chunk_ids.add(chunk_id_from_result)
            
        return samlad_text.strip(), kallor, used_chunks_for_log

    def _search_chat_logs(self, query_embedding: np.ndarray, top_k: int = 1) -> Tuple[Optional[str], Optional[Any]]:
        if self.chatlog_index is None or not self.chatlog_index_to_metadata_map:
            logger.debug("Chatlog-index ej laddat, hoppar över DB RAG.")
            return None, None
        if not self.db_manager or not self.db_manager.connection_string:
            logger.debug("DBManager ej konfigurerad, kan ej hämta cachat svar.")
            return None, None
        if query_embedding is None or query_embedding.ndim == 0:
            logger.error("Tom query embedding för chatlog-sökning.")
            return None, None

        logger.info(f"🔍 Söker i CHATLOG-index (top_k={top_k})...")
        try:
            distances, indices = self.chatlog_index.search(query_embedding.reshape(1, -1).astype(np.float32), top_k)

            if indices is not None and len(indices[0]) > 0:
                best_match_idx_pos = indices[0][0]
                best_match_dist = float(distances[0][0])
                similarity = 1 / (1 + best_match_dist)

                logger.info(f" Bästa träff i chatlog: FAISS Pos {best_match_idx_pos}, Avstånd {best_match_dist:.4f}, Likhet {similarity:.4f}")

                if similarity >= self.db_similarity_threshold:
                    if 0 <= best_match_idx_pos < len(self.chatlog_index_to_metadata_map):
                        chat_log_map_entry = self.chatlog_index_to_metadata_map[best_match_idx_pos]
                        actual_chat_log_id = chat_log_map_entry.get("chatlog_id") or chat_log_map_entry.get("id")

                        if actual_chat_log_id is None:
                            logger.warning(f"Kunde inte extrahera chatlog ID från mappning: {chat_log_map_entry}")
                            return None, None

                        logger.debug(f"Hämtar svar för ChatLogID: {actual_chat_log_id} från DB...")

                        # Validera tabell- och kolumnnamn för att förhindra SQL injection
                        try:
                            safe_table = self.db_manager._validate_identifier(self.chatlog_table_name, "table")
                            safe_response_col = self.db_manager._validate_identifier(self.chatlog_response_col, "column")
                            safe_id_col = self.db_manager._validate_identifier(self.chatlog_id_col, "column")
                        except (ValueError, AttributeError) as validation_err:
                            logger.error(f"❌ SQL identifier validation failed: {validation_err}")
                            return None, None

                        query = f'SELECT "{safe_response_col}" FROM "{safe_table}" WHERE "{safe_id_col}" = ?'
                        try:
                            row = self.db_manager.fetch_one(query, (actual_chat_log_id,))
                            if row and row[0] is not None and str(row[0]).strip():
                                logger.info(f"✅ Använder sparat svar från DB för ChatLogID: {actual_chat_log_id}")
                                return str(row[0]), actual_chat_log_id
                            else:
                                logger.warning(f"⚠️ Hittade ChatLogID {actual_chat_log_id} men inget giltigt svar i DB.")
                        except Exception as db_err:
                            logger.error(f"❌ Fel vid DB-hämtning för ChatLogID {actual_chat_log_id}: {db_err}", exc_info=True)
                    else:
                        logger.warning(f"⚠️ Ogiltigt FAISS-index {best_match_idx_pos} från chatlog-sökning.")
                else:
                    logger.info(f"Likhet ({similarity:.4f}) < Tröskel ({self.db_similarity_threshold}). Använder inte svar från chatlog.")
            else:
                logger.info("Inga resultat från sökning i chatlog-index.")
        except Exception as e:
            logger.error(f"❌ Fel vid sökning i chatlog-index: {e}", exc_info=True)
        return None, None

    def generate_response(self, user_input: str) -> str:
        # Validera input
        if not user_input or not user_input.strip():
            return "⚠️ Vänligen skriv en fråga."

        # Begränsa input-längd för att förhindra DoS och överdrivna API-kostnader
        if len(user_input) > MAX_USER_INPUT_LENGTH:
            logger.warning(f"⚠️ User input too long: {len(user_input)} chars (max: {MAX_USER_INPUT_LENGTH})")
            return f"⚠️ Frågan är för lång. Max {MAX_USER_INPUT_LENGTH} tecken tillåts (du skrev {len(user_input)} tecken)."

        # Sanitera input - ta bort potentiellt farliga tecken (behåll vanlig text)
        user_input = user_input.strip()

        if not self.embedding_model:
             return "🚨 Internt fel: Embedding-modell ej laddad."

        default_no_answer = "🤖 Ursäkta, jag kunde inte hitta ett svar baserat på tillgänglig information."
        final_response = default_no_answer
        
        cached_response_source_id: Optional[Any] = None 
        current_interaction_log_id: Optional[Any] = None

        query_embedding = self._get_embedding(user_input)
        if query_embedding is None:
            self.save_chat_log(user_input, "🚨 Internt fel: Kunde inte skapa text-embedding för frågan.", None, None)
            return "🚨 Internt fel: Kunde inte skapa text-embedding för frågan."

        cached_response, source_log_id = self._search_chat_logs(query_embedding)
        
        if cached_response:
            if not cached_response.strip().startswith(("🚨", "🤖 Ursäkta")):
                logger.info(f"✅ Använder cachat svar från chatlog (Ursprungs-ID: {source_log_id}).")
                final_response = cached_response + "\n\n*(Svar hämtat från en tidigare liknande fråga)*"
                cached_response_source_id = source_log_id
                
                self.save_chat_log(user_input, final_response, query_embedding, cached_response_source_id)
                return final_response
            else:
                logger.info(f"Hittade matchande fråga (ID: {source_log_id}) men svaret var ett standard-/felmeddelande. Ignorerar.")
        
        logger.info("Ingen tillräckligt bra/giltig träff i chatlog, fortsätter med dokument-RAG.")
        samlad_text_kontext = ""
        kallor_list: List[str] = []
        used_chunks_list: List[Dict[str, Any]] = []

        if self.doc_index and self.doc_index_to_metadata_map:
            doc_results = self._search_document_index(query_embedding, top_k=self.doc_search_top_k)
            if doc_results:
                samlad_text_kontext, kallor_list, used_chunks_list = self._build_context_from_docs(doc_results)

                # Validera att kontexten inte är för lång
                if len(samlad_text_kontext) > MAX_CONTEXT_LENGTH:
                    logger.warning(f"⚠️ Context too long ({len(samlad_text_kontext)} chars), truncating to {MAX_CONTEXT_LENGTH}")
                    samlad_text_kontext = samlad_text_kontext[:MAX_CONTEXT_LENGTH] + "\n\n...[Kontext trunkerad p.g.a. längd]"
        else:
            logger.warning("Dokumentindex ej laddat. Försöker LLM utan RAG-kontext.")

        prompt_for_llm = f"Kontext:\n{samlad_text_kontext or 'Ingen specifik kontext från dokument hittades.'}\n\nFråga: {user_input.strip()}\n\nSvar:"
        
        llm_generated_text = self._generate_with_lmstudio(prompt_for_llm)
        
        if llm_generated_text.strip() and not llm_generated_text.startswith("🚨"):
            final_response = llm_generated_text.strip()
            if kallor_list:
                 final_response += "\n\n---\n*Baserat på:* " + ", ".join(sorted(list(set(kallor_list))))
        
        current_interaction_log_id = self.save_chat_log(user_input, final_response, query_embedding, None)
        
        if current_interaction_log_id and used_chunks_list:
            self.save_used_chunks(current_interaction_log_id, used_chunks_list)
            
        return final_response

    def _generate_with_lmstudio(self, prompt_for_user_role: str) -> str:
        logger.info(f"Skickar prompt till LLM API: {self.api_base}...")
        payload = { "model": self.llm_model_name, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt_for_user_role}], "temperature": self.temperature, "max_tokens": self.max_tokens }
        try:
            logger.debug(f"---> LLM API Payload:\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n<---")
            response = requests.post(self.api_base, headers={"Content-Type": "application/json"}, json=payload, timeout=300)
            response.raise_for_status()
            data = response.json()
            if "choices" in data and data["choices"] and "message" in data["choices"][0] and "content" in data["choices"][0]["message"]:
                svar = str(data["choices"][0]["message"]["content"])
                logger.info("✅ Svar mottaget från LLM API.")
                return svar
            logger.warning(f"⚠️ LLM API svarade med oväntat format: {str(data)[:500]}")
            return "🤖 Språkmodellen returnerade ett svar i oväntat format."
        except requests.exceptions.Timeout:
            logger.error("❌ Timeout vid anrop till LLM API.")
            return "🚨 Kunde inte nå språkmodellen (timeout)."
        except requests.exceptions.HTTPError as http_err:
            err_details = f"HTTP-fel {http_err.response.status_code}: {http_err.response.text[:200]}"
            logger.error(f"❌ {err_details} vid LLM API-anrop.")
            return f"🚨 Fel vid kommunikation med språkmodellen ({err_details})."
        except Exception as e:
            logger.error(f"❌ Oväntat fel vid LLM API-generering: {e}", exc_info=True)
            return "🚨 Ett oväntat internt fel uppstod."

    def save_chat_log(self, question: str, response_text: str, question_embedding_arr: Optional[np.ndarray], cached_response_ref_id: Optional[Any]) -> Optional[Any]:
        if not self.db_manager or not self.db_manager.connection_string:
             logger.debug("DBManager ej konfigurerad, kan ej spara chatlog.")
             return None

        # Validera tabellnamn
        try:
            safe_table = self.db_manager._validate_identifier(self.chatlog_table_name, "table")
        except (ValueError, AttributeError) as validation_err:
            logger.error(f"❌ SQL table validation failed: {validation_err}")
            return None

        embedding_bytes = question_embedding_arr.astype(np.float32).tobytes() if question_embedding_arr is not None else None

        sql = f'INSERT INTO "{safe_table}" (UserQuestion, BotResponse, UserQuestionEmbedding, CachedResponseID) VALUES (?, ?, ?, ?)'
        params = (question, response_text, embedding_bytes, cached_response_ref_id)
        
        try:
            new_id = self.db_manager.execute_insert_and_get_id(sql, params)
            if new_id is not None:
                 logger.info(f"💾 Chatlogg sparad med nytt ID: {new_id} (Ref ID: {cached_response_ref_id}).")
                 return new_id
            else:
                 logger.warning(f"⚠️ Chatlogg sparades inte (execute_insert_and_get_id returnerade None).")
                 return None
        except Exception as e:
            logger.error(f"❌ Misslyckades med att spara chatlogg: {e}", exc_info=True)
            return None

    def save_used_chunks(self, chat_log_id: Any, chunks_used: List[Dict[str, Any]]) -> bool:
        if not self.db_manager or not self.db_manager.connection_string:
            logger.debug("DBManager ej konfigurerad, kan ej spara använda chunks.")
            return False
        if not chunks_used or chat_log_id is None:
            if not chunks_used: logger.debug("Inga använda chunks att spara.")
            else: logger.error("Kan inte spara UsedChunks utan ett ChatLogID.")
            return False

        sql = "INSERT INTO UsedChunks (ChatLogID, ChunkID, DocumentID) VALUES (?, ?, ?)"
        tuples_for_db: List[Tuple[Any, str, str]] = [
            (chat_log_id, str(chunk.get('chunk_id','')), str(chunk.get('document_id','')))
            for chunk in chunks_used if chunk.get('chunk_id') and chunk.get('document_id')
        ]
        
        if not tuples_for_db:
            logger.warning("Inga giltiga chunk-tuples att spara till UsedChunks.")
            return True # Inget att spara, men inte ett fel.
        
        try:
            rows_affected = self.db_manager.execute_many(sql, tuples_for_db)
            if rows_affected is not None and rows_affected == len(tuples_for_db):
                logger.info(f"💾 {len(tuples_for_db)} använda chunks sparade för ChatLogID {chat_log_id}.")
                return True
            else:
                logger.warning(f"Använda chunks sparades inte korrekt för ChatLogID {chat_log_id} (rader: {rows_affected} av {len(tuples_for_db)}).")
                return False
        except Exception as e:
            logger.error(f"❌ Misslyckades spara använda chunks för ChatLogID {chat_log_id}: {e}", exc_info=True)
            return False

if __name__ == "__main__":
    import sys
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)-8s] %(name)-25s %(funcName)-25s L:%(lineno)-4d - %(message)s",
            handlers=[
                logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
                logging.StreamHandler(sys.stdout)
            ]
        )
        logger = logging.getLogger(__name__)
        logger.info(f"Loggning konfigurerad för RAGChatBot standalone.")

    logger.info("="*50)
    logger.info("       RAG ChatBot Demonstration (Standalone)     ")
    logger.info("="*50)

    # DEMO_DB_CONN_STR = 'Driver={ODBC Driver 17 for SQL Server};Server=...;Database=...;Trusted_Connection=yes;'
    DEMO_DB_CONN_STR: Optional[str] = None 
    
    db_mngr_instance: Optional[DatabaseManager] = None
    if DEMO_DB_CONN_STR:
        try:
            from sql.database_manager import DatabaseManager as RealDBManager
            db_mngr_instance = RealDBManager(DEMO_DB_CONN_STR)
        except ImportError:
            db_mngr_instance = DatabaseManager(DEMO_DB_CONN_STR)
    else:
        db_mngr_instance = DatabaseManager(None) # Använd mock

    interactive_bot = RAGChatBot(
        db_manager=db_mngr_instance,
        doc_search_top_k=2,
        db_similarity_threshold=0.70
    )

    if not interactive_bot.embedding_model:
         logger.critical("‼️ Embedding-modell kunde inte laddas. Avslutar.")
         sys.exit(1)
    if not interactive_bot.doc_index and not interactive_bot.chatlog_index:
         logger.warning("‼️ Varken dokument- eller chatlog-index laddades.")

    print("\n" + "="*50)
    print("🧠 Välkommen till Interaktiv RAG Chatbot!")
    print("   Skriv 'avsluta', 'exit', eller 'quit' för att sluta.")
    print("="*50)

    while True:
        try:
            user_question = input("\nDu: ").strip()
            if user_question.lower() in ['avsluta', 'exit', 'quit']: break
            if not user_question: continue

            print("Bot: ⏳ Bearbetar...")
            bot_response = interactive_bot.generate_response(user_question)
            print(f"\nBot svarar:")
            print("-" * 60)
            print(bot_response)
            print("-" * 60)
        except KeyboardInterrupt:
            break
        except Exception as e_loop:
             logger.error(f"🚨 Fel i huvudloopen: {e_loop}", exc_info=True)
             break

    print("\n👋 Hejdå!")
    logger.info("="*50)
    logger.info("       RAG Chatbot Demonstration Avslutad       ")
    logger.info("="*50)