"""실행: python -m arena   (설정은 환경변수, arena/config.py 참고)"""
import logging
import logging.handlers
import os
import sys
import traceback

from . import __version__
from .config import Config
from .web import make_server, start_sync_thread


def main():
    cfg = Config()
    print(f"[arena] {__version__} 시작: cwd={os.getcwd()} data={cfg.data_dir} port={cfg.port} "
          f"token={'설정됨' if cfg.upload_token else '없음'}", flush=True)
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(cfg.data_dir / "arena.log", maxBytes=1_048_576,
                                                             backupCount=3, encoding="utf-8"))
    except OSError as e:
        print(f"[arena] 데이터 폴더에 쓸 수 없음: {cfg.data_dir} ({e})", flush=True)
        raise
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    try:
        httpd, store = make_server(cfg)
    except Exception:
        logging.getLogger("arena").error("시작 실패:\n%s", traceback.format_exc())
        raise
    try:
        n = store.cleanup_playbacks()
        if n:
            logging.getLogger("arena").info("다시보기 녹화 리플레이 %d개 정리", n)
    except Exception:  # noqa: BLE001
        logging.getLogger("arena").exception("다시보기 녹화 정리 실패")
    try:
        n = store.backfill_player_stats()
        if n:
            logging.getLogger("arena").info("예전 리플레이 %d개에서 플레이어 지표를 채움", n)
    except Exception:  # noqa: BLE001 - 지표는 없어도 사이트는 돈다
        logging.getLogger("arena").exception("플레이어 지표 채우기 실패")
    if any(store.source(k) for k in store.sources):
        start_sync_thread(cfg, store)
    logging.getLogger("arena").info("RTS ARENA on http://%s:%d (data=%s, rakmu=%s, samchun=%s)", cfg.host, cfg.port,
                                    cfg.data_dir, cfg.rakmu_matches_file or cfg.rakmu_matches_url or "미설정",
                                    cfg.samchun_matches_file or cfg.samchun_matches_url or "미설정")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
