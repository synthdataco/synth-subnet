import io
import json
import logging

import bittensor  # noqa: F401  registers the TRACE and SUCCESS levels
from google.cloud.logging.handlers import StructuredLogHandler

from synth.utils.logging import (
    EVENTS_LEVEL_NUM,
    gcp_severity,
    use_gcp_severities,
)


def emit(level: int) -> tuple[dict, logging.LogRecord]:
    stream = io.StringIO()
    handler = StructuredLogHandler(stream=stream)
    use_gcp_severities(handler)
    record = logging.LogRecord("test", level, __file__, 1, "msg", None, None)
    handler.handle(record)
    # The first entry a handler writes is the library's diagnostic one.
    return json.loads(stream.getvalue().splitlines()[-1]), record


def test_custom_levels_get_a_gcp_severity():
    logging.addLevelName(EVENTS_LEVEL_NUM, "EVENT")
    assert (
        emit(logging.getLevelNamesMapping()["TRACE"])[0]["severity"] == "DEBUG"
    )
    assert (
        emit(logging.getLevelNamesMapping()["SUCCESS"])[0]["severity"]
        == "NOTICE"
    )
    assert emit(EVENTS_LEVEL_NUM)[0]["severity"] == "WARNING"


def test_standard_levels_keep_their_name():
    for level in (
        logging.DEBUG,
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL,
    ):
        assert emit(level)[0]["severity"] == logging.getLevelName(level)


def test_record_keeps_its_level_name_for_other_handlers():
    _, record = emit(logging.getLevelNamesMapping()["SUCCESS"])
    assert record.levelname == "SUCCESS"


def test_gcp_severity_bands():
    assert gcp_severity(5) == "DEBUG"
    assert gcp_severity(logging.INFO) == "INFO"
    assert gcp_severity(21) == "NOTICE"
    assert gcp_severity(38) == "WARNING"
    assert gcp_severity(45) == "ERROR"
    assert gcp_severity(60) == "CRITICAL"
