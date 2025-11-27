import click
from click.testing import CliRunner
import pytest
from bbblb.cli import main
from bbblb.settings import BBBLBConfig


@pytest.fixture(scope="function")
def runner(config: BBBLBConfig):
    yield CliRunner(
        env={
            f"BBBLB_{name}": str(getattr(config, name))
            for name in ("DB", "SECRET", "PATH_DATA", "DOMAIN")
        }
    )


def test_cli_server(runner: CliRunner):
    result = runner.invoke(
        main, ["server", "create", "--secret", "1234", "test.example.com"]
    )
    assert result.exit_code == 0
    result = runner.invoke(main, ["server", "list"])
    assert result.exit_code == 0
    assert result.stdout == "test.example.com 1234\n"
