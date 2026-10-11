/* Run in SQL Server Management Studio. Creates the case-study DWH objects. */
IF DB_ID(N'TelecomDwh') IS NULL CREATE DATABASE TelecomDwh;
GO
USE TelecomDwh;
GO

IF OBJECT_ID(N'dbo.dim_imsi_reference', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_imsi_reference (
        imsi varchar(9) NOT NULL CONSTRAINT PK_dim_imsi_reference PRIMARY KEY,
        subscriber_id int NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.fact_transaction', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.fact_transaction (
        fact_transaction_id bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_fact_transaction PRIMARY KEY,
        transaction_id bigint NOT NULL,
        imsi varchar(15) NOT NULL,
        subscriber_id int NOT NULL,
        tac varchar(8) NOT NULL,
        snr varchar(6) NOT NULL,
        imei varchar(20) NOT NULL,
        cell int NOT NULL,
        lac int NOT NULL,
        event_type varchar(1) NULL,
        event_ts datetime2(0) NOT NULL,
        source_file nvarchar(260) NOT NULL,
        source_row int NOT NULL,
        loaded_at datetime2(0) NOT NULL CONSTRAINT DF_fact_transaction_loaded_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_fact_transaction_source UNIQUE (source_file, source_row)
    );
    CREATE INDEX IX_fact_transaction_event_ts ON dbo.fact_transaction(event_ts);
END;
GO

IF OBJECT_ID(N'dbo.rejected_record', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.rejected_record (
        rejected_record_id bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_rejected_record PRIMARY KEY,
        source_file nvarchar(260) NOT NULL,
        source_row int NOT NULL,
        raw_record nvarchar(max) NOT NULL,
        rejection_reason nvarchar(1000) NOT NULL,
        rejected_at datetime2(0) NOT NULL CONSTRAINT DF_rejected_record_at DEFAULT SYSUTCDATETIME()
    );
    CREATE INDEX IX_rejected_record_source ON dbo.rejected_record(source_file);
END;
GO

IF OBJECT_ID(N'dbo.etl_batch', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.etl_batch (
        source_file nvarchar(260) NOT NULL CONSTRAINT PK_etl_batch PRIMARY KEY,
        valid_count int NOT NULL,
        rejected_count int NOT NULL,
        completed_at datetime2(0) NOT NULL CONSTRAINT DF_etl_batch_completed DEFAULT SYSUTCDATETIME()
    );
END;
GO
