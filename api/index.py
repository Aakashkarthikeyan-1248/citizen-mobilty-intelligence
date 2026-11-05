"""
Vercel Serverless Function entry point.
Exposes the FastAPI application instance for Vercel's Python runtime.
"""
import sys
from pathlib import Path

# Add project root to sys.path so modules (config, engines, api) resolve properly in AWS Lambda
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from main import app
