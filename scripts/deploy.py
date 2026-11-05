"""
============================================================
CMI — Deployment Automation Manager

Automates the full Git + Vercel deployment pipeline:
  1. Initialize git repository (if not already initialized).
  2. Add the GitHub remote.
  3. Stage, commit, and push to the master branch.
  4. Write .vercel/project.json configuration.
  5. Optionally invoke Vercel CLI for deployment.

Usage:
    python scripts/deploy.py              # Full pipeline
    python scripts/deploy.py --git-only   # Git operations only
    python scripts/deploy.py --vercel-only # Vercel config only

All operations are idempotent — safe to run multiple times.
============================================================
"""

from __future__ import annotations

import os
import sys
import json
import subprocess
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone

# ── Resolve project root (one level up from scripts/) ──────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cmi.deploy")


# ── Git Operations ─────────────────────────────────────────────

def run_cmd(cmd: list[str], cwd: Path = PROJECT_ROOT) -> tuple[int, str]:
    """
    Execute a shell command and return (exit_code, output).
    Logs the command being run for auditability.
    """
    logger.info("$ %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    if output:
        for line in output.splitlines():
            logger.info("  %s", line)
    return result.returncode, output


def git_init():
    """Initialize a git repository if one doesn't exist."""
    git_dir = PROJECT_ROOT / ".git"
    if git_dir.exists():
        logger.info("Git repository already initialized.")
        return
    run_cmd(["git", "init"])
    logger.info("Git repository initialized at %s", PROJECT_ROOT)


def git_add_remote(settings):
    """
    Add the GitHub remote. If it already exists, update the URL.
    Uses HTTPS by default (switch to SSH if keys are configured).
    Automatically normalizes spaces in repo names to hyphens.
    """
    repo_slug = settings.github_repo_name.strip().replace(" ", "-")
    remote_url = (
        f"https://github.com/{settings.github_username}/"
        f"{repo_slug}.git"
    )

    # Check if 'origin' remote already exists
    code, output = run_cmd(["git", "remote", "get-url", "origin"])
    if code == 0:
        if output.strip() == remote_url:
            logger.info("Remote 'origin' already set to %s", remote_url)
            return
        else:
            logger.info("Updating remote 'origin' URL...")
            run_cmd(["git", "remote", "set-url", "origin", remote_url])
    else:
        run_cmd(["git", "remote", "add", "origin", remote_url])

    logger.info("Remote 'origin' → %s", remote_url)


def git_commit_and_push(message: str | None = None):
    """
    Stage all changes, commit with a timestamped message,
    and push to origin/master.
    """
    if message is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        message = f"CMI deployment — {ts}"

    # Stage all files
    run_cmd(["git", "add", "-A"])

    # Check if there are changes to commit
    code, _ = run_cmd(["git", "diff", "--cached", "--quiet"])
    if code == 0:
        logger.info("No changes to commit.")
    else:
        run_cmd(["git", "commit", "-m", message])

    # Ensure we're on master branch
    run_cmd(["git", "branch", "-M", "master"])

    # Push (will fail if no network / no repo created — that's expected)
    code, output = run_cmd(["git", "push", "-u", "origin", "master"])
    if code != 0:
        logger.warning(
            "Push failed (expected if repo doesn't exist on GitHub yet). "
            "Create the repo first, then re-run this script."
        )


# ── Vercel Configuration ──────────────────────────────────────

def write_vercel_config(settings):
    """
    Write .vercel/project.json with the account linkage.
    This file tells the Vercel CLI which project/account to deploy to.
    """
    vercel_dir = PROJECT_ROOT / ".vercel"
    vercel_dir.mkdir(parents=True, exist_ok=True)

    repo_slug = settings.github_repo_name.strip().replace(" ", "-")
    config = {
        "projectId": f"prj_{repo_slug}",
        "orgId": settings.vercel_account_id,
        "settings": {
            "framework": None,
            "buildCommand": "pip install -r requirements.txt",
            "outputDirectory": "static",
            "installCommand": "pip install -r requirements.txt",
            "devCommand": "uvicorn main:app --host 0.0.0.0 --port 8000 --reload",
        },
    }

    config_path = vercel_dir / "project.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    logger.info("Vercel config written to %s", config_path)


def write_vercel_json():
    """
    Write vercel.json in the project root for deployment routing.
    Routes all non-static requests to the FastAPI application.
    """
    vercel_json = {
        "version": 2,
        "builds": [
            {
                "src": "main.py",
                "use": "@vercel/python",
            }
        ],
        "routes": [
            {
                "src": "/static/(.*)",
                "dest": "/static/$1",
            },
            {
                "src": "/(.*)",
                "dest": "/main.py",
            },
        ],
    }

    config_path = PROJECT_ROOT / "vercel.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(vercel_json, f, indent=2)

    logger.info("vercel.json written to %s", config_path)


def invoke_vercel_cli():
    """
    Attempt to invoke the Vercel CLI for deployment.
    Skips gracefully if the CLI is not installed.
    """
    # Check if vercel CLI is available
    code, _ = run_cmd(["vercel", "--version"])
    if code != 0:
        logger.warning(
            "Vercel CLI not found. Install it with: npm i -g vercel"
        )
        logger.info("Vercel config files have been written. "
                     "Run 'vercel' manually after installing the CLI.")
        return

    # Link project (interactive — user will need to confirm)
    logger.info("Linking project to Vercel...")
    run_cmd(["vercel", "link", "--yes"])

    logger.info(
        "Vercel linked. Run 'vercel --prod' to deploy to production."
    )


# ── Main Pipeline ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CMI Deployment Automation Manager"
    )
    parser.add_argument(
        "--git-only",
        action="store_true",
        help="Run git operations only (init, remote, commit, push)",
    )
    parser.add_argument(
        "--vercel-only",
        action="store_true",
        help="Write Vercel configuration only",
    )
    parser.add_argument(
        "--message", "-m",
        type=str,
        default=None,
        help="Custom git commit message",
    )
    args = parser.parse_args()

    settings = get_settings()

    logger.info("=" * 60)
    logger.info("CMI Deployment Manager")
    logger.info("Project root: %s", PROJECT_ROOT)
    logger.info("=" * 60)

    if not args.vercel_only:
        # ── Git Pipeline ───────────────────────────────────────
        logger.info("─── Git Operations ───")
        git_init()
        git_add_remote(settings)
        git_commit_and_push(message=args.message)

    if not args.git_only:
        # ── Vercel Pipeline ────────────────────────────────────
        logger.info("─── Vercel Configuration ───")
        write_vercel_config(settings)
        write_vercel_json()
        invoke_vercel_cli()

    logger.info("=" * 60)
    logger.info("Deployment pipeline complete.")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
