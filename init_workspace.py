"""
============================================================
CMI — Citizen Mobility Intelligence
Workspace Initialization & Architecture Provisioner

This script establishes, verifies, flags, and provisions the
complete architectural directory hierarchy and core manifests
for the CMI engine inside E:\\cmi.

Antigravity Principles:
- Zero global state mutation
- Idempotent execution (safe to run repeatedly)
- Strict typed contracts and paths resolution
- Explicit diagnostics report on directory & permission health
============================================================
"""

from __future__ import annotations

import os
import sys
import json
import shutil
import logging
from pathlib import Path

# Configure console logger with high-visibility formatting
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cmi.init")

# ── Primary Root Configuration ─────────────────────────────────
LOCAL_TARGET_PATH = Path(r"E:\cmi").resolve()

# ── Target Directory Tree Contract ─────────────────────────────
TARGET_DIRECTORIES: dict[str, str] = {
    "root":            "",
    "api":             "api",
    "engines":         "engines",
    "static":          "static",
    "templates":       "templates",
    "data_raw":        "data/raw",
    "data_processed":  "data/processed",
    "data_cache":      "data/cache",
    "scripts":         "scripts",
    "tests":           "tests",
    "vercel_meta":     ".vercel",
}

# ── Required Files Checklist ───────────────────────────────────
CORE_MANIFESTS: list[str] = [
    ".env",
    ".env.example",
    ".gitignore",
    "config.py",
    "main.py",
    "requirements.txt",
    "vercel.json",
    "README.md",
    "engines/__init__.py",
    "engines/text_isolation.py",
    "engines/llm_router.py",
    "engines/route_harvester.py",
    "api/__init__.py",
    "api/routes.py",
    "scripts/deploy.py",
    "scripts/deploy.sh",
    "static/index.html",
    ".vercel/project.json",
]


class WorkspaceInitializer:
    """
    Validates, provisions, and audits the CMI directory and file infrastructure.
    """

    def __init__(self, target_root: Path = LOCAL_TARGET_PATH):
        self.root = target_root
        self.report: dict[str, dict] = {
            "directories": {},
            "files": {},
            "permissions": {},
        }

    def verify_and_provision_directories(self) -> None:
        """
        Walks every declared directory in the contract.
        Flags whether it already existed or had to be provisioned.
        """
        logger.info("Initializing CMI workspace hierarchy at: %s", self.root)

        for key, rel_path in TARGET_DIRECTORIES.items():
            full_path = self.root / rel_path if rel_path else self.root
            existed = full_path.exists()

            if not existed:
                try:
                    full_path.mkdir(parents=True, exist_ok=True)
                    status = "CREATED"
                except Exception as exc:
                    status = f"FAILED: {exc}"
            else:
                status = "EXISTS"

            # Check write permission
            can_write = os.access(str(full_path), os.W_OK)

            self.report["directories"][key] = {
                "path": str(full_path),
                "status": status,
                "writable": can_write,
            }
            logger.info("  [%s] %-16s -> %s (Writable: %s)", status, key, full_path, can_write)

            # Ensure .gitkeep in empty data directories
            if "data" in key:
                gitkeep = full_path / ".gitkeep"
                if not gitkeep.exists():
                    gitkeep.touch()

    def audit_core_manifests(self) -> None:
        """
        Verifies that every architectural module and configuration file
        is present and readable.
        """
        logger.info("Auditing core architectural manifests...")
        for rel_file in CORE_MANIFESTS:
            file_path = self.root / rel_file
            if file_path.exists() and file_path.is_file():
                size_bytes = file_path.stat().st_size
                self.report["files"][rel_file] = {
                    "status": "PRESENT",
                    "bytes": size_bytes,
                }
                logger.info("  [PRESENT] %-28s (%d bytes)", rel_file, size_bytes)
            else:
                self.report["files"][rel_file] = {
                    "status": "MISSING",
                    "bytes": 0,
                }
                logger.warning("  [MISSING] %-28s", rel_file)

    def test_environment_permissions(self) -> bool:
        """
        Tests read/write/delete roundtrip inside the target root.
        """
        test_file = self.root / ".cmi_perm_test.tmp"
        try:
            test_file.write_text("cmi_init_probe", encoding="utf-8")
            content = test_file.read_text(encoding="utf-8")
            test_file.unlink()
            success = content == "cmi_init_probe"
            self.report["permissions"]["root_rw"] = success
            return success
        except Exception as exc:
            logger.error("Permissions test failed: %s", exc)
            self.report["permissions"]["root_rw"] = False
            return False

    def print_summary(self) -> None:
        """Prints a structured architectural status summary."""
        print("\n" + "=" * 70)
        print("          CMI WORKSPACE PROVISIONING AUDIT SUMMARY")
        print("=" * 70)
        print(f"Target Root       : {self.root}")
        print(f"Total Dirs Tracked: {len(self.report['directories'])}")
        print(f"Total Files Audited: {len(self.report['files'])}")
        
        missing = [f for f, v in self.report["files"].items() if v["status"] == "MISSING"]
        if missing:
            print(f"Status            : [!] INCOMPLETE ({len(missing)} files missing)")
            for m in missing:
                print(f"   - {m}")
        else:
            print("Status            : [OK] FULLY PROVISIONED & VERIFIED")
        print("=" * 70 + "\n")


def main():
    initializer = WorkspaceInitializer()
    initializer.verify_and_provision_directories()
    initializer.test_environment_permissions()
    initializer.audit_core_manifests()
    initializer.print_summary()


if __name__ == "__main__":
    main()
