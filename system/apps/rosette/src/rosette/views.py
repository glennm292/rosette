"""Running the views Rosette publishes, with the engine that publishes them.

The review page shows the result of executing the saved DACTAL queries in ``datasets`` --
through the real DACTAL engine, the same one the workspace's Data app runs -- rather than
working the same thing out again in Python. That is the point of the arrangement: what the
page shows and what "see the query" opens cannot disagree, because they are one query over
one set of rows.

The rows come from ``datasets.build_datasets``, cached on disk beside the notebook and
rewritten only when the notebook has actually changed. The engine runs in a short-lived
``node`` process (``run_query.cjs``); results are cached per query against the data's content
hash, so a page load after a quiet minute costs nothing.
"""

import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

from rosette.datasets import build_datasets
from rosette.notebook import DATA_DIR
from rosette.tutor import RosetteError

ENGINE = Path(__file__).parent / "assets" / "dactal_engine.js"
RUN_QUERY = Path(__file__).parent / "run_query.cjs"
# The shaped rows, as the engine reads them. A cache of what ``datasets`` produces, not a
# second copy of the notebook: it is rewritten from the notebook whenever the notebook moves.
VIEW_DATA_FILENAME = "view_data.json"
# A query over a few hundred lines is milliseconds; this is the ceiling for node starting up
# and the engine parsing itself on a machine under load.
QUERY_TIMEOUT_SECONDS = 30.0


class ViewError(RosetteError):
    """A view could not be worked out, so the page has nothing honest to show."""


def view_data_path(data_dir: Path = DATA_DIR) -> Path:
    return data_dir / VIEW_DATA_FILENAME


def refresh_view_data(data_dir: Path = DATA_DIR) -> str:
    """Re-shape the notebook for the engine; returns the content version of the result.

    Written only when the content changed, so the version is a faithful data version: the
    query cache below is keyed on it, and a notebook nobody has touched re-runs nothing.
    """
    item = build_datasets(data_dir)["datasets"]["rosette"]["item"]
    body = json.dumps(
        {"export": "workspace", "data": {"rosette": item}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    path = view_data_path(data_dir)
    try:
        unchanged = path.read_text(encoding="utf-8") == body
    except OSError:
        unchanged = False
    if not unchanged:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(body, encoding="utf-8")
        temp_path.replace(path)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


@lru_cache(maxsize=64)
def _ran(query: str, path: str, version: str) -> str:
    """One query's result as JSON text. ``version`` is in the key, not the call: it is what
    makes a changed notebook miss the cache."""
    try:
        done = subprocess.run(
            ["node", str(RUN_QUERY), "--engine", str(ENGINE), "--export", path,
             "--query", query],
            capture_output=True,
            text=True,
            timeout=QUERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ViewError(f"Could not run the view: {error}") from error
    if done.returncode != 0:
        raise ViewError(f"Could not run the view: {done.stderr.strip() or 'no reason given'}")
    return done.stdout


def run_view(query: str, data_dir: Path = DATA_DIR) -> list:
    """The rows this query returns, read back from the engine."""
    version = refresh_view_data(data_dir)
    try:
        return json.loads(_ran(query, str(view_data_path(data_dir)), version))
    except ValueError as error:
        raise ViewError(f"The view came back unreadable: {error}") from error


# The app whose query page these views can be opened in: the workspace's Data app, which
# reads the same rows out of ``datasets`` and so runs the same query over the same lines.
QUERY_APP = "workspace-data"
WORKSPACE_URL = os.environ.get("ROSETTE_SHELL_URL", "http://127.0.0.1:8000")


def open_in_workspace(app_name: str, path: str, client: str) -> int:
    """Ask the workspace shell to open (or focus) a window of ``app_name`` at ``path``."""
    args = {"app": app_name, "path": path}
    if client:
        args["client"] = client
    call = urllib.request.Request(
        f"{WORKSPACE_URL}/api/layout/broadcast",
        data=json.dumps({"op": "open", "args": args, "requester": None}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(call, timeout=10) as answer:
            return int(answer.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except urllib.error.URLError:
        return 502
