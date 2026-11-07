"""
============================================================
CMI — Citizen Mobility Intelligence
Configuration Module

Centralizes all environment variables, paths, and application
settings. Uses pydantic-settings for typed validation so that
missing keys surface immediately at startup rather than at
runtime deep inside a request handler.
============================================================
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic import Field


# ── Resolve project root ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent

# ── Directory contract ────────────────────────────────────────
# Every sub-path the application may read/write is declared here
# so that downstream modules never hard-code paths.
DIRS = {
    "root":       PROJECT_ROOT,
    "engines":    PROJECT_ROOT / "engines",
    "api":        PROJECT_ROOT / "api",
    "static":     PROJECT_ROOT / "static",
    "templates":  PROJECT_ROOT / "templates",
    "data_raw":   PROJECT_ROOT / "data" / "raw",
    "data_proc":  PROJECT_ROOT / "data" / "processed",
    "data_cache": PROJECT_ROOT / "data" / "cache",
    "scripts":    PROJECT_ROOT / "scripts",
    "tests":      PROJECT_ROOT / "tests",
}


class Settings(BaseSettings):
    """
    Typed, validated application settings loaded from .env.
    Pydantic-settings automatically reads a `.env` file in the
    project root and maps UPPER_SNAKE keys to these fields.
    """

    # ── AI Provider Keys ──────────────────────────────────────
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key (Model A priority)",
    )
    nvidia_api_key: str = Field(
        default="",
        description="NVIDIA NIM / NGC API key for LLM inference (Model B priority)",
    )
    groq_api_key: str = Field(
        default="",
        description="Groq Cloud API key for ultra-fast LLM inference (Model C priority)",
    )

    # ── SMS & OTP Gateway (Renflair & Twilio) ─────────────────
    renflair_api_key: str = Field(
        default="",
        description="Renflair SMS Gateway API key for mobile OTP dispatch",
    )
    twilio_account_sid: str = Field(
        default="",
        description="Twilio Account SID (starts with AC...)",
    )
    twilio_auth_token: str = Field(
        default="",
        description="Twilio primary Auth Token",
    )
    twilio_api_key_sid: str = Field(
        default="",
        description="Twilio API Key SID (starts with SK...)",
    )
    twilio_api_secret: str = Field(
        default="",
        description="Twilio API Key Secret",
    )
    twilio_phone_number: str = Field(
        default="",
        description="Twilio provisioned sending phone number (e.g. +1...)",
    )

    # ── Email & SMTP Gateway ──────────────────────────────────
    smtp_host: str = Field(
        default="smtp.gmail.com",
        description="SMTP server hostname (e.g. smtp.gmail.com, smtp.sendgrid.net)",
    )
    smtp_port: int = Field(
        default=587,
        description="SMTP server port (587 for TLS, 465 for SSL)",
    )
    smtp_user: str = Field(
        default="",
        description="SMTP authentication username / sender email address",
    )
    smtp_password: str = Field(
        default="",
        description="SMTP authentication password / Gmail App Password",
    )
    smtp_from_email: str = Field(
        default="",
        description="Sender email address displayed in From header",
    )
    smtp_from_name: str = Field(
        default="CMI Singara Chennai",
        description="Sender display name",
    )

    # ── Geolocation ───────────────────────────────────────────
    google_maps_api_key: str = Field(
        default="",
        description="Google Maps Platform key (Directions, Geocoding)",
    )

    # ── Supabase Database ─────────────────────────────────────
    supabase_url: str = Field(
        default="https://wuqkasfqcksgayyamvma.supabase.co",
        description="Supabase Project REST URL",
    )
    supabase_key: str = Field(
        default="sb_publishable_6rXTJ2XpsEoDlslFqixhHQ_uy7pGqsC",
        description="Supabase public anon key",
    )

    # ── DevOps Metadata ───────────────────────────────────────
    github_username: str = Field(default="Aakashkarthikeyan-1248")
    github_repo_name: str = Field(default="citizen-mobility-intelligence")
    vercel_account_id: str = Field(default="Aakash-1248")

    # ── App Runtime ───────────────────────────────────────────
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)
    app_env: str = Field(default="development")
    log_level: str = Field(default="DEBUG")

    # ── LLM Defaults ─────────────────────────────────────────
    llm_timeout_seconds: float = Field(
        default=4.5,
        description="Per-provider HTTP timeout before fallback",
    )
    llm_max_retries: int = Field(
        default=1,
        description="Retries within a single provider before fallback",
    )

    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Singleton accessor — ensures the .env file is parsed exactly once
    and the resulting Settings object is reused across the application.
    """
    return Settings()


def verify_workspace() -> dict[str, bool]:
    """
    Startup guard: verifies every directory in the DIRS contract
    exists, creates any that are missing, and returns a report dict
    mapping directory names → whether they already existed.

    Wrapped in try-except to guarantee safety on read-only serverless
    environments such as Vercel AWS Lambda (/var/task).
    """
    report: dict[str, bool] = {}
    for name, path in DIRS.items():
        existed = path.exists()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Serverless read-only filesystem (e.g. Vercel)
            pass
        report[name] = existed
    return report
