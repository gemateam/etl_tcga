-- Esquema del pipeline TCGA medallion.
-- Se ejecuta como el rol de escritura (etl_rw) desde el init de PostgreSQL
-- o con `tcga init-db`.

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS ops;

-- ---------------------------------------------------------------- bronze
CREATE TABLE IF NOT EXISTS bronze.cases_raw (
    case_id           TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    submitter_id      TEXT,
    payload           JSONB NOT NULL,
    _ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    _source_file      TEXT,
    _gdc_data_release TEXT
);

-- ----------------------------------------------------------------- silver
CREATE TABLE IF NOT EXISTS silver.cases (
    case_id           TEXT PRIMARY KEY,
    submitter_id      TEXT,
    project_id        TEXT NOT NULL,
    project_name      TEXT,
    primary_site      TEXT,
    disease_type      TEXT,
    consent_type      TEXT,
    _gdc_data_release TEXT,
    _updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_silver_cases_project ON silver.cases (project_id);

CREATE TABLE IF NOT EXISTS silver.cases_demographics (
    case_id                            TEXT PRIMARY KEY
        REFERENCES silver.cases (case_id) ON DELETE CASCADE,
    demographic_id                     TEXT,
    sex_at_birth                       TEXT,
    race                               TEXT,
    ethnicity                          TEXT,
    vital_status                       TEXT,
    days_to_death                      INTEGER,
    days_to_birth                      INTEGER,
    age_at_index                       INTEGER,
    year_of_birth                      INTEGER,
    year_of_death                      INTEGER,
    cause_of_death                     TEXT,
    marital_status                     TEXT,
    education_level                    TEXT,
    country_of_residence_at_enrollment TEXT,
    state                              TEXT,
    demographic_updated_at             TIMESTAMPTZ,
    _gdc_data_release                  TEXT,
    _updated_at                        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_demographics_vital ON silver.cases_demographics (vital_status);
CREATE INDEX IF NOT EXISTS idx_demographics_race ON silver.cases_demographics (race);

CREATE TABLE IF NOT EXISTS silver.cases_diagnoses (
    diagnosis_id                TEXT PRIMARY KEY,
    case_id                     TEXT NOT NULL
        REFERENCES silver.cases (case_id) ON DELETE CASCADE,
    submitter_id                TEXT,
    primary_diagnosis           TEXT,
    tissue_or_organ_of_origin   TEXT,
    classification_of_tumor     TEXT,
    tumor_grade                 TEXT,
    ajcc_pathologic_stage       TEXT,
    ajcc_pathologic_t           TEXT,
    ajcc_pathologic_n           TEXT,
    ajcc_pathologic_m           TEXT,
    ajcc_clinical_stage         TEXT,
    age_at_diagnosis            INTEGER,
    days_to_diagnosis           INTEGER,
    days_to_last_follow_up      INTEGER,
    last_known_disease_status   TEXT,
    morphology                  TEXT,
    laterality                  TEXT,
    site_of_resection_or_biopsy TEXT,
    year_of_diagnosis           INTEGER,
    days_to_recurrence          INTEGER,
    progression_or_recurrence   TEXT,
    diagnosis_is_primary_disease BOOLEAN,
    treatments                  JSONB,
    _gdc_data_release           TEXT,
    _updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_diagnoses_case ON silver.cases_diagnoses (case_id);
CREATE INDEX IF NOT EXISTS idx_diagnoses_primary ON silver.cases_diagnoses (primary_diagnosis);
CREATE INDEX IF NOT EXISTS idx_diagnoses_stage ON silver.cases_diagnoses (ajcc_pathologic_stage);

CREATE TABLE IF NOT EXISTS silver.cases_exposures (
    exposure_id                     TEXT PRIMARY KEY,
    case_id                         TEXT NOT NULL
        REFERENCES silver.cases (case_id) ON DELETE CASCADE,
    submitter_id                    TEXT,
    alcohol_history                 TEXT,
    alcohol_intensity               TEXT,
    alcohol_drinks_per_day          NUMERIC,
    cigarettes_per_day              NUMERIC,
    pack_years_smoked               NUMERIC,
    smoking_frequency               TEXT,
    tobacco_smoking_status          TEXT,
    tobacco_smoking_onset_year      INTEGER,
    tobacco_smoking_quit_year       INTEGER,
    type_of_tobacco_used            TEXT,
    exposure_duration_years         NUMERIC,
    asbestos_exposure_type          TEXT,
    environmental_tobacco_smoke_exposure TEXT,
    secondhand_smoke_as_child       TEXT,
    _gdc_data_release               TEXT,
    _updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_exposures_case ON silver.cases_exposures (case_id);

CREATE TABLE IF NOT EXISTS silver.samples (
    sample_id                   TEXT PRIMARY KEY,
    case_id                     TEXT NOT NULL
        REFERENCES silver.cases (case_id) ON DELETE CASCADE,
    submitter_id                TEXT,
    sample_type                 TEXT,
    tissue_type                 TEXT,
    tumor_descriptor            TEXT,
    specimen_type               TEXT,
    biospecimen_anatomic_site   TEXT,
    preservation_method         TEXT,
    days_to_collection          INTEGER,
    _gdc_data_release           TEXT,
    _updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_samples_case ON silver.samples (case_id);
CREATE INDEX IF NOT EXISTS idx_samples_type ON silver.samples (sample_type);

CREATE TABLE IF NOT EXISTS silver.files (
    file_id              TEXT PRIMARY KEY,
    case_id              TEXT,
    sample_id            TEXT,
    sample_type          TEXT,
    project_id           TEXT NOT NULL,
    file_name            TEXT NOT NULL,
    file_size            BIGINT,
    md5sum               TEXT,
    access               TEXT,
    data_category        TEXT,
    data_type            TEXT,
    data_format          TEXT,
    experimental_strategy TEXT,
    workflow_type        TEXT,
    _gdc_data_release    TEXT,
    _updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_files_case ON silver.files (case_id);
CREATE INDEX IF NOT EXISTS idx_files_project ON silver.files (project_id);

-- ------------------------------------------------------------------- gold
CREATE TABLE IF NOT EXISTS gold.top_20_diagnosis (
    project_id        TEXT NOT NULL,
    diagnosis         TEXT NOT NULL,
    cases             BIGINT NOT NULL,
    pct               NUMERIC(7, 2),
    diagnosis_rank    INTEGER NOT NULL,
    gdc_data_release  TEXT,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, diagnosis)
);

CREATE TABLE IF NOT EXISTS gold.gene_dim (
    project_id  TEXT NOT NULL,
    gene_id     TEXT NOT NULL,
    gene_name   TEXT,
    gene_type   TEXT,
    PRIMARY KEY (project_id, gene_id)
);
CREATE INDEX IF NOT EXISTS idx_gene_dim_name ON gold.gene_dim (gene_name);

-- gold.cx_pivot tiene columnas dinámicas (una por gen) y la crea G02.

CREATE TABLE IF NOT EXISTS gold.sample_summary (
    project_id       TEXT NOT NULL,
    sample_type      TEXT NOT NULL,
    vital_status     TEXT NOT NULL,
    stage            TEXT NOT NULL,
    n_samples        BIGINT NOT NULL,
    n_cases          BIGINT NOT NULL,
    gdc_data_release TEXT,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, sample_type, vital_status, stage)
);

CREATE TABLE IF NOT EXISTS gold.gene_stats (
    project_id    TEXT NOT NULL,
    gene_id       TEXT NOT NULL,
    gene_name     TEXT,
    gene_type     TEXT,
    sample_type   TEXT NOT NULL,
    metric        TEXT NOT NULL DEFAULT 'fpkm_unstranded',
    n_samples     BIGINT NOT NULL,
    n_expressed   BIGINT NOT NULL,
    mean          DOUBLE PRECISION,
    std           DOUBLE PRECISION,
    variance      DOUBLE PRECISION,
    median        DOUBLE PRECISION,
    p25           DOUBLE PRECISION,
    p75           DOUBLE PRECISION,
    p90           DOUBLE PRECISION,
    gdc_data_release TEXT,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, gene_id, sample_type, metric)
);
CREATE INDEX IF NOT EXISTS idx_gene_stats_name ON gold.gene_stats (gene_name);

-- -------------------------------------------------------------------- ops
CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id            UUID PRIMARY KEY,
    step              TEXT NOT NULL,
    project_id        TEXT,
    status            TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    rows_in           BIGINT,
    rows_out          BIGINT,
    params            JSONB NOT NULL DEFAULT '{}'::jsonb,
    gdc_data_release  TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    error             TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_step_started ON ops.pipeline_runs (step, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_project ON ops.pipeline_runs (project_id);

CREATE TABLE IF NOT EXISTS ops.download_control (
    file_id          TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    file_name        TEXT NOT NULL,
    file_size        BIGINT,
    expected_md5     TEXT,
    status           TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'complete', 'failed')),
    md5_ok           BOOLEAN,
    bytes            BIGINT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    downloaded_at    TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_download_status ON ops.download_control (status);

CREATE TABLE IF NOT EXISTS ops.dq_results (
    id           BIGSERIAL PRIMARY KEY,
    run_id       UUID NOT NULL REFERENCES ops.pipeline_runs (run_id) ON DELETE CASCADE,
    step         TEXT NOT NULL,
    project_id   TEXT,
    check_name   TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('critical', 'warning')),
    passed       BOOLEAN NOT NULL,
    details      TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dq_run ON ops.dq_results (run_id);

CREATE TABLE IF NOT EXISTS ops.gdc_status (
    id               INTEGER PRIMARY KEY DEFAULT 1,
    data_release     TEXT NOT NULL,
    tag              TEXT,
    commit           TEXT,
    checked_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------ gold.marker_genes
CREATE TABLE IF NOT EXISTS gold.marker_genes (
    gene_name   TEXT PRIMARY KEY,
    gene_id     TEXT,
    description TEXT NOT NULL,
    note        TEXT
);

-- ------------------------------------------------ gold.sample_marker_values
CREATE TABLE IF NOT EXISTS gold.sample_marker_values (
    project_id   TEXT NOT NULL,
    sample_id    TEXT NOT NULL,
    case_id      TEXT,
    sample_type  TEXT,
    gene_id      TEXT NOT NULL,
    gene_name    TEXT,
    tpm          DOUBLE PRECISION,
    pct_rank     DOUBLE PRECISION,
    category     TEXT,
    gdc_data_release TEXT,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, sample_id, gene_id)
);
CREATE INDEX IF NOT EXISTS idx_smv_sample ON gold.sample_marker_values (sample_id);
CREATE INDEX IF NOT EXISTS idx_smv_gene ON gold.sample_marker_values (gene_id);
CREATE INDEX IF NOT EXISTS idx_smv_case ON gold.sample_marker_values (case_id);

-- --------------------------------------------------------------- permisos
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO web_ro', current_database());
EXCEPTION WHEN undefined_object THEN
    RAISE NOTICE 'El rol web_ro no existe; se omite GRANT CONNECT';
END $$;

GRANT USAGE ON SCHEMA bronze, silver, gold, ops TO web_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA bronze, silver, gold, ops TO web_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA bronze, silver, gold, ops
    GRANT SELECT ON TABLES TO web_ro;
