import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, AliasChoices

_program_data = Path(os.environ.get('PROGRAMDATA', Path.home())) / 'CODEZONE'
_env_file = _program_data / 'config.env'

class Settings(BaseSettings):
    app_name: str = 'CODEZONE Business Management System'
    database_url: str = ''
    secret_key: str = ''
    access_token_expire_minutes: int = 480
    email_domain: str = Field(default='', validation_alias=AliasChoices('CODEZONE_EMAIL_DOMAIN','EMAIL_DOMAIN'))
    email_provider: str = Field(default='none', validation_alias=AliasChoices('CODEZONE_EMAIL_PROVIDER','EMAIL_PROVIDER'))
    cpanel_base_url: str = Field(default='', validation_alias=AliasChoices('CODEZONE_CPANEL_BASE_URL','CPANEL_BASE_URL'))
    cpanel_username: str = Field(default='', validation_alias=AliasChoices('CODEZONE_CPANEL_USERNAME','CPANEL_USERNAME'))
    cpanel_api_token: str = Field(default='', validation_alias=AliasChoices('CODEZONE_CPANEL_API_TOKEN','CPANEL_API_TOKEN'))
    cpanel_default_quota_mb: int = Field(default=1024, validation_alias=AliasChoices('CODEZONE_CPANEL_DEFAULT_QUOTA_MB','CPANEL_DEFAULT_QUOTA_MB'))
    cpanel_timeout_seconds: int = Field(default=20, validation_alias=AliasChoices('CODEZONE_CPANEL_TIMEOUT_SECONDS','CPANEL_TIMEOUT_SECONDS'))
    model_config = SettingsConfigDict(env_file=str(_env_file) if _env_file.exists() else '.env', extra='ignore')

settings = Settings()
if not settings.secret_key:
    raise RuntimeError('CODEZONE SECRET_KEY is not configured. The installer must create %PROGRAMDATA%\\CODEZONE\\config.env or a development .env must provide SECRET_KEY.')
if len(settings.secret_key.encode('utf-8')) < 32:
    raise RuntimeError('CODEZONE SECRET_KEY must be at least 32 bytes long.')
