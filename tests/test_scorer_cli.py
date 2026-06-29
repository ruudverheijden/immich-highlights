import pytest

from src.scorer import parse_args


def test_parse_args_accepts_force_rescore():
    """The explicit maintenance flag should be available to users."""
    args = parse_args(["--force-rescore"])

    assert args.force_rescore is True


def test_parse_args_rejects_unknown_parameters():
    """Unknown CLI parameters should fail instead of being silently ignored."""
    with pytest.raises(SystemExit) as exc:
        parse_args(["--unexpected"])

    assert exc.value.code == 2


def test_parse_args_rejects_abbreviated_parameters():
    """Partial long-option matches should fail to avoid accidental rescans."""
    with pytest.raises(SystemExit) as exc:
        parse_args(["--force"])

    assert exc.value.code == 2
