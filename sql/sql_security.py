# sql/sql_security.py

"""
Säkerhetsmodul för SQL-operationer.
Förhindrar SQL injection genom validering av identifierare.
"""

import re
from typing import Set

# Whitelists för tillåtna tabell- och kolumnnamn
ALLOWED_TABLES: Set[str] = {
    'Documents',
    'DocumentChunks',
    'ChatLogs',
    'UsedChunks'
}

ALLOWED_COLUMNS: Set[str] = {
    # Documents table
    'DocumentID',
    'FileName',
    'FilePath',
    'FileSize',
    'ExtractionDate',
    'TotalChunks',
    'Language',

    # DocumentChunks table
    'ChunkID',
    'ChunkIndex',
    'ChunkText',
    'Source',
    'PageHint',
    'SectionTitle',
    'TagsJson',
    'TokenCount',
    'CreationTimestamp',

    # ChatLogs table
    'ChatLogID',
    'UserQuestion',
    'BotResponse',
    'Timestamp',
    'UserQuestionEmbedding',
    'CachedResponseID',

    # UsedChunks table
    'UsedChunkLinkID',
    'LogTimestamp'
}


class SQLSecurityError(Exception):
    """Exception som kastas vid säkerhetsproblem med SQL-identifierare."""
    pass


def validate_sql_identifier(identifier: str, identifier_type: str = "identifier") -> str:
    """
    Validerar att en SQL-identifierare (tabell- eller kolumnnamn) är säker.

    Args:
        identifier: Namnet att validera
        identifier_type: Typ av identifierare för felmeddelanden ('table' eller 'column')

    Returns:
        Den validerade identifieraren

    Raises:
        SQLSecurityError: Om identifieraren innehåller ogiltiga tecken
    """
    if not identifier:
        raise SQLSecurityError(f"SQL {identifier_type} name cannot be empty")

    # Tillåt endast alfanumeriska tecken och underscore
    # SQL identifierare får inte börja med siffra i de flesta DBMS
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', identifier):
        raise SQLSecurityError(
            f"Invalid SQL {identifier_type} name: '{identifier}'. "
            f"Only alphanumeric characters and underscores are allowed."
        )

    # Extra kontroll: För lång identifierare (SQL Server max 128, vi sätter 100)
    if len(identifier) > 100:
        raise SQLSecurityError(
            f"SQL {identifier_type} name too long: '{identifier[:50]}...' "
            f"(max 100 characters)"
        )

    return identifier


def validate_table_name(table_name: str, strict: bool = True) -> str:
    """
    Validerar ett tabellnamn mot whitelist.

    Args:
        table_name: Tabellnamnet att validera
        strict: Om True, kontrollera mot whitelist. Om False, bara syntaxvalidering.

    Returns:
        Det validerade tabellnamnet

    Raises:
        SQLSecurityError: Om tabellnamnet är ogiltigt eller inte i whitelist
    """
    validated = validate_sql_identifier(table_name, "table")

    if strict and validated not in ALLOWED_TABLES:
        raise SQLSecurityError(
            f"Table name '{validated}' is not in the allowed list. "
            f"Allowed tables: {', '.join(sorted(ALLOWED_TABLES))}"
        )

    return validated


def validate_column_name(column_name: str, strict: bool = True) -> str:
    """
    Validerar ett kolumnnamn mot whitelist.

    Args:
        column_name: Kolumnnamnet att validera
        strict: Om True, kontrollera mot whitelist. Om False, bara syntaxvalidering.

    Returns:
        Det validerade kolumnnamnet

    Raises:
        SQLSecurityError: Om kolumnnamnet är ogiltigt eller inte i whitelist
    """
    validated = validate_sql_identifier(column_name, "column")

    if strict and validated not in ALLOWED_COLUMNS:
        raise SQLSecurityError(
            f"Column name '{validated}' is not in the allowed list. "
            f"Allowed columns: {', '.join(sorted(ALLOWED_COLUMNS)[:10])}..."  # Visa bara första 10
        )

    return validated


def build_safe_query(
    query_template: str,
    table_name: str = None,
    column_names: list = None,
    strict: bool = True
) -> str:
    """
    Bygger en säker SQL-query genom att validera och escapa identifierare.

    Args:
        query_template: SQL-mall med {table} och {column} placeholders
        table_name: Tabellnamn att validera och infoga
        column_names: Lista av kolumnnamn att validera och infoga
        strict: Om whitelist-validering ska användas

    Returns:
        Den kompletta, validerade SQL-queryn

    Raises:
        SQLSecurityError: Om någon identifierare är ogiltig

    Example:
        query = build_safe_query(
            'SELECT {columns} FROM {table} WHERE {id_col} = ?',
            table_name='ChatLogs',
            column_names=['UserQuestion', 'BotResponse', 'ChatLogID']
        )
    """
    replacements = {}

    if table_name:
        validated_table = validate_table_name(table_name, strict)
        replacements['table'] = f'"{validated_table}"'

    if column_names:
        validated_columns = [validate_column_name(col, strict) for col in column_names]
        # Bygg kolumnlista med citattecken
        replacements['columns'] = ', '.join([f'"{col}"' for col in validated_columns])

        # För enskilda kolumner (om mall använder {column1}, {column2} etc)
        for i, col in enumerate(validated_columns):
            replacements[f'column{i+1}'] = f'"{col}"'
            if i == 0:
                replacements['column'] = f'"{col}"'  # För första kolumnen

    # Ersätt alla placeholders i mallen
    try:
        safe_query = query_template.format(**replacements)
    except KeyError as e:
        raise SQLSecurityError(f"Query template missing placeholder: {e}")

    return safe_query


def escape_like_pattern(pattern: str) -> str:
    """
    Escapar specialtecken i LIKE-mönster för att förhindra injection.

    Args:
        pattern: Söksträngen att escapea

    Returns:
        Escapad sträng säker för LIKE-operationer
    """
    # Escapea SQL LIKE-specialtecken: %, _, [, ]
    escaped = pattern.replace('[', '[[]')  # Måste vara först
    escaped = escaped.replace('%', '[%]')
    escaped = escaped.replace('_', '[_]')

    return escaped


# Hjälpfunktion för att lägga till nya tillåtna identifierare (för framtida utbyggnad)
def add_allowed_table(table_name: str) -> None:
    """Lägger till ett tabellnamn i whitelist (användbart för tester)."""
    ALLOWED_TABLES.add(table_name)


def add_allowed_column(column_name: str) -> None:
    """Lägger till ett kolumnnamn i whitelist (användbart för tester)."""
    ALLOWED_COLUMNS.add(column_name)
