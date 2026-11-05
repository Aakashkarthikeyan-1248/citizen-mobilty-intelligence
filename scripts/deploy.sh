#!/usr/bin/env bash
# ============================================================
# CMI — Deployment Automation Script (Bash)
#
# Automates: Git init → remote → commit → push → Vercel config
#
# Usage:
#     bash scripts/deploy.sh
#     bash scripts/deploy.sh "Custom commit message"
#
# Prerequisites:
#     - git installed and configured
#     - GitHub repo created (manually or via gh CLI)
#     - Vercel CLI installed (npm i -g vercel) — optional
# ============================================================

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────
PROJECT_ROOT="E:/cmi"
GITHUB_USERNAME="Aakashkarthikeyan-1248"
GITHUB_REPO_NAME="citizen-mobility-intelligence"
VERCEL_ACCOUNT_ID="Aakash-1248"
REMOTE_URL="https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO_NAME}.git"
COMMIT_MSG="${1:-CMI deployment — $(date -u '+%Y-%m-%d %H:%M UTC')}"

# ── Colors ─────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()   { echo -e "${GREEN}[CMI]${NC} $1"; }
warn()  { echo -e "${YELLOW}[CMI WARN]${NC} $1"; }
error() { echo -e "${RED}[CMI ERROR]${NC} $1"; }

# ── Navigate to project root ──────────────────────────────────
cd "$PROJECT_ROOT" || { error "Cannot cd to $PROJECT_ROOT"; exit 1; }
log "Working directory: $(pwd)"

# ── Step 1: Git Init ──────────────────────────────────────────
log "─── Step 1: Git Initialization ───"
if [ -d ".git" ]; then
    log "Git already initialized."
else
    git init
    log "Git repository initialized."
fi

# ── Step 2: Add Remote ────────────────────────────────────────
log "─── Step 2: GitHub Remote ───"
CURRENT_URL=$(git remote get-url origin 2>/dev/null || echo "")
if [ "$CURRENT_URL" = "$REMOTE_URL" ]; then
    log "Remote 'origin' already set correctly."
elif [ -n "$CURRENT_URL" ]; then
    git remote set-url origin "$REMOTE_URL"
    log "Remote 'origin' URL updated → $REMOTE_URL"
else
    git remote add origin "$REMOTE_URL"
    log "Remote 'origin' added → $REMOTE_URL"
fi

# ── Step 3: Stage & Commit ────────────────────────────────────
log "─── Step 3: Stage & Commit ───"
git add -A

if git diff --cached --quiet 2>/dev/null; then
    log "No changes to commit."
else
    git commit -m "$COMMIT_MSG"
    log "Committed: $COMMIT_MSG"
fi

# ── Step 4: Push to Master ────────────────────────────────────
log "─── Step 4: Push to origin/master ───"
git branch -M master
if git push -u origin master; then
    log "Pushed successfully to origin/master."
else
    warn "Push failed. Ensure the GitHub repo exists:"
    warn "  https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO_NAME}"
    warn "Create it, then re-run this script."
fi

# ── Step 5: Vercel Configuration ──────────────────────────────
log "─── Step 5: Vercel Configuration ───"

# Write .vercel/project.json
mkdir -p .vercel
cat > .vercel/project.json <<EOF
{
  "projectId": "prj_${GITHUB_REPO_NAME}",
  "orgId": "${VERCEL_ACCOUNT_ID}",
  "settings": {
    "framework": null,
    "buildCommand": "pip install -r requirements.txt",
    "outputDirectory": "static",
    "installCommand": "pip install -r requirements.txt",
    "devCommand": "uvicorn main:app --host 0.0.0.0 --port 8000 --reload"
  }
}
EOF
log "Wrote .vercel/project.json"

# Write vercel.json
cat > vercel.json <<EOF
{
  "version": 2,
  "builds": [
    { "src": "main.py", "use": "@vercel/python" }
  ],
  "routes": [
    { "src": "/static/(.*)", "dest": "/static/\$1" },
    { "src": "/(.*)", "dest": "/main.py" }
  ]
}
EOF
log "Wrote vercel.json"

# ── Step 6: Vercel CLI (optional) ─────────────────────────────
log "─── Step 6: Vercel CLI ───"
if command -v vercel &>/dev/null; then
    log "Vercel CLI found. Linking project..."
    vercel link --yes || warn "Vercel link failed. Run 'vercel login' first."
    log "Run 'vercel --prod' to deploy to production."
else
    warn "Vercel CLI not found. Install: npm i -g vercel"
    log "Config files are ready. Deploy manually after installing."
fi

log "════════════════════════════════════════════════════════════"
log "Deployment pipeline complete."
log "════════════════════════════════════════════════════════════"
