import os
import shutil
import tempfile

# The runner builds its stores from the data directory when it is first
# imported, and opening a store can write (it folds old archive files into the
# cleared log). Point that first import at a throwaway directory so collecting
# the tests never touches the user's real notebook.
_FIRST_IMPORT_DATA_DIR = tempfile.mkdtemp(prefix="rosette-test-")
os.environ["ROSETTE_DATA_DIR"] = _FIRST_IMPORT_DATA_DIR


def pytest_unconfigure() -> None:
    shutil.rmtree(_FIRST_IMPORT_DATA_DIR, ignore_errors=True)
