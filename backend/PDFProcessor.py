# backend/PDFProcessor.py

import logging
from datetime import datetime, timezone
import pandas as pd
import os
import re
import json
from pathlib import Path
import sys
import uuid
import fitz  # PyMuPDF
import tiktoken
from typing import Optional, Any, List, Dict, Tuple

# --- Importera DatabaseManager (med mock som fallback) ---
try:
    from sql.database_manager import DatabaseManager
except ImportError:
    logging.warning("⚠️ PDFProcessor: DatabaseManager-modulen kunde inte importeras. Databasfunktioner kommer inte att fungera. En mock-klass används.")
    class DatabaseManager:
        def __init__(self, connection_string: Optional[str]):
            self.connection_string = connection_string
            if connection_string:
                 logging.warning("⚠️ PDFProcessor: DatabaseManager-mocken initierades med en anslutningssträng, men den riktiga modulen importerades inte.")
        def execute_query(self, *args, **kwargs) -> Optional[int]:
             if self.connection_string: logging.error("❌ PDFProcessor Mock DB: execute_query anropad.")
             return 0
        def execute_many(self, *args, **kwargs) -> Optional[int]:
             if self.connection_string: logging.error("❌ PDFProcessor Mock DB: execute_many anropad.")
             return 0
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): return False


# --- Konfiguration ---
DB_DOCUMENTS_TABLE = "Documents"
DB_CHUNKS_TABLE = "DocumentChunks"
MAX_TOKENS_PER_CHUNK = 1200
TOKEN_OVERLAP = 100

TAG_RULES = {
    "installation": ["installera", "installation", "krav", "förutsättningar", "setup"],
    "felsökning": ["felsök", "problem", "åtgärd", "felkod", "error", "troubleshoot"],
    "API": ["API", "endpoint", "JSON", "request", "response", "REST", "integration"],
    "användarkonto": ["inloggning", "lösenord", "konto", "behörighet", "användare", "login"],
    "Windows": ["Windows", "fil", "register", "kör", ".exe", "cmd"],
    "Mac": ["Mac", "Finder", "kommandotolk", "terminal", ".dmg", "bash"]
}

# --- Loggning Setup ---
logger = logging.getLogger(__name__)


# === Hjälpfunktioner ===
def extract_tags_from_text(text: str, tag_rules: Dict[str, List[str]]) -> List[str]:
    """Extraherar taggar baserat på nyckelord i texten."""
    tags = set()
    lower_text = text.lower()
    for tag, keywords in tag_rules.items():
        if any(re.search(r'\b' + re.escape(keyword.lower()) + r'\b', lower_text) for keyword in keywords):
            tags.add(tag)
    return sorted(list(tags))

def get_output_base_name(input_path: Path, base_input_folder: Path) -> str:
    """
    Skapar ett unikt output-filnamn baserat på relativ sökväg och filstam.
    """
    try:
        relative_path = input_path.relative_to(base_input_folder)
        safe_path_part = str(relative_path.parent).replace(os.sep, '_').replace('/', '_')
        if safe_path_part and safe_path_part != '.':
            return f"{safe_path_part}_{input_path.stem}"
        else:
            return input_path.stem
    except ValueError:
        logger.warning(f"⚠️ Input path '{input_path}' är inte relativ till '{base_input_folder}'. Använder bara filnamn som bas.")
        return input_path.stem


