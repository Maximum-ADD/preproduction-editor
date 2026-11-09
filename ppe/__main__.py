"""Start the Pre-production Editor: `python -m ppe`."""
import argparse
import logging
import threading
import webbrowser

from .server import app


def main():
    p = argparse.ArgumentParser(prog="ppe", description="Pre-production Editor")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = p.parse_args()

    url = f"http://127.0.0.1:{args.port}/"
    print(f"Pre-production Editor running at {url}  (Ctrl+C to stop)")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
