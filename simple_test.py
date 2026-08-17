from __future__ import annotations

import sys


def main() -> int:
    try:
        import requests
    except ModuleNotFoundError:
        print("requests import: failed", file=sys.stderr)
        print(f"interpreter: {sys.executable}", file=sys.stderr)
        print(
            "Install project dependencies with: "
            f"{sys.executable} -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    print("requests import: ok")
    print(f"interpreter: {sys.executable}")
    print(f"requests version: {requests.__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
