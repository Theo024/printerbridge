import argparse
import socket
import sys

ESC_POS_RESET = b"\x1b\x40"  # ESC @
TIMEOUT = 2  # seconds


def main():
    parser = argparse.ArgumentParser(description="Printerbridge healthcheck")
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Printerbridge server hostname (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9100,
        help="Printerbridge server port (default: 9100)",
    )
    args = parser.parse_args()

    try:
        with socket.create_connection((args.host, args.port), timeout=TIMEOUT) as s:
            s.sendall(ESC_POS_RESET)
        sys.exit(0)
    except Exception as e:
        print(f"Healthcheck failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
