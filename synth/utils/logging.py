import argparse
import inspect
import functools
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time
from types import SimpleNamespace

import google.cloud.logging
import google.auth.exceptions

logger = logging.getLogger(__name__)

EVENTS_LEVEL_NUM = 38
DEFAULT_LOG_BACKUP_COUNT = 10
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_FILE_NAME = "bittensor.log"
LOG_FILE_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_LOGGING_DIR = str(Path.home() / ".bittensor" / "miners")

# Loggers of our own code; --logging.* sets their level. Third-party loggers
# stay at the root level (WARNING).
APP_LOGGERS = ("synth", "neurons", "__main__")

# Below DEBUG: shown with --logging.trace only.
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

_stdout_handler: logging.Handler | None = None

# Third-party loggers that emit high-volume DEBUG/INFO noise. The GCP handler
# forwards everything at DEBUG (see setup_gcp_logging), so we raise these to
# WARNING to keep them out of Cloud Logging and local stdout alike.
NOISY_LOGGERS = (
    "websockets.client",
    "sqlalchemy.engine",
    "sqlalchemy.orm.mapper.Mapper",
    "sqlalchemy.orm.strategies.LazyLoader",
    "sqlalchemy.orm.relationships.RelationshipProperty",
    "sqlalchemy.orm.path_registry",
    "sqlalchemy.pool.impl.QueuePool",
    "bittensor",
    "urllib3.connectionpool",
)


def silence_noisy_loggers():
    """Raise high-volume third-party loggers (NOISY_LOGGERS) to WARNING.

    Idempotent; call from each entry point that may run without the others
    (GCP log setup, DB engine creation) so suppression holds in every context.
    """
    for noisy_logger in NOISY_LOGGERS:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def add_logging_args(parser: argparse.ArgumentParser):
    """The --logging.* flags, with bittensor 10's names and defaults."""
    parser.add_argument(
        "--logging.debug",
        action="store_true",
        help="Turn on debug logging.",
        default=bool(os.getenv("BT_LOGGING_DEBUG")),
    )
    parser.add_argument(
        "--logging.trace",
        action="store_true",
        help="Turn on trace logging (more verbose than debug).",
        default=bool(os.getenv("BT_LOGGING_TRACE")),
    )
    parser.add_argument(
        "--logging.info",
        action="store_true",
        help="Turn on info logging.",
        default=bool(os.getenv("BT_LOGGING_INFO")),
    )
    parser.add_argument(
        "--logging.record_log",
        action="store_true",
        help="Turns on logging to file.",
        default=bool(os.getenv("BT_LOGGING_RECORD_LOG")),
    )
    parser.add_argument(
        "--logging.logging_dir",
        type=str,
        help="Logging default root directory.",
        default=os.getenv("BT_LOGGING_LOGGING_DIR") or DEFAULT_LOGGING_DIR,
    )


