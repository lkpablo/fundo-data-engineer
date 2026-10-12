USE [FundoSource];
GO

SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

IF OBJECT_ID(N'[dbo].[Customers]') IS NULL
BEGIN
	CREATE TABLE [dbo].[Customers](
		[CustomerId] [int] IDENTITY(1,1) NOT NULL,
		[CustomerRef] [nvarchar](max) NOT NULL,
		[FirstName] [varchar](100) NOT NULL,
		[LastName] [varchar](100) NOT NULL,
		[Email] [varchar](255) NULL,
		[Phone] [varchar](50) NULL,
		[NationalId] [varchar](20) NULL,
		[DateOfBirth] [date] NULL,
		[CreatedAt] [datetime] NOT NULL,
		[UpdatedAt] [datetime] NOT NULL,

		CONSTRAINT [PK_Customers] PRIMARY KEY CLUSTERED ([CustomerId] ASC)
	) ON [PRIMARY];
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Customers_CreatedAt')
	ALTER TABLE [dbo].[Customers] ADD CONSTRAINT [DF_Customers_CreatedAt] DEFAULT (GETUTCDATE()) FOR [CreatedAt];
GO

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Customers_UpdatedAt')
	ALTER TABLE [dbo].[Customers] ADD CONSTRAINT [DF_Customers_UpdatedAt] DEFAULT (GETUTCDATE()) FOR [UpdatedAt];
GO

IF NOT EXISTS (SELECT 1 FROM sys.change_tracking_tables WHERE object_id = OBJECT_ID(N'[dbo].[Customers]'))
	ALTER TABLE [dbo].[Customers] ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = OFF);
GO


IF OBJECT_ID(N'[dbo].[Advances]') IS NULL
BEGIN
	CREATE TABLE [dbo].[Advances](
		[AdvanceId] [int] IDENTITY(1,1) NOT NULL,
		[CustomerId] [int] NOT NULL,
		[Amount] [decimal](12, 2) NOT NULL,
		[Status] [varchar](20) NOT NULL,
		[RequestedAt] [datetime] NOT NULL,
		[FundedAt] [datetime] NULL,
		[PaidOffAt] [datetime] NULL,
		[CreatedAt] [datetime] NOT NULL,
		[UpdatedAt] [datetime] NOT NULL,

		CONSTRAINT [PK_Advances] PRIMARY KEY CLUSTERED ([AdvanceId] ASC),
		CONSTRAINT [FK_Advances_Customers] FOREIGN KEY ([CustomerId]) REFERENCES [dbo].[Customers] ([CustomerId])
	) ON [PRIMARY];
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Advances_CreatedAt')
	ALTER TABLE [dbo].[Advances] ADD CONSTRAINT [DF_Advances_CreatedAt] DEFAULT (GETUTCDATE()) FOR [CreatedAt];
GO

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Advances_UpdatedAt')
	ALTER TABLE [dbo].[Advances] ADD CONSTRAINT [DF_Advances_UpdatedAt] DEFAULT (GETUTCDATE()) FOR [UpdatedAt];
GO

IF NOT EXISTS (SELECT 1 FROM sys.change_tracking_tables WHERE object_id = OBJECT_ID(N'[dbo].[Advances]'))
	ALTER TABLE [dbo].[Advances] ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = OFF);
GO


IF OBJECT_ID(N'[dbo].[Cards]') IS NULL
BEGIN
	CREATE TABLE [dbo].[Cards](
		[CardId] [int] IDENTITY(1,1) NOT NULL,
		[CustomerId] [int] NOT NULL,
		[Brand] [varchar](20) NOT NULL,
		[Last4] [char](4) NOT NULL,
		[ExpMonth] [tinyint] NOT NULL,
		[ExpYear] [smallint] NOT NULL,
		[Status] [varchar](20) NOT NULL,
		[CreatedAt] [datetime] NOT NULL,
		[UpdatedAt] [datetime] NOT NULL,

		CONSTRAINT [PK_Cards] PRIMARY KEY CLUSTERED ([CardId] ASC),
		CONSTRAINT [FK_Cards_Customers] FOREIGN KEY ([CustomerId]) REFERENCES [dbo].[Customers] ([CustomerId])
	) ON [PRIMARY];
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Cards_CreatedAt')
	ALTER TABLE [dbo].[Cards] ADD CONSTRAINT [DF_Cards_CreatedAt] DEFAULT (GETUTCDATE()) FOR [CreatedAt];
GO

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Cards_UpdatedAt')
	ALTER TABLE [dbo].[Cards] ADD CONSTRAINT [DF_Cards_UpdatedAt] DEFAULT (GETUTCDATE()) FOR [UpdatedAt];
GO

IF NOT EXISTS (SELECT 1 FROM sys.change_tracking_tables WHERE object_id = OBJECT_ID(N'[dbo].[Cards]'))
	ALTER TABLE [dbo].[Cards] ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = OFF);
GO


IF OBJECT_ID(N'[dbo].[Transactions]') IS NULL
BEGIN
	CREATE TABLE [dbo].[Transactions](
		[TransactionId] [bigint] IDENTITY(1,1) NOT NULL,
		[CustomerId] [int] NOT NULL,
		[CardId] [int] NULL,
		[Amount] [decimal](12, 2) NOT NULL,
		[TxnType] [varchar](20) NOT NULL,
		[TxnDate] [datetime] NOT NULL,
		[RowVer] [rowversion] NOT NULL,

		CONSTRAINT [PK_Transactions] PRIMARY KEY CLUSTERED ([TransactionId] ASC),
		CONSTRAINT [FK_Transactions_Customers] FOREIGN KEY ([CustomerId]) REFERENCES [dbo].[Customers] ([CustomerId])
	) ON [PRIMARY];
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'NCI_Transactions_RowVer' AND object_id = OBJECT_ID(N'[dbo].[Transactions]'))
	CREATE NONCLUSTERED INDEX [NCI_Transactions_RowVer] ON [dbo].[Transactions]
	(
		[RowVer] ASC
	) ON [PRIMARY];
GO


IF OBJECT_ID(N'[dbo].[CustomerHistory]') IS NULL
BEGIN
	CREATE TABLE [dbo].[CustomerHistory](
		[HistoryId] [bigint] IDENTITY(1,1) NOT NULL,
		[CustomerId] [int] NOT NULL,
		[FieldName] [varchar](50) NOT NULL,
		[OldValue] [nvarchar](400) NULL,
		[NewValue] [nvarchar](400) NULL,
		[ChangedAt] [datetime] NOT NULL,
		[ChangedBy] [varchar](100) NULL,

		CONSTRAINT [PK_CustomerHistory] PRIMARY KEY CLUSTERED ([HistoryId] ASC)
	) ON [PRIMARY];
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_CustomerHistory_ChangedAt')
	ALTER TABLE [dbo].[CustomerHistory] ADD CONSTRAINT [DF_CustomerHistory_ChangedAt] DEFAULT (GETUTCDATE()) FOR [ChangedAt];
GO

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_CustomerHistory_ChangedBy')
	ALTER TABLE [dbo].[CustomerHistory] ADD CONSTRAINT [DF_CustomerHistory_ChangedBy] DEFAULT (SUSER_SNAME()) FOR [ChangedBy];
GO


IF OBJECT_ID(N'[dbo].[tmp_export_2021]') IS NULL
BEGIN
	CREATE TABLE [dbo].[tmp_export_2021](
		[ExportId] [int] NULL,
		[Payload] [nvarchar](max) NULL,
		[ExportedAt] [datetime] NULL
	) ON [PRIMARY];
END
GO
