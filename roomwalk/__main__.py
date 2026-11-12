"""Start the app:  python -m roomwalk"""

from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(prog="roomwalk", description="Video of a room -> walkable 3D scan")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--lan", action="store_true",
                        help="also let phones/tablets on your Wi-Fi open the app")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        raise SystemExit("Roomwalk needs Python 3.10 to 3.14 (this is %s)." % sys.version.split()[0])
    missing = []
    for module, package in (("numpy", "numpy"), ("PIL", "pillow"),
                            ("imageio_ffmpeg", "imageio-ffmpeg"), ("pycolmap", "pycolmap")):
        try:
            __import__(module)
        except Exception as exc:
            missing.append("%s (%s)" % (package, exc))
    if missing:
        raise SystemExit("These parts are missing or broken:\n  - " + "\n  - ".join(missing) +
                         "\n\nRun the start script again, or:  pip install -r requirements.txt")

    from .server import serve
    serve(port=args.port, lan=args.lan, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
