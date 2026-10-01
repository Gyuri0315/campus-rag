"""Stable paths shared by tests regardless of their subpackage."""

from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TEST_ROOT.parents[1]
FIXTURES_ROOT = TEST_ROOT / "fixtures"
