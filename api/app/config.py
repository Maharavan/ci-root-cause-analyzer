"""
System Configuration
"""
from pydantic_settings import BaseSettings
from pydantic import BaseModel,HttpUrl

class JenkinsServer(BaseModel):
    """
    Description: Model contains Jenkins components to provide structurized format

    Args:
        base_url: Jenkins URL for retrieving logs
        user: Username
        token: Authentication token
    """
    base_url: HttpUrl
    user: str
    token: str

class GithubServer(BaseModel):
    """
    Description: Model contains GitHub API configuration for workflow access

    Args:
        base_url: GitHub API base URL (default: https://api.github.com)
        token: GitHub personal access token with workflow read permissions
    """
    base_url: str = "https://api.github.com"
    token: str

class Settings(BaseSettings):
    """
    Configuration for Agentic CI-CD service
    """
    DB_HOST: str = "postgresql"
    DB_PORT: int = 5432
    POSTGRES_USER: str = "agentic"
    POSTGRES_PASSWORD: str = "agentic"
    LOG_PATH: str = "storage/logs"
    FAILURE_TABLE: str = "failures"
    POSTGRES_DB: str = "agentic_db"

    ##JENKINS SERVER
    JENKINS_URL: str = "https://localhost:8050/blue/rest/organizations/jenkins//blue/rest/organizations/jenkins/"
    JENKINS_USER: str
    JENKINS_TOKEN: str

    

    SEMANTIC_PATH: str

    LLM_API_KEY: str

    RCA_LLM_DEPLOYMENT: str = "gpt-4-mini"
    RCA_LLM_API_VERSION: str = "2024-08-01-preview"

    EMBEDDING_MODEL: str = "groq/llama-3.3-70b-versatile"
    RCA_TEMPERATURE: float = 0
    CLASSIFY_TEMPERATURE: float = 0

    SMTP_TOKEN: str
    SMTP_SERVER: str
    SMTP_PORT: int
    SMTP_USER: str
    SMTP_PASSWORD: str

    FAILURE_PATTERN_TABLE: str = "failure_knowledge_table"

    REDIS_PORT: str = 6379
    REDIS_HOST: str = "localhost"
    DEFAULT_MAIL: str
    ## GitHub Config
    GITHUB_TOKEN: str = ""
    GITHUB_API_BASE_URL: str = "https://api.github.com"

    class Config:
        env_file = ".env"

settings = Settings()

JENKINS_SERVER = JenkinsServer(
        base_url=settings.JENKINS_URL,
        user=settings.JENKINS_USER,
        token=settings.JENKINS_TOKEN,
)

GITHUB_SERVER = GithubServer(
    base_url=settings.GITHUB_API_BASE_URL,
    token=settings.GITHUB_TOKEN,
)

