from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path.cwd()))
unittest.main(module="test_public")
