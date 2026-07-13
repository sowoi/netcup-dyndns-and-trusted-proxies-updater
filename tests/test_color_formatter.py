import logging

from src.updateDynDns import ColorFormatter, TqdmLoggingHandler


def _format(level, message):
    formatter = ColorFormatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    record = logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    return formatter.format(record)


def test_error_messages_are_colored_red():
    formatted = _format(logging.ERROR, "something went wrong")

    assert formatted.startswith(ColorFormatter.RED)
    assert formatted.endswith(ColorFormatter.RESET)
    assert "something went wrong" in formatted


def test_info_messages_are_not_colored():
    formatted = _format(logging.INFO, "all good")

    assert ColorFormatter.RED not in formatted
    assert ColorFormatter.RESET not in formatted
    assert "all good" in formatted


def test_tqdm_logging_handler_writes_via_tqdm(mocker):
    """The handler must route messages through tqdm.write to avoid corrupting the bar."""
    tqdm_write_mock = mocker.patch("src.updateDynDns.tqdm.write")
    handler = TqdmLoggingHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test_tqdm_handler")
    logger.handlers = [handler]
    logger.propagate = False

    logger.error("boom")

    tqdm_write_mock.assert_called_once_with("boom")
