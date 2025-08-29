-- ===================================================
-- Skapa/Uppdatera Databasschema för RAG Chatbot
-- Version: 2025-06-09
-- ===================================================
-- Ändringslogg:
-- * Konsoliderat och strukturerat all CREATE TABLE och ALTER TABLE logik per tabell.
-- * Säkerställt att alla diskuterade kolumner finns med och hanteras med ALTER TABLE.
-- * Infört korrekt FK-relation mellan UsedChunks och ChatLogs med ON DELETE CASCADE.
-- * Säkerställt korrekta datatyper (t.ex. DATETIME2(3) för tidsstämplar, IDENTITY för PKs).
-- * Förbättrad läsbarhet och kommentarer.
-- ===================================================

PRINT 'Startar databasschema-uppdatering...';
GO

-- 1. Tabell för Dokumentmetadata
IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'Documents')
BEGIN
    CREATE TABLE dbo.Documents (
        DocumentID      NVARCHAR(100) PRIMARY KEY,
        FileName        NVARCHAR(255) NOT NULL,
        FilePath        NVARCHAR(1024) NULL,
        FileSize        BIGINT        NULL,
        ExtractionDate  DATETIME2(3)  NULL,
        TotalChunks     INT           NULL,
        Language        VARCHAR(10)   NULL
    );
    PRINT 'Tabell Documents skapad.';
END
ELSE
BEGIN
    PRINT 'Tabell Documents finns redan. Kontrollerar kolumner...';
    IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'Documents' AND COLUMN_NAME = 'FilePath')
    BEGIN
        ALTER TABLE dbo.Documents ADD FilePath NVARCHAR(1024) NULL;
        PRINT 'Kolumnen FilePath har lagts till i befintlig Documents-tabell.';
    END
    ELSE IF EXISTS (SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'Documents' AND COLUMN_NAME = 'FilePath' AND CHARACTER_MAXIMUM_LENGTH < 1024)
    BEGIN
        ALTER TABLE dbo.Documents ALTER COLUMN FilePath NVARCHAR(1024) NULL;
        PRINT 'Kolumnen FilePath i Documents-tabellen har uppdaterats till NVARCHAR(1024).';
    END
END
GO

-- Index för Documents
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Documents_FileName' AND object_id = OBJECT_ID('dbo.Documents'))
BEGIN
    CREATE INDEX IX_Documents_FileName ON dbo.Documents(FileName);
    PRINT 'Index IX_Documents_FileName skapat på Documents.';
END
GO

-- 2. Tabell för Dokument-Chunks
IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'DocumentChunks')
BEGIN
    CREATE TABLE dbo.DocumentChunks (
        ChunkID           NVARCHAR(100) PRIMARY KEY,
        DocumentID        NVARCHAR(100) NOT NULL,
        ChunkIndex        INT           NOT NULL,
        ChunkText         NVARCHAR(MAX) NULL,
        Source            NVARCHAR(255) NULL,
        PageHint          INT           NULL,
        SectionTitle      NVARCHAR(255) NULL,
        TagsJson          NVARCHAR(MAX) NULL,
        TokenCount        INT           NULL,
        CreationTimestamp DATETIME2(3)  NULL,

        CONSTRAINT FK_DocumentChunks_Document FOREIGN KEY (DocumentID)
            REFERENCES dbo.Documents(DocumentID)
            ON DELETE CASCADE
            ON UPDATE CASCADE
    );
    PRINT 'Tabell DocumentChunks skapad.';

    CREATE INDEX IX_DocumentChunks_DocumentID ON dbo.DocumentChunks(DocumentID);
    PRINT 'Index IX_DocumentChunks_DocumentID skapat på DocumentChunks.';
END
ELSE 
BEGIN
    PRINT 'Tabell DocumentChunks finns redan. Kontrollerar/lägger till kolumner...';
    IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'DocumentChunks' AND COLUMN_NAME = 'CreationTimestamp')
    BEGIN
        ALTER TABLE dbo.DocumentChunks ADD CreationTimestamp DATETIME2(3) NULL;
        PRINT 'Kolumnen CreationTimestamp har lagts till i befintlig DocumentChunks-tabell.';
    END
END
GO

-- 3. Tabell för Chattloggar
IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'ChatLogs')
BEGIN
    CREATE TABLE dbo.ChatLogs (
        ChatLogID             INT IDENTITY(1,1) PRIMARY KEY, 
        UserQuestion          NVARCHAR(MAX) NULL,          
        BotResponse           NVARCHAR(MAX) NULL,          
        Timestamp             DATETIME2(3)  NOT NULL DEFAULT(SYSDATETIME()), 
        UserQuestionEmbedding VARBINARY(MAX) NULL,       
        CachedResponseID      INT           NULL
    );
    PRINT 'Tabell ChatLogs skapad.';
    CREATE INDEX IX_ChatLogs_Timestamp ON dbo.ChatLogs(Timestamp);
    PRINT 'Index IX_ChatLogs_Timestamp skapat på ChatLogs.';
    
    -- Lägg till självrefererande FK efter att tabellen är skapad
    ALTER TABLE dbo.ChatLogs
    ADD CONSTRAINT FK_ChatLogs_CachedResponse
    FOREIGN KEY (CachedResponseID) REFERENCES dbo.ChatLogs(ChatLogID)
    ON DELETE NO ACTION -- Förhindrar att en radering av en 'förälder'-logg raderar 'barn'-loggar
    ON UPDATE NO ACTION; 
    PRINT 'Foreign Key FK_ChatLogs_CachedResponse tillagd till ChatLogs.';
