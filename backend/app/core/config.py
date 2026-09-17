"""全局配置：读取项目根目录 .env（敏感信息不进代码/文档）。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> 项目根目录为上两级
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- MySQL ----------
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "bid_chat_db"

    # ---------- 阿里百炼（OpenAI 兼容） ----------
    llm_base_url: str = ""
    llm_api_key: str = ""
    text_model: str = "qwen3.8-flash"
    vision_model: str = "qwen3.8-flash"

    # ---------- 对话 Agent 专用模型（对话/定稿核验/追问建议；留空则回退上面主模型） ----------
    # 正文生成、思路生成等重活仍走 TEXT_MODEL，对话链路单独用指令遵循更强的模型。
    chat_base_url: str = ""
    chat_api_key: str = ""
    chat_model: str = ""

    # ---------- MinerU 云端解析 ----------
    mineru_api_base: str = "https://mineru.net/api/v4"
    mineru_api_token: str = ""
    # MinerU 解析引擎（提交时传 model_version）。默认 pipeline；如账户/套餐用新版 VLM 引擎
    #（如 vlm3.4.4）请在 .env 设 mineru_model_version=vlm3.4.4
    mineru_model_version: str = "pipeline"
    # 结果下载 CDN 的 IP 兜底（可选，逗号分隔）。仅当本机 DNS 被代理污染、导致
    # cdn-mineru.openxlab.org.cn 解析失败时才需要填；留空则走系统正常 DNS。
    mineru_cdn_ips: str = ""

    # ---------- 编制会话 ----------
    # 后端启动时自动续跑中断的编制会话（generating_* 阶段从 MySQL 检查点续跑；
    # confirm_* 阶段不自动跑，等用户确认）。MVP 默认关，按需开。
    compile_auto_resume: bool = False

    # ---------- 解析缓存 ----------
    # 上传文件按内容 MD5 判断：同文件此前已解析过（知识库或项目文件）则直接复用
    # 解析产物（full.md/content_list），不再重复走 MinerU（省时间与配额）
    reuse_parsed_by_md5: bool = True

    # ---------- 调试 ----------
    # 单章生成正文时，把实际发给模型的完整上下文（WRITER_SYSTEM + user）作为
    # debug_context 事件随 SSE 返回，前端 console.log（排查"生成与预期差距大"用）
    gen_debug_context: bool = False

    # ---------- 本地存储 ----------
    uploads_dir: Path = PROJECT_ROOT / "uploads"

    @property
    def db_url_jdbc(self) -> str:
        return (
            f"mysql://{self.mysql_user}:***@{self.mysql_host}:{self.mysql_port}"
            f"/{self.mysql_database}"
        )


settings = Settings()
settings.uploads_dir.mkdir(parents=True, exist_ok=True)
