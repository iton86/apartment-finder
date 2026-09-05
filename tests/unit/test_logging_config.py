"""
Tests for the one place that configures logging. The level-resolution
precedence and the third-party ceiling are the parts worth pinning down:
the first is what a user hits from the CLI, and the second is a
credential-leak guard, not a tidiness preference.
"""

import logging

import pytest

from apartment_finder.interface.logging_config import (
    NOISY_LOGGERS,
    configure_logging,
    resolve_level,
    shorten_logger_name,
)


class TestShortenLoggerName:
    def test_collapses_a_deep_module_path_to_layer_and_module(self):
        assert (
            shorten_logger_name("apartment_finder.application.use_cases.scrape_and_store_listings")
            == "application.scrape_and_store_listings"
        )

    def test_keeps_two_part_names_whole(self):
        assert shorten_logger_name("apartment_finder.interface.cli") == "interface.cli"

    def test_leaves_third_party_names_untouched(self):
        # 'azure.identity' shortened to 'azure.identity' anyway, but the
        # point is that non-package names never lose their first component.
        assert shorten_logger_name("sqlalchemy.engine.Engine") == "sqlalchemy.engine.Engine"

    def test_handles_the_bare_package_name(self):
        assert shorten_logger_name("apartment_finder") == "apartment_finder"


class TestResolveLevel:
    def test_defaults_to_info(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert resolve_level() == logging.INFO

    def test_reads_log_level_env_var(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "debug")
        assert resolve_level() == logging.DEBUG

    def test_explicit_argument_beats_env_var(self, monkeypatch):
        # The --log-level flag has to win, or you can't override a
        # LOG_LEVEL baked into a container image.
        monkeypatch.setenv("LOG_LEVEL", "error")
        assert resolve_level("debug") == logging.DEBUG

    @pytest.mark.parametrize("name", ["DEBUG", "debug", " Debug "])
    def test_accepts_any_casing_and_stray_whitespace(self, name, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert resolve_level(name) == logging.DEBUG

    def test_rejects_unknown_level_by_name(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        with pytest.raises(ValueError, match="Unknown log level 'VERBOSE'"):
            resolve_level("verbose")


class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def restore_root_logger(self):
        # configure_logging uses force=True, which rips out pytest's own
        # handlers. Put them back so later tests still capture logs.
        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        yield
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    def test_applies_the_requested_level_to_our_own_loggers(self):
        configure_logging("debug")
        assert logging.getLogger("apartment_finder.anything").isEnabledFor(logging.DEBUG)

    def test_debug_does_not_reach_third_party_loggers(self):
        # azure.identity and azure.core log auth headers at DEBUG, so
        # --log-level debug must not turn them all the way up.
        configure_logging("debug")
        for name in NOISY_LOGGERS:
            assert not logging.getLogger(name).isEnabledFor(logging.DEBUG), name

    def test_third_party_loggers_still_get_quieter_at_higher_levels(self):
        # The ceiling is a ceiling, not a floor: asking for warning-only
        # output shouldn't pin them back up at INFO.
        configure_logging("error")
        for name in NOISY_LOGGERS:
            assert not logging.getLogger(name).isEnabledFor(logging.INFO), name

    def test_returns_the_level_it_applied(self):
        assert configure_logging("warning") == logging.WARNING

    def test_formats_a_record_from_a_child_logger_without_keyerror(self, capsys):
        # The format string reads %(short_name)s, which only exists because
        # the filter puts it there. If the filter were attached to a logger
        # instead of the handler, records propagating up from child loggers
        # would blow up here.
        configure_logging("info")
        logging.getLogger("apartment_finder.infrastructure.scrapers.imot_bg_scraper").info("hi")

        stderr = capsys.readouterr().err
        # The name column is padded to a fixed width, so match the parts
        # rather than the exact spacing.
        assert "infrastructure.imot_bg_scraper" in stderr
        assert stderr.rstrip().endswith("| hi")
        assert "Traceback" not in stderr
