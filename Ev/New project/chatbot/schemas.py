from __future__ import annotations

from app.config import get_utils_repo_path
from pydantic import BaseModel, Field, field_validator


class NormalRunRequest(BaseModel):
    pilot: str = Field(description="Pilot key from the S3 pilot config")
    date: str = Field(description="Run date in YYYYMMDD format")
    create_pr: bool = Field(default=False, description="Whether to export scripts and create a PR")
    repo_path: str | None = Field(default=None, description="Local path to the Utils repo")
    gchat_webhook_url: str | None = Field(default=None, description="Optional Google Chat incoming webhook URL")
    frontend_url: str | None = Field(default=None, description="Optional frontend URL to include in summaries and Google Chat")
    output_dir: str = Field(default="output", description="Base output directory")
    checkforev_zero_min_id: int = Field(default=995, description="Lower id bound for CheckForEV=0 query")

    @field_validator("pilot")
    @classmethod
    def validate_pilot(cls, value: str) -> str:
        return value.lower().strip()

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        if len(value) != 8 or not value.isdigit():
            raise ValueError("date must be in YYYYMMDD format")
        return value

    @field_validator("repo_path")
    @classmethod
    def validate_repo_path(cls, value: str | None, info) -> str | None:
        create_pr = info.data.get("create_pr", False)
        if create_pr and (value is None or not value.strip()) and get_utils_repo_path(required=False) is None:
            raise ValueError("repo_path is required when create_pr is true")
        return value


class SpecialRunRequest(BaseModel):
    pilot: str = Field(description="Pilot key from the S3 pilot config")
    date: str = Field(description="Run date in YYYYMMDD format")
    meter_list_s3: str = Field(description="S3 URI to client-provided meter list CSV")
    request_name: str = Field(description="Short identifier for the special request")
    create_pr: bool = Field(default=False, description="Whether to export scripts and create a PR")
    repo_path: str | None = Field(default=None, description="Local path to the Utils repo")
    gchat_webhook_url: str | None = Field(default=None, description="Optional Google Chat incoming webhook URL")
    frontend_url: str | None = Field(default=None, description="Optional frontend URL to include in summaries and Google Chat")
    output_dir: str = Field(default="output/special", description="Base output directory")
    checkforev_zero_min_id: int = Field(default=995, description="Lower id bound for CheckForEV=0 query")

    @field_validator("pilot")
    @classmethod
    def validate_special_pilot(cls, value: str) -> str:
        return value.lower().strip()

    @field_validator("date")
    @classmethod
    def validate_special_date(cls, value: str) -> str:
        if len(value) != 8 or not value.isdigit():
            raise ValueError("date must be in YYYYMMDD format")
        return value

    @field_validator("repo_path")
    @classmethod
    def validate_special_repo_path(cls, value: str | None, info) -> str | None:
        create_pr = info.data.get("create_pr", False)
        if create_pr and (value is None or not value.strip()) and get_utils_repo_path(required=False) is None:
            raise ValueError("repo_path is required when create_pr is true")
        return value


class RunSummaryResponse(BaseModel):
    run_id: str
    pilot: str
    date: str
    status: str
    output_dir: str | None = None
    frontend_url: str | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    mysql_counts: dict[str, int]
    redshift_counts: dict[str, int]
    script_counts: dict[str, int]
    mysql_meter_files: dict[str, str | None] = Field(default_factory=dict)
    redshift_meter_files: dict[str, str | None] = Field(default_factory=dict)
    meter_files_folder_s3_uri: str | None = None
    message: str | None = None


class RunSectionResponse(BaseModel):
    run_id: str
    pilot: str
    date: str
    section: str
    data: dict[str, str | int | None]


class InteractionOption(BaseModel):
    id: str
    label: str


class InteractionResponse(BaseModel):
    title: str
    message: str
    options: list[InteractionOption]
