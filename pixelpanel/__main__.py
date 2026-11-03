import logging
import sys
from logging.handlers import RotatingFileHandler

from pixelpanel import config, gui


def main() -> int:
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(config.APP_DIR / "pixelpanel.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:  # no console in the windowed .exe
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("bleak").setLevel(logging.WARNING)
    return gui.run(start_hidden="--minimized" in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
