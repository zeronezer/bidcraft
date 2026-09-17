-- ============================================================
-- 施组AI编制系统 - MySQL 8 建表脚本
-- 执行：mysql -h<你的MySQL主机> -u<用户> -p <数据库名> < scripts/init_db.sql
-- 或：python scripts/check_env.py（含自动建表）
-- ============================================================
USE bid_chat_db;

-- 项目
CREATE TABLE IF NOT EXISTS project (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(200) NOT NULL COMMENT '项目名称',
  description TEXT COMMENT '项目描述',
  project_type VARCHAR(50) DEFAULT '' COMMENT '工程类型：铁路/公路/房建/市政…',
  toc_hint JSON NULL COMMENT '招标文件规定的施组目录（解析招标文件时提取，目录生成优先采用）',
  status VARCHAR(20) DEFAULT 'active' COMMENT 'active/deleted',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='项目';

-- 标段（仅招标文件存在标段划分时产生；无标段划分的项目可无记录）
CREATE TABLE IF NOT EXISTS lot (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_id BIGINT NOT NULL,
  lot_code VARCHAR(100) NOT NULL COMMENT '标段编号，如 CGJXZQ-3',
  lot_name VARCHAR(200) DEFAULT '',
  price_limit DECIMAL(18,4) NULL COMMENT '最高投标限价（万元）',
  extra JSON NULL COMMENT '其他抽取信息',
  profile JSON NULL COMMENT '标段档案：里程范围/工程量/主要构造物/大型临时设施/过渡工程/弃土渣场/关键里程碑/相邻接口/特殊约束（JSON）',
  profile_source VARCHAR(300) NULL COMMENT '档案数据来源（文件名·章节，多来源分号分隔）',
  profile_updated_at DATETIME NULL COMMENT '档案最后更新时间',
  selected TINYINT DEFAULT 0 COMMENT '用户选定的标段（M1-2：多选一/单标段确认）',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标段';

-- 项目文件（招标文件/指导性施组/参考图纸/答疑补遗/踏勘报告…）
CREATE TABLE IF NOT EXISTS project_file (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_id BIGINT NOT NULL,
  lot_id BIGINT NULL COMMENT '归属标段（可为空）',
  file_name VARCHAR(500) NOT NULL,
  file_path VARCHAR(500) NOT NULL COMMENT '存储相对路径（uploads/）',
  file_type VARCHAR(20) DEFAULT '' COMMENT 'pdf/docx/doc/png/jpg/xlsx',
  category VARCHAR(50) DEFAULT 'other' COMMENT 'tender/guiding_sod/drawing/clarification/survey_report/planning/other',
  size_bytes BIGINT DEFAULT 0,
  status VARCHAR(20) DEFAULT 'pending' COMMENT 'pending/parsing/parsed/failed',
  parsed_path VARCHAR(500) NULL COMMENT '解析产物路径（Markdown/JSON）',
  file_md5 VARCHAR(32) NULL DEFAULT NULL COMMENT '文件内容 MD5（同文件复用已解析结果，跳过 MinerU）',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id),
  KEY idx_category (category),
  KEY idx_md5 (file_md5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='项目文件';

-- 知识库文档（两类知识库的原料：历史施组/工艺工法）
CREATE TABLE IF NOT EXISTS knowledge_doc (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  file_name VARCHAR(500) NOT NULL,
  file_path VARCHAR(500) NOT NULL,
  doc_category VARCHAR(30) DEFAULT 'sod' COMMENT '知识分类：sod=历史施组 / method=工艺工法',
  project_tags JSON NULL COMMENT '来源项目标签：[工程类型, 年份…]',
  status VARCHAR(20) DEFAULT 'pending' COMMENT 'pending/parsing/parsed/failed',
  parsed_path VARCHAR(500) NULL,
  file_md5 VARCHAR(32) NULL DEFAULT NULL COMMENT '文件内容 MD5（同文件复用已解析结果，跳过 MinerU）',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_status (status),
  KEY idx_category (doc_category),
  KEY idx_md5 (file_md5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库文档（原料仓，两类知识）';

-- 知识页（从历史施组文档提炼的成品，写法参考）
CREATE TABLE IF NOT EXISTS knowledge_page (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  doc_id BIGINT NOT NULL,
  chapter_type VARCHAR(50) NOT NULL COMMENT '骨架章节类型（约12类，见方案3.2）',
  title VARCHAR(300) DEFAULT '',
  tags JSON NULL COMMENT '专业标签：["路基","桥梁"]',
  content JSON NOT NULL COMMENT '知识页 schema：method/key_points/tables/images/usage…',
  source_location VARCHAR(300) NULL COMMENT '原文出处（文档+页码/章节）',
  status VARCHAR(20) DEFAULT 'draft' COMMENT 'draft/confirmed',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_doc (doc_id),
  KEY idx_chapter_type (chapter_type),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='章节知识页（历史施组库成品）';

-- 工法条目（工艺工法库成品，施工方案章的方法素材）
CREATE TABLE IF NOT EXISTS method_entry (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  doc_id BIGINT NOT NULL,
  name VARCHAR(300) NOT NULL COMMENT '工法名称，如"钻孔灌注桩施工工艺"',
  applies_to JSON NULL COMMENT '适用场景标签：["桥梁基础","桩基"]',
  content JSON NOT NULL COMMENT '工法 schema：process/key_controls/equipment/quality_points/tables/images…',
  status VARCHAR(20) DEFAULT 'draft' COMMENT 'draft/confirmed',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_name (name),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工法条目（旧版结构，工艺工法库重构后弃用）';

-- 工艺工法概况索引（2026-09-04 重构：一工法一条概况；正文编写时按概况选工法、整篇文档注入）
CREATE TABLE IF NOT EXISTS method_index (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  doc_id BIGINT NOT NULL COMMENT 'knowledge_doc.id',
  name VARCHAR(300) NOT NULL COMMENT '工法名称（如"钻孔灌注桩施工工艺"）',
  summary VARCHAR(600) DEFAULT '' COMMENT '工法概况（做什么/关键工艺/适用结构 50-200字）',
  applies_to VARCHAR(400) DEFAULT '' COMMENT '适用范围（编写哪些章节/问题时应参考本工法）',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_doc (doc_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工艺工法概况索引（一工法一条，用时选工法取整文）';

-- 项目文件章节索引（skill 式按需加载：目录+概要+适用范围做索引，用时选章节再取原文）
CREATE TABLE IF NOT EXISTS file_section_index (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  file_id BIGINT NOT NULL COMMENT 'project_file.id',
  project_id BIGINT NOT NULL,
  sec_level INT NOT NULL DEFAULT 1 COMMENT '标题层级（1-3 级入索引）',
  sec_path VARCHAR(500) NOT NULL COMMENT '章节全路径标题（父 > 子）',
  summary VARCHAR(500) DEFAULT '' COMMENT 'AI 章节概要（该节讲了什么）',
  applies_to VARCHAR(300) DEFAULT '' COMMENT '适用范围（编写什么样的问题/章节时参考本节）',
  start_line INT NOT NULL DEFAULT 0 COMMENT 'full.md 起始行号（0 起）',
  end_line INT NOT NULL DEFAULT 0 COMMENT 'full.md 结束行号（不含）',
  char_count INT DEFAULT 0 COMMENT '章节字符数（含子节）',
  applies_lots JSON NULL COMMENT '本章适用的标段：["CGJXZQ-10"]本标段/["all"]全线共性/["background"]全线背景/["multi"]多标段/[]未判定',
  lot_evidence VARCHAR(255) NULL COMMENT '标段归属判定依据（命中的构造物/里程/判定方式）',
  content MEDIUMTEXT NULL COMMENT '本章节原文（自包含：取原文不依赖文件与行号，2026-09-10 改造）',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_file (file_id),
  KEY idx_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='项目文件章节索引（按需加载原文，替代切片 RAG）';

-- 经验条目（对话/编写过程沉淀）
CREATE TABLE IF NOT EXISTS experience_item (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  exp_type VARCHAR(30) NOT NULL COMMENT '编制经验/编写经验/表述偏好/纠偏规则',
  chapter_type VARCHAR(50) DEFAULT '' COMMENT '骨架章节类型',
  tags JSON NULL COMMENT '专业/工程标签',
  content TEXT NOT NULL,
  source JSON NULL COMMENT '来源追溯：project_id/message_id/diff_summary',
  status VARCHAR(20) DEFAULT 'pending' COMMENT 'pending/confirmed',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_type (exp_type),
  KEY idx_chapter (chapter_type),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='经验条目（对话沉淀）';

-- 目录树节点
CREATE TABLE IF NOT EXISTS chapter_node (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_id BIGINT NOT NULL,
  lot_id BIGINT NULL,
  parent_id BIGINT NULL COMMENT '父节点（NULL=一级章节）',
  title VARCHAR(300) NOT NULL,
  sort_order INT DEFAULT 0 COMMENT '同级排序',
  status VARCHAR(20) DEFAULT 'unwritten' COMMENT 'unwritten/thought_ready/generated/confirmed',
  outline JSON NULL COMMENT '编写思路（确认思路后落库，正文生成的输入）',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id),
  KEY idx_parent (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='目录树节点';

-- 正文（与目录节点一对一，TipTap JSON 文档）
CREATE TABLE IF NOT EXISTS section_content (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  chapter_node_id BIGINT NOT NULL UNIQUE,
  content JSON NULL COMMENT 'TipTap 文档 JSON（唯一内容载体）',
  ai_generated TINYINT DEFAULT 0 COMMENT '最近一次写入是否 AI 生成',
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='章节正文';

-- 对话会话与消息
CREATE TABLE IF NOT EXISTS chat_session (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_id BIGINT NOT NULL,
  title VARCHAR(200) DEFAULT '新会话',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话会话';

CREATE TABLE IF NOT EXISTS chat_message (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id BIGINT NOT NULL,
  role VARCHAR(20) NOT NULL COMMENT 'user/assistant/system',
  content LONGTEXT NOT NULL,
  refs JSON NULL COMMENT '右键引用上下文 [{type,ref,content}]',
  trace JSON NULL COMMENT '本助手消息的工具调用轨迹(名称/参数摘要/成败)',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话消息';

-- 全局事实表（共享事实源，见方案 3.9）
CREATE TABLE IF NOT EXISTS global_fact (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_id BIGINT NOT NULL,
  lot_id BIGINT NULL COMMENT '标段级事实（NULL=项目级）',
  category VARCHAR(32) DEFAULT '' COMMENT '工期/人员/机械/造价/地质/结构/质量标准/安全目标…',
  fact_key VARCHAR(100) NOT NULL COMMENT '事实名：总工期/塔吊数量…',
  fact_value VARCHAR(200) NOT NULL,
  unit VARCHAR(30) DEFAULT '',
  source_file VARCHAR(500) NULL COMMENT '出处文件',
  source_location VARCHAR(200) NULL COMMENT '出处页码/章节',
  confidence DECIMAL(4,3) DEFAULT 1.000 COMMENT '抽取置信度 0~1',
  applicable_lots JSON NULL COMMENT '适用标段代码列表：["all"]=全线通用，["HSZQ-11"]=指定标段（NULL=旧数据视为通用）',
  status VARCHAR(20) DEFAULT 'pending' COMMENT 'pending/confirmed/expired',
  edited TINYINT DEFAULT 0 COMMENT '人工编辑过：1=重抽取时保留、不覆盖',
  version INT DEFAULT 1,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_project (project_id),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='全局事实表';

-- 异步任务（MVP 用线程池 + 任务表；生产替换 Celery）
CREATE TABLE IF NOT EXISTS task (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_type VARCHAR(50) NOT NULL COMMENT 'parse/extract/generate/qc…',
  ref_id BIGINT NULL COMMENT '关联对象 id（文件/项目…）',
  status VARCHAR(20) DEFAULT 'running' COMMENT 'running/success/failed',
  progress INT DEFAULT 0 COMMENT '0~100',
  detail TEXT NULL COMMENT '结果摘要或错误信息',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='异步任务';

-- 目录模板库（骨架 + 专业子目录模板，如铁路 12 章模板）
CREATE TABLE IF NOT EXISTS toc_template (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(200) NOT NULL COMMENT '模板名称：铁路工程实施性施组模板',
  project_type VARCHAR(50) DEFAULT '' COMMENT '适用工程类型',
  toc JSON NOT NULL COMMENT '目录树 JSON',
  is_default TINYINT DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='目录模板库';

-- 招标要求（评分办法/技术要求结构化提取 → 章节覆盖检查清单原料，M5-6 质检对照）
CREATE TABLE IF NOT EXISTS tender_requirement (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_id BIGINT NOT NULL,
  category VARCHAR(20) DEFAULT '技术要求' COMMENT '评分办法/技术要求',
  title VARCHAR(300) NOT NULL COMMENT '要求/评分项名称',
  content TEXT COMMENT '评分标准或要求内容',
  weight DECIMAL(8,2) NULL COMMENT '分值/权重（评分办法用）',
  suggested_chapter VARCHAR(300) DEFAULT '' COMMENT '建议落到的施组章节',
  applicable_lots JSON NULL COMMENT '该要求适用的标段（同 applies_lots 取值；空/含 all 表示全线通用）',
  source_file VARCHAR(500) DEFAULT '',
  source_location VARCHAR(200) DEFAULT '',
  status VARCHAR(20) DEFAULT 'pending' COMMENT 'pending/confirmed/ignored',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id),
  KEY idx_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='招标要求（覆盖检查清单）';

-- 编制会话（编排状态机进度/中断内容持久化；进程重启可恢复，见 compile_service）
CREATE TABLE IF NOT EXISTS compile_session (
  project_id BIGINT PRIMARY KEY,
  thread_id VARCHAR(100) NOT NULL COMMENT 'LangGraph 检查点 thread_id（每次新 run 换新）',
  stage VARCHAR(30) NOT NULL DEFAULT 'idle' COMMENT 'idle/generating_toc/confirm_toc/generating_outline/confirm_outline/writing/done/failed/outlines_ready',
  progress INT DEFAULT 0,
  total INT DEFAULT 0,
  interrupt_json JSON NULL COMMENT '中断点内容（目录/思路）',
  result_json JSON NULL COMMENT '完成时的轻量结果摘要',
  partial_json JSON NULL COMMENT '生成中的部分产物（目录流式：章骨架/已完成的子节，前端实时渲染进度）',
  error TEXT NULL,
  running TINYINT DEFAULT 0,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='编制会话';
