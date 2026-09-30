-- AgentSphere — MySQL schema (Phase 1)
--
-- Group 1 (Figure 3.6): app_user, class, student, fee, result, attendance, homework, schedule
-- Group 2 (platform):   user_session, agent, agent_knowledge_source, agent_tool,
--                       agent_guardrail, chat_session, chat_message
--
-- This file is the canonical DDL. backend/app/models.py mirrors it; change both together.
--
--   mysql -u root -p < backend/sql/schema.sql

CREATE DATABASE IF NOT EXISTS agentsphere
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE agentsphere;

-- ---------------------------------------------------------------- school records (3.6)

CREATE TABLE IF NOT EXISTS app_user (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  email          VARCHAR(255) NOT NULL UNIQUE,
  password_hash  VARCHAR(255) NOT NULL,
  full_name      VARCHAR(150) NOT NULL,
  role           ENUM('parent','teacher','administrator') NOT NULL,
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_app_user_role (role)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `class` (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  name           VARCHAR(100) NOT NULL,
  grade_level    VARCHAR(50),
  academic_year  VARCHAR(20),
  room           VARCHAR(50),
  teacher_id     INT,
  CONSTRAINT fk_class_teacher FOREIGN KEY (teacher_id) REFERENCES app_user(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS student (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  student_no     VARCHAR(50) NOT NULL UNIQUE,
  full_name      VARCHAR(150) NOT NULL,
  date_of_birth  DATE,
  class_id       INT,
  guardian_id    INT,
  enrolled_on    DATE,
  CONSTRAINT fk_student_class    FOREIGN KEY (class_id)    REFERENCES `class`(id)  ON DELETE SET NULL,
  CONSTRAINT fk_student_guardian FOREIGN KEY (guardian_id) REFERENCES app_user(id) ON DELETE SET NULL,
  INDEX idx_student_class (class_id),
  INDEX idx_student_guardian (guardian_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS fee (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  student_id   INT NOT NULL,
  description  VARCHAR(255) NOT NULL,
  amount       DECIMAL(10,2) NOT NULL,
  amount_paid  DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  due_date     DATE,
  paid_date    DATE,
  status       ENUM('unpaid','partial','paid','waived') NOT NULL DEFAULT 'unpaid',
  CONSTRAINT fk_fee_student FOREIGN KEY (student_id) REFERENCES student(id) ON DELETE CASCADE,
  INDEX idx_fee_student (student_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS result (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  student_id   INT NOT NULL,
  subject      VARCHAR(100) NOT NULL,
  term         VARCHAR(50),
  assessment   VARCHAR(100),
  score        DECIMAL(5,2),
  max_score    DECIMAL(5,2),
  grade        VARCHAR(5),
  recorded_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_result_student FOREIGN KEY (student_id) REFERENCES student(id) ON DELETE CASCADE,
  INDEX idx_result_student (student_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS attendance (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  student_id       INT NOT NULL,
  attendance_date  DATE NOT NULL,
  status           ENUM('present','absent','late','excused') NOT NULL,
  remarks          VARCHAR(255),
  CONSTRAINT fk_attendance_student FOREIGN KEY (student_id) REFERENCES student(id) ON DELETE CASCADE,
  CONSTRAINT uq_attendance_day UNIQUE (student_id, attendance_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS homework (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  class_id       INT NOT NULL,
  teacher_id     INT,
  subject        VARCHAR(100) NOT NULL,
  title          VARCHAR(255) NOT NULL,
  description    TEXT,
  assigned_date  DATE,
  due_date       DATE,
  CONSTRAINT fk_homework_class   FOREIGN KEY (class_id)   REFERENCES `class`(id)  ON DELETE CASCADE,
  CONSTRAINT fk_homework_teacher FOREIGN KEY (teacher_id) REFERENCES app_user(id) ON DELETE SET NULL,
  INDEX idx_homework_class (class_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS schedule (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  class_id     INT NOT NULL,
  teacher_id   INT,
  subject      VARCHAR(100) NOT NULL,
  day_of_week  INT NOT NULL COMMENT '1=Monday .. 7=Sunday (ISO-8601)',
  start_time   TIME NOT NULL,
  end_time     TIME NOT NULL,
  room         VARCHAR(50),
  CONSTRAINT fk_schedule_class   FOREIGN KEY (class_id)   REFERENCES `class`(id)  ON DELETE CASCADE,
  CONSTRAINT fk_schedule_teacher FOREIGN KEY (teacher_id) REFERENCES app_user(id) ON DELETE SET NULL,
  INDEX idx_schedule_class (class_id)
) ENGINE=InnoDB;

-- ------------------------------------------------------------------- platform tables

CREATE TABLE IF NOT EXISTS user_session (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  user_id     INT NOT NULL,
  jti         VARCHAR(64) NOT NULL UNIQUE,
  role        ENUM('parent','teacher','administrator') NOT NULL,
  issued_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at  DATETIME NOT NULL,
  revoked_at  DATETIME,
  CONSTRAINT fk_session_user FOREIGN KEY (user_id) REFERENCES app_user(id) ON DELETE CASCADE,
  INDEX idx_session_user (user_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS agent (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  name               VARCHAR(150) NOT NULL,
  description        TEXT,
  role_instructions  TEXT NOT NULL,
  audience_roles     JSON NOT NULL,
  status             ENUM('draft','published','archived') NOT NULL DEFAULT 'draft',
  model_settings     JSON NOT NULL,
  created_by         INT,
  created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_agent_creator FOREIGN KEY (created_by) REFERENCES app_user(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS agent_knowledge_source (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  agent_id     INT NOT NULL,
  source_type  ENUM('document','url','database_table','faq') NOT NULL,
  label        VARCHAR(150) NOT NULL,
  location     VARCHAR(500) NOT NULL,
  notes        TEXT,
  CONSTRAINT fk_knowledge_agent FOREIGN KEY (agent_id) REFERENCES agent(id) ON DELETE CASCADE,
  INDEX idx_knowledge_agent (agent_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS agent_tool (
  id        INT AUTO_INCREMENT PRIMARY KEY,
  agent_id  INT NOT NULL,
  tool_key  VARCHAR(100) NOT NULL,
  enabled   BOOLEAN NOT NULL DEFAULT TRUE,
  config    JSON NOT NULL,
  CONSTRAINT fk_tool_agent FOREIGN KEY (agent_id) REFERENCES agent(id) ON DELETE CASCADE,
  CONSTRAINT uq_agent_tool UNIQUE (agent_id, tool_key)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS agent_guardrail (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  agent_id        INT NOT NULL,
  guardrail_type  ENUM('blocked_topic','required_disclaimer','pii_redaction',
                       'refusal_message','max_response_words') NOT NULL,
  value           TEXT NOT NULL,
  CONSTRAINT fk_guardrail_agent FOREIGN KEY (agent_id) REFERENCES agent(id) ON DELETE CASCADE,
  INDEX idx_guardrail_agent (agent_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS chat_session (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  agent_id    INT NOT NULL,
  user_id     INT NOT NULL,
  title       VARCHAR(255) NOT NULL DEFAULT 'New conversation',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_chat_agent FOREIGN KEY (agent_id) REFERENCES agent(id)    ON DELETE CASCADE,
  CONSTRAINT fk_chat_user  FOREIGN KEY (user_id)  REFERENCES app_user(id) ON DELETE CASCADE,
  INDEX idx_chat_user (user_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS chat_message (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  session_id  INT NOT NULL,
  sender      ENUM('user','agent','system') NOT NULL,
  content     TEXT NOT NULL,
  meta        JSON NOT NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_message_session FOREIGN KEY (session_id) REFERENCES chat_session(id) ON DELETE CASCADE,
  INDEX idx_message_session (session_id)
) ENGINE=InnoDB;

-- ------------------------------------------------- Phase 2: memory (FR8) + NFR3 logging

CREATE TABLE IF NOT EXISTS role_preference (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  role        VARCHAR(20) NOT NULL,
  pref_key    VARCHAR(100) NOT NULL,
  preference  TEXT NOT NULL,
  priority    INT NOT NULL DEFAULT 0,
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT uq_role_preference UNIQUE (role, pref_key),
  INDEX idx_role_preference_role (role)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS request_log (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  session_id       INT,
  agent_id         INT,
  user_id          INT,
  role             VARCHAR(20),
  route            VARCHAR(20) NOT NULL,
  model_requested  VARCHAR(100) NOT NULL,
  model_used       VARCHAR(100) NOT NULL,
  used_fallback    BOOLEAN NOT NULL DEFAULT FALSE,
  total_ms         INT NOT NULL,
  model_ms         INT NOT NULL,
  tool_ms          INT NOT NULL DEFAULT 0,
  iterations       INT NOT NULL DEFAULT 0,
  tool_calls       INT NOT NULL DEFAULT 0,
  stop_reason      VARCHAR(50),
  succeeded        BOOLEAN NOT NULL DEFAULT TRUE,
  error            VARCHAR(255),
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_reqlog_session FOREIGN KEY (session_id) REFERENCES chat_session(id) ON DELETE SET NULL,
  CONSTRAINT fk_reqlog_agent   FOREIGN KEY (agent_id)   REFERENCES agent(id)        ON DELETE SET NULL,
  CONSTRAINT fk_reqlog_user    FOREIGN KEY (user_id)    REFERENCES app_user(id)     ON DELETE SET NULL,
  -- The NFR3 report groups by model and filters by date.
  INDEX idx_reqlog_created (created_at),
  INDEX idx_reqlog_model (model_used)
) ENGINE=InnoDB;

-- ------------------------------------------- Phase 3a: announcements, teaching, auditing

CREATE TABLE IF NOT EXISTS announcement (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  title         VARCHAR(255) NOT NULL,
  body          TEXT NOT NULL,
  class_id      INT,
  audience      VARCHAR(20) NOT NULL DEFAULT 'all',
  published_on  DATE NOT NULL,
  author_id     INT,
  CONSTRAINT fk_announcement_class  FOREIGN KEY (class_id)  REFERENCES `class`(id)  ON DELETE CASCADE,
  CONSTRAINT fk_announcement_author FOREIGN KEY (author_id) REFERENCES app_user(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- `class.teacher_id` is the form tutor only. Teaching is many-to-many, and a teacher's
-- access scope is every class they take, so it needs its own table. Relational twin of
-- the agso:teaches edge in the ontology.
CREATE TABLE IF NOT EXISTS teacher_class (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  teacher_id  INT NOT NULL,
  class_id    INT NOT NULL,
  subject     VARCHAR(100),
  CONSTRAINT fk_teacherclass_teacher FOREIGN KEY (teacher_id) REFERENCES app_user(id) ON DELETE CASCADE,
  CONSTRAINT fk_teacherclass_class   FOREIGN KEY (class_id)   REFERENCES `class`(id)  ON DELETE CASCADE,
  CONSTRAINT uq_teacher_class UNIQUE (teacher_id, class_id)
) ENGINE=InnoDB;

-- Every retrieval decision, allowed or refused (FR10 enforcement, FR12 admin review).
CREATE TABLE IF NOT EXISTS access_audit (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  user_id            INT,
  role               VARCHAR(20) NOT NULL,
  session_id         INT,
  template           VARCHAR(50) NOT NULL,
  question           TEXT,
  parameters         JSON NOT NULL,
  allowed            BOOLEAN NOT NULL,
  refusal_reason     VARCHAR(255),
  requested_student  VARCHAR(50),
  result_count       INT NOT NULL DEFAULT 0,
  created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_audit_user    FOREIGN KEY (user_id)    REFERENCES app_user(id)     ON DELETE SET NULL,
  CONSTRAINT fk_audit_session FOREIGN KEY (session_id) REFERENCES chat_session(id) ON DELETE SET NULL,
  -- The FR12 review screen filters on refusals, newest first.
  INDEX idx_audit_allowed (allowed, created_at),
  INDEX idx_audit_user (user_id)
) ENGINE=InnoDB;

-- ------------------------------------------------ Phase 3b: document corpus (FR4, FR9)

CREATE TABLE IF NOT EXISTS document (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  title           VARCHAR(255) NOT NULL,
  filename        VARCHAR(255) NOT NULL,
  doc_type        VARCHAR(50) NOT NULL DEFAULT 'general',
  media_type      VARCHAR(100) NOT NULL DEFAULT 'text/plain',
  -- Which roles may retrieve from this document. The document-level half of RBAC: it is
  -- what stops the staff handbook being retrievable for a parent.
  audience_roles  JSON NOT NULL,
  class_id        INT,
  source_path     VARCHAR(500) NOT NULL,
  checksum        CHAR(64) NOT NULL,
  byte_size       INT NOT NULL DEFAULT 0,
  status          ENUM('pending','indexed','failed') NOT NULL DEFAULT 'pending',
  error           VARCHAR(500),
  chunk_count     INT NOT NULL DEFAULT 0,
  uploaded_by     INT,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  indexed_at      DATETIME,
  CONSTRAINT fk_document_class  FOREIGN KEY (class_id)    REFERENCES `class`(id)  ON DELETE SET NULL,
  CONSTRAINT fk_document_upload FOREIGN KEY (uploaded_by) REFERENCES app_user(id) ON DELETE SET NULL,
  INDEX idx_document_status (status),
  INDEX idx_document_source (source_path)
) ENGINE=InnoDB;

-- Vectors are stored as raw little-endian float32 BLOBs and searched with numpy, not with
-- sqlite-vec: the python.org macOS build ships sqlite3 without loadable-extension support,
-- so no SQLite extension can be loaded at all. At ~1,000 chunks an exact dot product is
-- about a millisecond and has perfect recall. See app/rag/store.py.
CREATE TABLE IF NOT EXISTS document_chunk (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  document_id      INT NOT NULL,
  chunk_index      INT NOT NULL,
  text             TEXT NOT NULL,
  location         VARCHAR(100),
  token_estimate   INT NOT NULL DEFAULT 0,
  embedding        BLOB,
  embedding_model  VARCHAR(100),
  embedding_dim    INT NOT NULL DEFAULT 0,
  CONSTRAINT fk_chunk_document FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE,
  CONSTRAINT uq_document_chunk UNIQUE (document_id, chunk_index),
  INDEX idx_chunk_document (document_id)
) ENGINE=InnoDB;
