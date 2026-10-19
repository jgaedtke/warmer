import os
import sys
import tempfile
from pathlib import Path

# Point the app at a throwaway database before it's imported.
os.environ["WARMER_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
