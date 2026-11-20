"""실행: python -m arena   (설정은 환경변수, arena/config.py 참고)"""
import logging
import logging.handlers
import sys

from .config import Config
from .web import make_server, start_sync_thread


def main():
    cfg = Config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout),
                logging.handlers.RotatingFileHandler(cfg.data_dir / "arena.log", maxBytes=1_048_576,
                                                     backupCount=3, encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    httpd, store = make_server(cfg)
    if store.rakmu.configured:
        start_sync_thread(cfg, store)
    logging.getLogger("arena").info("RTS ARENA on http://%s:%d (data=%s, rakmu=%s)", cfg.host, cfg.port, cfg.data_dir,
                                    cfg.rakmu_matches_file or cfg.rakmu_matches_url or "미설정")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
