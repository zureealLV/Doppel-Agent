import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path.cwd()))
if __name__ == "__main__":
    unittest.main(module="test_public")
