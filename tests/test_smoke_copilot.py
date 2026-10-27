"""Live smoke commands require an explicitly selected disposable Video."""
from __future__ import annotations

import pytest

from scripts import smoke_copilot


def test_project_is_required_before_any_request(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["smoke_copilot", "--url", "http://studio.test"])
    monkeypatch.setattr(smoke_copilot, "_post_turn", lambda *args: pytest.fail("must not send a turn"))
    with pytest.raises(SystemExit) as error:
        smoke_copilot.main()
    assert error.value.code == 2
    assert "--project" in capsys.readouterr().err
