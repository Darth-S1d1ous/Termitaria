#!/usr/bin/env python3
"""Run the Kuzu graph memory and the Poincaré memory on the same LoCoMo slice."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from memcompare.runner import main

if __name__ == "__main__":
    main()
