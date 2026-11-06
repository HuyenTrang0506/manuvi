import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PDF = os.path.expandvars("$HOME/mnt/Project/translator-electronic/data/382014584-sk70sr-1es-shop-manual.pdf")
OUT = Path(__file__).resolve().parents[1] / "workspace" / "spikes"
OUT.mkdir(parents=True, exist_ok=True)
