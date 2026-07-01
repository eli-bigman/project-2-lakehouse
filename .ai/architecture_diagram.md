# E-Commerce Lakehouse: Architecture Diagram & Orchestration Flow

This document defines the new architecture diagram and explains how all components are grouped, focusing on what is encapsulated within the **AWS Step Functions** orchestration boundary.

---

## 1. Architecture Flow Diagram (Mermaid)

The diagram below represents the end-to-end data pipeline. The **AWS Step Functions Orchestration** boundary (represented by the large green container) encapsulates the active execution logic of the pipeline.

```mermaid
flowchart TB
    %% Styling and Theme
    classDef s3 fill:#f9f,stroke:#333,stroke-width:2px;
    classDef lambda fill:#ffb366,stroke:#333,stroke-width:2px;
    classDef spark fill:#99ccff,stroke:#333,stroke-width:2px;
    classDef sf fill:#ccffcc,stroke:#333,stroke-width:3px;
    classDef control fill:#ffd9b3,stroke:#333,stroke-width:2px;
    classDef consumer fill:#e6ccff,stroke:#333,stroke-width:2px;
    classDef ops fill:#ff9999,stroke:#333,stroke-width:2px;

    %% Data Lake (S3 Zones)
    subgraph S3_Data_Lake ["S3 Data Lake Buckets"]
        RawS3["Raw Bucket<br>(xlsx / csv)"]:::s3
        StagingS3["Staging Bucket<br>(Parquet - 7d expiry)"]:::s3
        DwhS3["DWH Bucket<br>(Delta Lake Tables)"]:::s3
        QuarantineS3["Quarantine Bucket<br>(Corrupt Parquet + Reason)"]:::s3
        ArchiveS3["Archive Bucket<br>(Original Raw - Immutable)"]:::s3
    end

    %% S3 Event Trigger
    SourceEvent["File Lands in Raw S3"] -.->|EventBridge Rule| SF_Orchestrator

    %% AWS Step Functions Orchestration Boundary
    subgraph SF_Orchestrator ["AWS Step Functions Orchestration Boundary (State Machine)"]
        SF_Start(["Pipeline Triggered"])
        
        %% Step 1: Claim & MD5
        Lambda_Claim["Lambda: claim_file<br>(Computes MD5 Checksum)"]:::lambda
        
        %% Step 2: Idempotency Choice
        Choice_Processed{"Already<br>Processed?"}
        End_Skip(["Skip Run (No-Op)"])
        
        %% Step 3: Normalization
        Lambda_Normalize["Lambda: normalize_to_parquet<br>(CSV/Excel -> Staging Parquet)"]:::lambda
        
        %% Step 4: Schema Validation
        Lambda_ValidateSchema["Lambda: validate_schema<br>(Dynamic Schema Check)"]:::lambda
        Choice_SchemaValid{"Schema<br>Valid?"}
        
        %% Step 5: Glue Spark Job
        Glue_ETL["Glue PySpark Job<br>(ETL, Dedup, Delta MERGE)"]:::spark
        
        %% Step 6: Archiving
        Lambda_Archive["Lambda: archive_file<br>(Move Raw -> Archive S3)"]:::lambda
        
        %% Failure States
        State_Fail["Mark Ledger FAILED"]:::ops
        SF_End(["Pipeline Finished"])

        %% Internal Step Functions Connections
        SF_Start --> Lambda_Claim
        Lambda_Claim --> Choice_Processed
        Choice_Processed -->|Yes| End_Skip
        Choice_Processed -->|No| Lambda_Normalize
        Lambda_Normalize --> Lambda_ValidateSchema
        Lambda_ValidateSchema --> Choice_SchemaValid
        
        Choice_SchemaValid -->|No| State_Fail
        Choice_SchemaValid -->|Yes| Glue_ETL
        
        Glue_ETL --> Lambda_Archive
        Lambda_Archive --> SF_End
        
        %% Glue Job Catch Blocks
        Glue_ETL -.->|On Failure| State_Fail
        Lambda_Normalize -.->|On Failure| State_Fail
        Lambda_ValidateSchema -.->|On Failure| State_Fail
        Lambda_Archive -.->|On Failure| State_Fail
    end

    %% Control Plane and Metadata
    subgraph Control_Plane ["Metadata & Control Plane"]
        DB_Ledger[("DynamoDB Ledger<br>(Idempotency & Status)")]:::control
        DB_Watermarks[("DynamoDB Watermarks<br>(High Watermark Timestamps)")]:::control
        Glue_Catalog[("Glue Data Catalog<br>(Table Metadata)")]:::control
    end

    %% Downstream Serving
    subgraph Analytics_Serving ["Downstream Serving Layer"]
        Athena["Amazon Athena<br>(Serverless SQL)"]:::consumer
        Streamlit["Streamlit Dashboard UI<br>(localhost / Docker)"]:::consumer
    end

    %% Alerting
    subgraph Alerting ["Ops & Observability"]
        CW_Logs["CloudWatch Logs"]:::ops
        SNS_Alerts["SNS Email Notifications"]:::ops
    end

    %% Inter-group Connections (Data & Metadata Flow)
    Lambda_Claim <==>|Read Checksum & Write status=CLAIMED| DB_Ledger
    Lambda_Claim -.->|Get Size/Bytes| RawS3
    
    Lambda_Normalize ===>|Read Raw| RawS3
    Lambda_Normalize ===>|Write Staging Parquet| StagingS3
    
    Lambda_ValidateSchema ===>|Verify Schema| StagingS3
    
    Glue_ETL ===>|Read Staging Parquet| StagingS3
    Glue_ETL ===>|Merge Clean Rows| DwhS3
    Glue_ETL ===>|Write Corrupt Rows| QuarantineS3
    Glue_ETL <==>|Read/Write Watermarks| DB_Watermarks
    Glue_ETL -.->|Update Table Schema| Glue_Catalog
    Glue_ETL -->|Update status=LOADED| DB_Ledger
    
    Lambda_Archive ===>|Move Raw -> Archive S3| ArchiveS3
    Lambda_Archive ===>|Delete Original Raw| RawS3
    Lambda_Archive -->|Update status=ARCHIVED| DB_Ledger
    
    State_Fail -->|Update status=FAILED| DB_Ledger
    State_Fail -.->|Trigger Alert| SNS_Alerts

    %% Analytics & Dashboards Connections
    Glue_Catalog -.->|Provide Schema| Athena
    DwhS3 -.->|Physical Source Files| Athena
    Athena ===>|SQL Queries| Streamlit
    DB_Ledger ===>|In-flight Status| Streamlit
    
    %% Logs Connections
    SF_Orchestrator -.->|Execution Metrics| CW_Logs
    Glue_ETL -.->|Spark Logs| CW_Logs
    Lambda_Normalize -.->|Lambda Logs| CW_Logs
```

