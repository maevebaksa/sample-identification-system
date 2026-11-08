"""Start the Sample Identification System:  python run.py [--config config.json]"""
import argparse
import logging
import threading

from sis import config
from sis.app import create_app


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", help="path to config.json (default: ./config.json)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load(args.config)
    app = create_app(cfg)
    threading.Thread(target=app.svc.poll_forever, daemon=True).start()
    logging.info("Data dir: %s | Moonraker: %s | UI: http://%s:%s",
                 cfg["data_dir"], cfg["moonraker_url"], cfg["host"], cfg["port"])
    from waitress import serve
    serve(app, host=cfg["host"], port=cfg["port"], threads=6)


if __name__ == "__main__":
    main()
