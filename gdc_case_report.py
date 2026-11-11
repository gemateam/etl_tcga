#!/usr/bin/env python3
"""Entrada compatible para generar un reporte complementario de un caso GDC."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "etl" / "src"))

if __name__ == "__main__":
    raise SystemExit(import_module("tcga.case_report").main())