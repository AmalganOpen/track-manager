"""Suite-wide isolation from the developer's real Rekordbox install.

Two production paths reach outside the sandbox: the process check reads
the real process table, and the resync writes to the real
``~/Library/Pioneer/rekordbox/master.db``. Both are neutralised by
default so that a test run can never depend on whether the developer
happens to have Rekordbox open, and can never mutate their actual
collection. Tests exercising either path override these themselves.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_rekordbox(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    from track_manager import rekordbox_db as tm_rb

    monkeypatch.setattr(
        tm_rb,
        "MASTER_DB_PATH",
        tmp_path_factory.mktemp("no-rekordbox") / "master.db",
    )
    if request.node.get_closest_marker("real_process_check"):
        return
    monkeypatch.setattr(tm_rb, "running_rekordbox_processes", lambda: [])