# === Huvudklass ===
class PDFProcessor:
    def __init__(self,
                 input_folder: str,
                 cleaned_folder: str,
                 chunk_folder: str,
                 db_manager: Optional[DatabaseManager] = None,
                 doc_table_name: str = DB_DOCUMENTS_TABLE,
                 chunk_table_name: str = DB_CHUNKS_TABLE,
                 max_tokens: int = MAX_TOKENS_PER_CHUNK,
                 token_overlap: int = TOKEN_OVERLAP,
                 tag_rules_override: Optional[Dict[str, List[str]]] = None):

        self.input_folder = Path(input_folder).resolve()
        self.cleaned_folder = Path(cleaned_folder).resolve()
        self.chunk_folder = Path(chunk_folder).resolve()
        
        self.db_manager = db_manager
        self.doc_table_name = doc_table_name
        self.chunk_table_name = chunk_table_name
        self.max_tokens = max_tokens
        self.token_overlap = token_overlap
        self.tag_rules = tag_rules_override if tag_rules_override is not None else TAG_RULES

        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        self.cleaned_folder.mkdir(parents=True, exist_ok=True)
        self.chunk_folder.mkdir(parents=True, exist_ok=True)

        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            self.logger.error(f"❌ Kunde inte ladda tokenizer 'cl100k_base': {e}. Chunking kommer inte att fungera.", exc_info=True)
            self.tokenizer = None

        if not self.is_db_configured:
             self.logger.warning("⚠️ Ingen giltig DatabaseManager-instans konfigurerad. Databaslagring hoppas över.")
        
        self.logger.info(f"PDFProcessor initierad. Input: '{self.input_folder}'")

    @property
    def is_db_configured(self) -> bool:
        """Kontrollerar om databashanteraren är korrekt konfigurerad för användning."""
        return (self.db_manager is not None and
                hasattr(self.db_manager, 'connection_string') and
                self.db_manager.connection_string is not None and
                isinstance(self.db_manager.connection_string, str) and
                self.db_manager.connection_string.strip() != "")

    def extract_and_clean_text_from_files(self) -> List[Tuple[Path, str, Optional[str]]]:
        """
        Extraherar, rensar text från källfiler och returnerar en lista med resultat.
        """
        self.logger.info(f"📄 Startar extrahering och rensning från: {self.input_folder}")
        results: List[Tuple[Path, str, Optional[str]]] = []

        supported_extensions = {'.pdf', '.xlsx', '.xls'}
        
        all_potential_files: List[Path] = []
        try:
            for item in self.input_folder.rglob("*"):
                if item.is_file() and item.suffix.lower() in supported_extensions:
                    all_potential_files.append(item)
        except Exception as e:
            self.logger.error(f"❌ Fel vid genomsökning av filer i {self.input_folder}: {e}", exc_info=True)
            return results

        input_files = sorted(list(set(all_potential_files)))

        if not input_files:
            self.logger.warning(f"⚠️ Inga filer med stödda filtyper ({', '.join(supported_extensions)}) hittades i '{self.input_folder}'.")
            return results

        self.logger.info(f"🔍 Hittade {len(input_files)} filer att bearbeta.")

        for input_file in input_files:
            output_base_name = get_output_base_name(input_file, self.input_folder)
            cleaned_text_for_this_file: Optional[str] = None
            
            try:
                cleaned_txt_path = self.cleaned_folder / f"{output_base_name}.txt"
                should_process = True
                if cleaned_txt_path.exists():
                    try:
                        if cleaned_txt_path.stat().st_mtime > input_file.stat().st_mtime:
                            self.logger.info(f"⏭️  Hoppar över {input_file.name}, rensad fil är aktuell.")
                            with open(cleaned_txt_path, "r", encoding="utf-8") as f:
                                cleaned_text_for_this_file = f.read()
                            should_process = False
                        else:
                            self.logger.info(f"🔄 Rensad fil {cleaned_txt_path.name} är äldre än {input_file.name}. Bearbetar om.")
                    except Exception as stat_err:
                        self.logger.warning(f"⚠️ Fel vid jämförelse av tidsstämplar för {input_file.name}: {stat_err}. Bearbetar om.")

                if not should_process:
                    results.append((input_file, output_base_name, cleaned_text_for_this_file))
                    continue

                self.logger.info(f"📄 Bearbetar: {input_file.relative_to(self.input_folder)}")
                raw_text: Optional[str] = None
                file_type = input_file.suffix.lower()

                if file_type == '.pdf':
                    try:
                        with fitz.open(input_file) as doc:
                            full_text_parts = [f"\n\n[SIDA {p.number + 1}]\n{p.get_text('text', sort=True).strip()}" for p in doc if p.get_text("text").strip()]
                            raw_text = '\n'.join(full_text_parts).strip()
                    except Exception as pdf_err:
                        self.logger.error(f"❌ Fel vid PDF-extraktion från {input_file.name}: {pdf_err}", exc_info=True)
                
                elif file_type in ['.xlsx', '.xls']:
                    try:
                        excel_data = pd.read_excel(input_file, sheet_name=None, header=None, dtype=str)
                        full_text_parts = []
                        for sheet_name, df in excel_data.items():
                            df.dropna(axis=0, how='all', inplace=True)
                            df.dropna(axis=1, how='all', inplace=True)
                            if not df.empty:
                                sheet_text = df.fillna('').to_string(index=False, header=False, na_rep='').strip()
                                if sheet_text:
                                    sheet_text = re.sub(r'[ \t]+', ' ', sheet_text)
                                    sheet_text = re.sub(r'\n\s*\n', '\n', sheet_text)
                                    full_text_parts.append(f"\n\n[SHEET {sheet_name}]\n{sheet_text}")
                        raw_text = '\n'.join(full_text_parts).strip()
                    except Exception as excel_err:
                        self.logger.error(f"❌ Fel vid läsning av Excel-fil {input_file.name}: {excel_err}", exc_info=True)
                
                if raw_text:
                    cleaned_text_for_this_file = self._clean_text_content(raw_text)
                    if not cleaned_text_for_this_file:
                        self.logger.warning(f"⚠️ Texten från {input_file.name} blev tom efter rensning.")
                        cleaned_text_for_this_file = None
                    else:
                        cleaned_txt_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(cleaned_txt_path, "w", encoding="utf-8") as f:
                            f.write(cleaned_text_for_this_file)
                        self.logger.info(f"✅ Text extraherad och rensad från {input_file.name} → {cleaned_txt_path.name}")
                else:
                    self.logger.warning(f"⚠️ Ingen råtext kunde extraheras från {input_file.name}.")
                    cleaned_text_for_this_file = None
            
            except Exception as file_proc_err:
                self.logger.error(f"❌ Oväntat fel vid bearbetning av filen {input_file.name}: {file_proc_err}", exc_info=True)
                cleaned_text_for_this_file = None
            
            results.append((input_file, output_base_name, cleaned_text_for_this_file))
        
        return results

    def _clean_text_content(self, text: str) -> str:
        """Rensar den extraherade texten."""
        cleaned = text
        self.logger.debug("Rensar textinnehåll...")
        
        header_texts_to_remove = [
            # Lång kombinerad sträng
            "Företag X, Adress X, Postnummer X Stad X, telefon Telefon X, fax Fax X,Företag X, Box X, Postnummer X Stad X, telefon Telefon X, fax Fax X, http://www.foretag-x.se",
            # Kortare varianter
            "Företag X, Adress X, Postnummer X Stad X, telefon Telefon X, fax Fax X",
            "Företag X, Box X, Postnummer X Stad X, telefon Telefon X, fax Fax X, http://www.foretag-x.se",
            # Generisk sekretessnotis
            "Confidential"
        ]
        
        self.logger.debug("Applicerar regler för borttagning av header/footer-text...")
        for header_text in header_texts_to_remove:
            if not header_text: continue
            # Skapa ett säkert regex-mönster för den exakta raden att ta bort
            escaped_header = re.escape(header_text.strip())
            pattern = rf'(?im)^\s*{escaped_header}\s*$' # Hela raden, skiftlägesokänsligt
            cleaned = re.sub(pattern, '', cleaned)
        
        # Ta bort BOM (Byte Order Mark) som kan finnas i början av filer
        cleaned = cleaned.lstrip('\ufeff')
        
        # Ersätt form feed-tecken (sidbrytning) med dubbel radbrytning för att skapa ett styckeavbrott
        cleaned = cleaned.replace("\x0c", "\n\n")
        
        # Normalisera alla typer av radbrytningar till en enda standard (\n)
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        
        # Ta bort sidnummer etc. (t.ex. "Sida 5", "Page 3 of 10")
        cleaned = re.sub(r'^\s*(Sida|Page)\s+\d+(\s*(av|of)\s*\d+)?\s*$', '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
        cleaned = re.sub(r'^\s*\d+\s*/\s*\d+\s*$', '', cleaned, flags=re.MULTILINE) # "1 / 10"
        
        # Ta bort rader som bara består av många punkter eller bindestreck (typiska avdelare)
        cleaned = re.sub(r'^\s*([.]{5,}|[-]{5,})\s*$', '', cleaned, flags=re.MULTILINE)

        # Hantera bindestreck vid radslut för att foga samman ord
        cleaned = re.sub(r'(\w)-\n(\w)', r'\1\2', cleaned)
        
        # Normalisera blanksteg och tomma rader för ett renare utseende
        cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned) # Flera mellanslag/tabbar till ett enda mellanslag
        cleaned = re.sub(r'\n\s*\n', '\n\n', cleaned) # Normalisera tomma rader
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned) # Se till att det max är två radbrytningar efter varandra

        # Ta bort rader som bara innehåller "NaN" (kan komma från Pandas/Excel-konvertering)
        cleaned = re.sub(r'(?m)^\s*NaN\s*$', '', cleaned)
        
        return cleaned.strip()

    def split_into_chunks(self, text: str, document_id: str, source_ref: str) -> List[Dict[str, Any]]:
        if not self.tokenizer:
            self.logger.error("❌ Tokenizer ej tillgänglig. Kan inte skapa chunks.")
            return []
        if not text or not text.strip():
            self.logger.warning(f"⚠️ Tom text mottagen för chunking (källa: {source_ref}).")
            return []

        self.logger.info(f"✂️ Delar upp '{source_ref}' i chunks (Max: {self.max_tokens} tokens, Överlapp: {self.token_overlap}).")
        try:
            tokens = self.tokenizer.encode(text)
            total_tokens = len(tokens)
            self.logger.debug(f"📊 Totalt antal tokens i dokumentet '{source_ref}': {total_tokens}")

            chunks_list: List[Dict[str, Any]] = []
            start_idx = 0
            chunk_idx_counter = 0
            step_size = max(1, self.max_tokens - self.token_overlap) 

            while start_idx < total_tokens:
                end_idx = min(start_idx + self.max_tokens, total_tokens)
                
                chunk_tokens = tokens[start_idx : end_idx]
                chunk_text = self.tokenizer.decode(chunk_tokens).strip()

                if not chunk_text:
                    start_idx += step_size
                    continue

                creation_time = datetime.now(timezone.utc).isoformat()
                
                page_match = re.search(r'\[SIDA\s+(\d+)\]', chunk_text, re.IGNORECASE)
                page_hint = int(page_match.group(1)) if page_match else None

                section_title = None
                first_line = chunk_text.split('\n', 1)[0].strip()
                if 3 < len(first_line) < 100 and not first_line.endswith(('.', '?', '!')) and not re.match(r'\[(SIDA|SHEET)\s+.*\]', first_line, re.IGNORECASE):
                    section_title = first_line

                chunk_data = {
                    "id": str(uuid.uuid4()),
                    "text": chunk_text,
                    "metadata": {
                        "document_id": document_id,
                        "source": source_ref,
                        "page_hint": page_hint,
                        "chunk_index": chunk_idx_counter,
                        "section_title": section_title,
                        "doc_type": "manual",
                        "language": "sv",
                        "tags": extract_tags_from_text(chunk_text, self.tag_rules),
                        "token_count": len(chunk_tokens),
                        "creation_timestamp_utc": creation_time
                    }
                }
                chunks_list.append(chunk_data)
                
                chunk_idx_counter += 1
                start_idx += step_size

            total_chunks_for_doc = len(chunks_list)
            for chunk_item in chunks_list:
                chunk_item["metadata"]["total_chunks"] = total_chunks_for_doc

            self.logger.info(f"✅ Skapade {total_chunks_for_doc} chunks från '{source_ref}'.")
            return chunks_list
        except Exception as e:
            self.logger.error(f"❌ Allvarligt fel vid chunking av text från '{source_ref}': {e}", exc_info=True)
            return []

    def save_chunks_to_json(self, chunks_data: List[Dict[str, Any]], output_base_name: str):
        if not chunks_data:
            self.logger.warning(f"⚠️ Inga chunks att spara till JSON för '{output_base_name}'.")
            return
        json_path = self.chunk_folder / f"{output_base_name}_chunks.json"
        try:
            data_to_save = {
                "creation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "source_base_name": output_base_name,
                "chunk_count": len(chunks_data),
                "chunks": chunks_data
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            self.logger.info(f"💾 Chunks för '{output_base_name}' sparade till JSON: {json_path.name}")
        except Exception as e:
            self.logger.error(f"❌ Fel vid sparning av JSON för '{output_base_name}': {e}", exc_info=True)

    def save_to_database(self, document_id: str, original_file_path: Path, chunks: List[Dict[str, Any]]):
        if not self.is_db_configured or not self.db_manager:
            self.logger.debug(f"Databaslagring hoppas över för Doc ID {document_id} (ingen DB-konfiguration).")
            return False
        if not chunks:
            self.logger.warning(f"⚠️ Inga chunks att spara till DB för dokument {document_id}.")
            return False

        self.logger.info(f"📦 Sparar dokument {document_id} och {len(chunks)} chunks till databasen...")
        try:
            file_name = original_file_path.name
            file_path_str = str(original_file_path.absolute())
            try: file_size_bytes = original_file_path.stat().st_size
            except FileNotFoundError: file_size_bytes = None
            
            doc_sql = f'INSERT INTO "{self.doc_table_name}" (DocumentID, FileName, FilePath, ExtractionDate, FileSize, TotalChunks, Language) VALUES (?, ?, ?, ?, ?, ?, ?)'
            doc_language = chunks[0]["metadata"].get("language", "sv")
            self.db_manager.execute_query(doc_sql, (document_id, file_name, file_path_str, datetime.now(timezone.utc), file_size_bytes, len(chunks), doc_language))
            self.logger.debug(f"✍️ Dokumentmetadata sparad för {document_id}.")

            chunk_sql = f'INSERT INTO "{self.chunk_table_name}" (ChunkID, DocumentID, ChunkIndex, ChunkText, Source, PageHint, SectionTitle, TagsJson, TokenCount, CreationTimestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
            chunk_tuples_to_save: List[Tuple[Any, ...]] = []
            
            for chunk_item in chunks:
                meta = chunk_item.get("metadata", {})
                
                source_val = meta.get("source", "")
                if len(source_val) > 255: # Trunkera för att undvika DB-fel
                    self.logger.warning(f"Trunkerar 'Source'-värde för ChunkID {chunk_item.get('id')}: '{source_val[:50]}...' till 255 tecken.")
                    source_val = source_val[:255]
                
                section_title_val = meta.get("section_title")
                if section_title_val and len(section_title_val) > 255: # Trunkera
                    self.logger.warning(f"Trunkerar 'SectionTitle' för ChunkID {chunk_item.get('id')}: '{section_title_val[:50]}...' till 255 tecken.")
                    section_title_val = section_title_val[:255]

                chunk_tuples_to_save.append((
                    chunk_item.get("id"),
                    document_id,
                    meta.get("chunk_index"),
                    chunk_item.get("text"),
                    source_val,
                    meta.get("page_hint"),
                    section_title_val,
                    json.dumps(meta.get("tags", []), ensure_ascii=False),
                    meta.get("token_count"),
                    meta.get("creation_timestamp_utc")
                ))
            
            self.db_manager.execute_many(chunk_sql, chunk_tuples_to_save)
            self.logger.info(f"✅ Dokument {document_id} och {len(chunks)} chunks sparade till databasen.")
            return True
        except Exception as e:
            self.logger.error(f"❌ Fel vid databaslagring för Dokument ID {document_id}: {e}", exc_info=True)
            return False

    def deduplicate_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not chunks: return []
        
        seen_normalized_text = set()
        filtered_chunks: List[Dict[str, Any]] = []
        last_seen_title: Optional[str] = None
        duplicate_text_count = 0
        reset_title_count = 0

        for chunk_item in chunks:
            chunk_text = chunk_item.get("text", "")
            if not isinstance(chunk_text, str):
                 self.logger.warning(f"⚠️ Ogiltig texttyp för chunk ID {chunk_item.get('id', 'N/A')}. Hoppas över.")
                 continue

            normalized_text = re.sub(r'\s+', ' ', chunk_text.strip()).lower()
            if not normalized_text or normalized_text in seen_normalized_text:
                duplicate_text_count += 1
                continue
            
            current_metadata = chunk_item.get("metadata", {})
            current_title = current_metadata.get("section_title")

            if isinstance(current_title, str) and isinstance(last_seen_title, str) and current_title.strip().lower() == last_seen_title.strip().lower():
                current_metadata["section_title"] = None
                reset_title_count += 1
                last_seen_title = None
            elif isinstance(current_title, str):
                last_seen_title = current_title
            else:
                last_seen_title = None

            seen_normalized_text.add(normalized_text)
            filtered_chunks.append(chunk_item)

        if duplicate_text_count > 0: self.logger.info(f"🗑️ Tog bort {duplicate_text_count} duplicerade text-chunks.")
        if reset_title_count > 0: self.logger.info(f"🏷️ Nollställde {reset_title_count} repetitiva sektionstitlar.")
        return filtered_chunks

    def process_all(self):
        """Kör hela bearbetningskedjan för alla filer."""
        self.logger.info("🚀 Startar batchbearbetning av alla dokument...")
        processed_count = 0
        failed_count = 0
        total_chunks_generated = 0
        
        extraction_results = self.extract_and_clean_text_from_files()

        if not extraction_results:
            self.logger.warning("Inga filer bearbetades. Avslutar process_all.")
            return

        for original_file_path, output_base_name, cleaned_text in extraction_results:
            doc_id_for_error = f"fil_{output_base_name}"
            
            if cleaned_text is None:
                self.logger.warning(f"⚠️ Hoppar över {original_file_path.name} (ingen rensad text).")
                failed_count += 1
                continue

            try:
                self.logger.info(f"⚙️ Bearbetar vidare: {output_base_name}.txt")
                document_id = str(uuid.uuid4())
                doc_id_for_error = document_id
                self.logger.debug(f"🆔 Tilldelat Document ID: {document_id} till {original_file_path.name}")

                source_ref = f"{output_base_name}.txt"
                chunks = self.split_into_chunks(cleaned_text, document_id, source_ref)

                if not chunks:
                     self.logger.warning(f"⚠️ Inga chunks skapades för {original_file_path.name}.")
                     processed_count += 1
                     continue
                
                deduplicated_chunks = self.deduplicate_chunks(chunks)
                if not deduplicated_chunks:
                    self.logger.warning(f"⚠️ Alla chunks från {original_file_path.name} filtrerades bort.")
                    processed_count += 1
                    continue
                
                total_chunks_generated += len(deduplicated_chunks)
                self.save_chunks_to_json(deduplicated_chunks, output_base_name)

                if self.is_db_configured:
                    self.save_to_database(document_id, original_file_path, deduplicated_chunks)
                
                processed_count += 1

            except Exception as e:
                self.logger.error(f"❌ Allvarligt fel vid bearbetning av {original_file_path.name} (Doc ID: {doc_id_for_error}): {e}", exc_info=True)
                failed_count += 1
        
        self.logger.info("🏁 Batchbearbetning klar.")
        self.logger.info(f"📊 Sammanfattning: {processed_count} filer framgångsrikt bearbetade.")
        self.logger.info(f"   Totalt {total_chunks_generated} chunks genererade (efter deduplicering).")
        self.logger.info(f"   {failed_count} filer misslyckades eller gav ingen text.")


# === Körning (för testning av denna modul standalone) ===
if __name__ == "__main__":
    if not logging.getLogger().hasHandlers(): 
        LOG_FILE_PDFPROC = "pdf_processor_standalone.log"
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s [%(levelname)-8s] %(name)-30s %(funcName)-25s L:%(lineno)-4d - %(message)s',
            handlers=[
                logging.FileHandler(LOG_FILE_PDFPROC, mode="w", encoding="utf-8"),
                logging.StreamHandler(sys.stdout)
            ]
        )
        logger = logging.getLogger(__name__)
        logger.info(f"Loggning konfigurerad för PDFProcessor standalone. Loggar till konsol och {LOG_FILE_PDFPROC}")

    logger.info("=================================================")
    logger.info("      PDFProcessor Standalone Test Start         ")
    logger.info("=================================================")

    test_root = Path("temp_pdf_processor_test")
    test_input = test_root / "input"
    test_cleaned = test_root / "cleaned"
    test_chunked = test_root / "chunked"
    for p in [test_input, test_cleaned, test_chunked]:
        p.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(fitz.Point(50, 72), "Detta är sida 1.")
        page.insert_text(fitz.Point(50, 144), "[SIDA 2]\nDetta är sida 2.")
        doc.save(test_input / "dummy_manual.pdf")
        doc.close()
        logger.info(f"Skapade dummy PDF: {test_input / 'dummy_manual.pdf'}")
    except Exception as e_dummy:
        logger.error(f"Kunde inte skapa dummy-fil: {e_dummy}.")

    DEMO_DB_CONN_STR: Optional[str] = None
    db_mngr_instance: Optional[DatabaseManager] = None
    if DEMO_DB_CONN_STR:
        try:
            from sql.database_manager import DatabaseManager as RealDBManager
            db_mngr_instance = RealDBManager(DEMO_DB_CONN_STR)
        except ImportError:
            db_mngr_instance = DatabaseManager(DEMO_DB_CONN_STR)
    else:
        db_mngr_instance = DatabaseManager(None)

    processor = PDFProcessor(
        input_folder=str(test_input),
        cleaned_folder=str(test_cleaned),
        chunk_folder=str(test_chunked),
        db_manager=db_mngr_instance
    )

    processor.process_all()

    logger.info(f"Testkörning slutförd. Kontrollera mapparna i: {test_root.resolve()}")
    logger.info("=================================================")
    logger.info("       PDFProcessor Standalone Test End          ")
    logger.info("=================================================")