END
ELSE 
BEGIN
    PRINT 'Tabell ChatLogs finns redan. Kontrollerar/lägger till kolumner...';
    IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'ChatLogs' AND COLUMN_NAME = 'UserQuestionEmbedding')
    BEGIN
        ALTER TABLE dbo.ChatLogs ADD UserQuestionEmbedding VARBINARY(MAX) NULL;
        PRINT 'Kolumnen UserQuestionEmbedding har lagts till i befintlig ChatLogs-tabell.';
    END
    IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'ChatLogs' AND COLUMN_NAME = 'CachedResponseID')
    BEGIN
        ALTER TABLE dbo.ChatLogs ADD CachedResponseID INT NULL;
        PRINT 'Kolumnen CachedResponseID har lagts till i befintlig ChatLogs-tabell.';
    END
    IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE name = 'FK_ChatLogs_CachedResponse' AND parent_object_id = OBJECT_ID('dbo.ChatLogs'))
    BEGIN
         ALTER TABLE dbo.ChatLogs
         ADD CONSTRAINT FK_ChatLogs_CachedResponse
         FOREIGN KEY (CachedResponseID) REFERENCES dbo.ChatLogs(ChatLogID)
         ON DELETE NO ACTION
         ON UPDATE NO ACTION;
         PRINT 'Foreign Key FK_ChatLogs_CachedResponse tillagd till ChatLogs.';
    END
END
GO

-- 4. Tabell för Använda Chunks (Kopplingstabell)
IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'UsedChunks')
BEGIN
    CREATE TABLE dbo.UsedChunks (
        UsedChunkLinkID INT IDENTITY(1,1) PRIMARY KEY, -- Bytte namn från UsedChunkID för tydlighet
        ChatLogID       INT NOT NULL,
        ChunkID         NVARCHAR(100) NOT NULL,
        DocumentID      NVARCHAR(100) NOT NULL,
        LogTimestamp    DATETIME2(3) NOT NULL DEFAULT(SYSDATETIME()),

        CONSTRAINT FK_UsedChunks_ChatLog FOREIGN KEY (ChatLogID)
            REFERENCES dbo.ChatLogs(ChatLogID)
            ON DELETE CASCADE -- Viktigt: Om en chattloggrad tas bort, tas även denna kopplingspost bort.
    );
    PRINT 'Tabell UsedChunks skapad med ChatLogID FK.';

    CREATE INDEX IX_UsedChunks_ChatLogID ON dbo.UsedChunks(ChatLogID);
    PRINT 'Index IX_UsedChunks_ChatLogID skapat på UsedChunks.';
    CREATE INDEX IX_UsedChunks_DocumentID_ChunkID ON dbo.UsedChunks(DocumentID, ChunkID);
    PRINT 'Index IX_UsedChunks_DocumentID_ChunkID skapat på UsedChunks.';
END
ELSE 
BEGIN
    PRINT 'Tabell UsedChunks finns redan. Kontrollerar/lägger till/ändrar kolumner...';
    
    -- Om den gamla 'UserQuestion'-kolumnen finns, ta bort den.
    IF EXISTS (SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'UsedChunks' AND COLUMN_NAME = 'UserQuestion')
    BEGIN
        ALTER TABLE dbo.UsedChunks DROP COLUMN UserQuestion;
        PRINT 'Kolumnen UserQuestion har tagits bort från UsedChunks.';
    END

    -- Lägg till ChatLogID om den inte finns
    IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'UsedChunks' AND COLUMN_NAME = 'ChatLogID')
    BEGIN
        -- Lägg till som NULLABLE först, sedan kan man migrera data och ändra till NOT NULL.
        -- För en ny/rensad databas kan man sätta NOT NULL direkt om man är säker.
        ALTER TABLE dbo.UsedChunks ADD ChatLogID INT NULL; 
        PRINT 'Kolumnen ChatLogID (NULLable) har lagts till i UsedChunks. Överväg att göra den NOT NULL och lägga till FK manuellt efter rensning.';
    END

    -- Lägg till FK om den inte finns (kräver att ChatLogID är av rätt typ och att datan är konsistent, vilket den är på en tom tabell)
    IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE name = 'FK_UsedChunks_ChatLog' AND parent_object_id = OBJECT_ID('dbo.UsedChunks'))
    BEGIN
        -- OBS: Detta steg kommer att misslyckas om `UsedChunks` innehåller data där `ChatLogID` är NULL eller inte matchar en post i `ChatLogs`.
        -- Det är bäst att köra detta EFTER att ha rensat tabellerna och gjort ChatLogID NOT NULL.
        PRINT 'Foreign Key FK_UsedChunks_ChatLog behöver läggas till. Försök detta manuellt efter att ha säkerställt datakonsistens.';
        /*
        -- FÖR MANUELL KÖRNING EFTER RENSNING:
        -- 1. ALTER TABLE dbo.UsedChunks ALTER COLUMN ChatLogID INT NOT NULL;
        -- 2. ALTER TABLE dbo.UsedChunks ADD CONSTRAINT FK_UsedChunks_ChatLog FOREIGN KEY (ChatLogID) REFERENCES dbo.ChatLogs(ChatLogID) ON DELETE CASCADE;
        */
    END
    
    -- Säkerställ att andra nödvändiga kolumner finns
    IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'UsedChunks' AND COLUMN_NAME = 'DocumentID')
    BEGIN
        ALTER TABLE dbo.UsedChunks ADD DocumentID NVARCHAR(100) NULL; -- Ändra till NOT NULL efter rensning
        PRINT 'Kolumnen DocumentID har lagts till i UsedChunks.';
    END
END
GO

PRINT 'Databasschema-uppdatering slutförd.';
GO