def setup_logging(logging_config):
    """Send records to stdout, and to <logging_dir>/bittensor.log with
    --logging.record_log, at the level the --logging.* flags select
    (WARNING by default)."""
    global _stdout_handler

    if logging_config.trace:
        level = TRACE
    elif logging_config.debug:
        level = logging.DEBUG
    elif logging_config.info:
        level = logging.INFO
    else:
        level = logging.WARNING

    formatter = logging.Formatter(LOG_FORMAT)
    root = logging.getLogger()
    if _stdout_handler is None:
        _stdout_handler = logging.StreamHandler(sys.stdout)
        _stdout_handler.setFormatter(formatter)
        root.addHandler(_stdout_handler)

    if logging_config.record_log and logging_config.logging_dir:
        logging_dir = os.path.expanduser(logging_config.logging_dir)
        os.makedirs(logging_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(logging_dir, LOG_FILE_NAME),
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=DEFAULT_LOG_BACKUP_COUNT,
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    for name in APP_LOGGERS:
        logging.getLogger(name).setLevel(level)


def setup_logging_from_argv():
    """setup_logging from the --logging.* flags in sys.argv, for worker
    processes, which inherit the parent's argv but not its handlers."""
    parser = argparse.ArgumentParser(add_help=False)
    add_logging_args(parser)
    args, _ = parser.parse_known_args()
    setup_logging(
        SimpleNamespace(
            **{
                key.removeprefix("logging."): value
                for key, value in vars(args).items()
            }
        )
    )


def setup_events_logger(full_path, events_retention_size):
    logging.addLevelName(EVENTS_LEVEL_NUM, "EVENT")

    logger = logging.getLogger("event")
    logger.setLevel(EVENTS_LEVEL_NUM)

    def event(self, message, *args, **kws):
        if self.isEnabledFor(EVENTS_LEVEL_NUM):
            self._log(EVENTS_LEVEL_NUM, message, args, **kws)

    logging.Logger.event = event

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        os.path.join(full_path, "events.log"),
        maxBytes=events_retention_size,
        backupCount=DEFAULT_LOG_BACKUP_COUNT,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(EVENTS_LEVEL_NUM)
    logger.addHandler(file_handler)

    return logger


class WandBHandler(logging.Handler):
    def __init__(self, wandb_run):
        super().__init__()
        self.wandb_run = wandb_run

    def emit(self, record):
        try:
            log_entry = self.format(record)
            if record.levelno >= 40:
                self.wandb_run.alert(
                    title="An error occurred",
                    text=log_entry,
                    level=record.levelname,
                )
        except Exception as err:
            filter = "will be ignored. Please make sure that you are using an active run"
            msg = f"Error occurred while sending alert to wandb: ---{str(err)}--- the message: ---{log_entry}---"
            if filter not in str(err):
                logger.log(TRACE, msg)
            else:
                logger.warning(msg)


def setup_wandb_alert(wandb_run):
    """Miners can use this to send alerts to wandb."""
    wandb_handler = WandBHandler(wandb_run)
    wandb_handler.setLevel(logging.ERROR)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    wandb_handler.setFormatter(formatter)

    return wandb_handler


def setup_gcp_logging(
    log_id_prefix: str | None, cycle_label: str | None = None
) -> tuple[
    logging.Handler | None,
    google.cloud.logging.Client | None,
]:
    """
    Sets up GCP logging and returns the handler and client for manual flushing/closing.
    Call close_gcp_logging(handler, client) before shutdown to avoid losing logs.
    """
    log_id = f"{log_id_prefix}-synth-validator"
    logger.info(
        f"setting up GCP log forwarder with log_id: {log_id} and cycle_label: {cycle_label}"
    )
    handler = None
    client = None
    try:
        client = google.cloud.logging.Client()
    except google.auth.exceptions.GoogleAuthError as e:
        logger.warning(
            f"log forwarder - Failed to set up GCP logging. GoogleAuthError: {e}",
        )
    else:
        if log_id_prefix is None:
            logger.warning(
                "log_id_prefix is None. GCP logging will not be set up."
            )
        else:
            labels = {"log_id": log_id}
            if cycle_label is not None:
                labels["cycle_label"] = cycle_label
            silence_noisy_loggers()
            client.setup_logging(log_level=logging.DEBUG, labels=labels)
            handler = next(
                (
                    h
                    for h in logging.getLogger().handlers
                    if isinstance(
                        h,
                        (
                            google.cloud.logging.handlers.CloudLoggingHandler,
                            google.cloud.logging.handlers.StructuredLogHandler,
                        ),
                    )
                ),
                None,
            )

            # Only suppress stdout logging once we have confirmed a GCP handler is installed,
            # otherwise we risk dropping logs entirely.
            if handler is not None and _stdout_handler is not None:
                _stdout_handler.setLevel(logging.CRITICAL + 1)

    return handler, client


def close_gcp_logging(handler, client):
    """
    Flushes and closes the GCP logging handler and client to ensure all logs are sent.
    Call this before process shutdown.
    """
    if handler is not None:
        try:
            handler.flush()
        except Exception as e:
            logger.warning(f"Error flushing GCP log handler: {e}")
        try:
            handler.close()
        except Exception as e:
            logger.warning(f"Error closing GCP log handler: {e}")
    if client is not None:
        try:
            client.close()
        except Exception as e:
            logger.warning(f"Error closing GCP log client: {e}")


def print_execution_time(func):
    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        start = time.time()
        result = await func(*args, **kwargs)
        end = time.time()
        logger.info(
            f"Execution time for {func.__name__}: {end - start:.4f} seconds"
        )
        return result

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        logger.info(
            f"Execution time for {func.__name__}: {end - start:.4f} seconds"
        )
        return result

    if inspect.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper
