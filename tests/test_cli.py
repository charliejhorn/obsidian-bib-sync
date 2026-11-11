import pytest

from obsidian_bib_sync.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "obib" in capsys.readouterr().out
