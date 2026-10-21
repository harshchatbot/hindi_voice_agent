import argparse

from techfi_shared.logging import Event, configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Local TechFi voice worker")
    parser.add_argument(
        "--check-config", action="store_true", help="Validate startup offline and exit"
    )
    args = parser.parse_args()
    from loguru import logger as dependency_logger

    dependency_logger.remove()
    logger = configure_logging()
    development = False
    try:
        from techfi_voice.config import StartupSettings, VoiceSettings

        # Bootstrap before runtime imports so their failures also get development diagnostics.
        development = StartupSettings().environment == "development"
        import uvicorn

        from techfi_voice.integrations.providers import session_profile
        from techfi_voice.server import create_app

        settings = VoiceSettings()
        app = create_app(settings)
        logger.info(Event.WORKER_CONFIGURATION, extra=session_profile(settings))
        if args.check_config:
            return
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            access_log=False,
            log_config=None,
            log_level="critical",
        )
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logger.error(Event.WORKER_FAILED)
        if development:
            from techfi_voice.diagnostics import development_traceback

            development_traceback(exc)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