---

## 2. Explanation of Encapsulated Steps inside the Step Functions Boundary

The State Machine acts as the orchestrator (the "brain") that sequentially invokes functions, handles branching, manages states, and catches exceptions. Here is what happens inside:

1. **`claim_file` Lambda:** Reads the file key and raw S3 metadata to compute the file's MD5 checksum. It queries **DynamoDB Ledger** to check if the file is new or failed. If it is already processed or currently processing, it flags the run as a duplicate (`already_processed: True`).
2. **Choice State (`Already Processed?`):** Evaluates the boolean output. If `True`, the pipeline terminates immediately (`Skip Run`) with a success state, preventing redundant resource consumption.
3. **`normalize_to_parquet` Lambda:** Converts CSV/Excel files into standardized Parquet. It reads from the **Raw S3 bucket** and writes to the **Staging S3 bucket** with custom folder partition keys (`batch_id`).
4. **`validate_schema` Lambda:** Validates the schema of the Parquet file.
5. **Choice State (`Schema Valid?`):** If the schema is invalid, the pipeline branches to the failure state. If valid, it triggers the AWS Glue Job. This "fail fast" mechanism saves PySpark compute start-up cost.
6. **Glue PySpark Job:** Performs ETL. It reads from **Staging**, validates rows (e.g. primary keys), writes invalid rows to **Quarantine**, merges valid rows into the target **Delta Table** in **DWH S3**, updates the **Glue Data Catalog**, and writes the loaded metrics into the **DynamoDB Ledger**.
7. **`archive_file` Lambda:** Moves the raw file from the **Raw bucket** to the **Archive bucket** and deletes the raw file from Raw. It then marks the ledger status to `ARCHIVED`.
8. **Catch Block (`Mark Ledger FAILED`):** If any of the above Lambdas or the Glue job fails, the Step Functions state machine catches the exception, updates the **DynamoDB Ledger** table to `FAILED` (with the error message), and triggers an **SNS Email Alert** to notify operators.

---

## 3. Improvements Over the Previous Diagram

1. **Clear Boundaries:** The Step Functions state machine is enclosed in a single container box. You can clearly see how the EventBridge trigger enters the box, and how the internal states (`claim_file` -> `Choice` -> `normalize` -> `validate` -> `Glue` -> `archive`) proceed sequentially.
2. **Detailed Lambdas:** Rather than a single vague "Lambda" block, the diagram explicitly breaks down the **four Lambdas** (`claim_file`, `normalize_to_parquet`, `validate_schema`, and `archive_file`) representing their actual jobs and interactions.
3. **Control Plane Connections:** It clearly illustrates that DynamoDB Ledger is accessed at the beginning (`claim_file`), during the run (`Glue_ETL`), at the end (`archive_file`), and on failure (`State_Fail`).
4. **Ops & Observability Integration:** It maps exactly how exceptions are caught, logging into CloudWatch, and propagating failures to SNS Email Alerts.
