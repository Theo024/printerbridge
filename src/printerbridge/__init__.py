import argparse
import logging
import signal
import socket
import sys
from typing import Optional

try:
    import usb.core
    import usb.util
except ImportError:
    print("Error: pyusb not installed. Run: pip install pyusb")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("printerbridge")


class USBPrinterError(Exception):
    """Custom exception for USB printer errors."""

    pass


class USBPrinter:
    """Handles USB communication with printer."""

    def __init__(self, vendor_id: int, product_id: int):
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.device = None
        self.endpoint_out = None
        self.endpoint_in = None

    def connect(self) -> None:
        """Connect to the USB printer."""
        self.device = usb.core.find(idVendor=self.vendor_id, idProduct=self.product_id)  # type: ignore
        if self.device is None:
            logger.error(
                f"Printer not found (VID: 0x{self.vendor_id:04x}, PID: 0x{self.product_id:04x})"
            )
            raise USBPrinterError(
                f"Printer not found (VID: 0x{self.vendor_id:04x}, PID: 0x{self.product_id:04x})"
            )

        # Set configuration (ignore errors if already configured)
        try:
            self.device.set_configuration()
        except usb.core.USBError as e:
            logger.debug(f"USBError during set_configuration: {e}")

        # Find endpoints
        cfg = self.device.get_active_configuration()
        self.endpoint_out = None
        self.endpoint_in = None
        for intf in cfg:
            for ep in intf:
                if (
                    usb.util.endpoint_direction(ep.bEndpointAddress)
                    == usb.util.ENDPOINT_OUT
                ):
                    self.endpoint_out = ep
                elif (
                    usb.util.endpoint_direction(ep.bEndpointAddress)
                    == usb.util.ENDPOINT_IN
                ):
                    self.endpoint_in = ep
        if not self.endpoint_out or not self.endpoint_in:
            logger.error("Could not find endpoint")
            raise USBPrinterError("Could not find endpoint")

        logger.info(
            f"Connected to printer (VID: 0x{self.vendor_id:04x}, PID: 0x{self.product_id:04x})"
        )

    def write(self, data: bytes) -> None:
        """Write data to the printer. Returns True if successful."""
        chunk_size = self.endpoint_out.wMaxPacketSize
        for i in range(0, len(data), chunk_size):
            chunk = data[i : i + chunk_size]
            self.endpoint_out.write(chunk)

    def read(self, size: int = 64, timeout: int = 100) -> Optional[bytes]:
        """Read data from the printer (if supported)."""
        try:
            data = self.endpoint_in.read(size, timeout)
            return bytes(data)
        except usb.core.USBError:
            return None  # Timeout is expected

    def disconnect(self) -> None:
        """Disconnect from the printer."""
        if self.device:
            try:
                usb.util.dispose_resources(self.device)
                logger.info("Disconnected from printer")
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")
            finally:
                self.device = None
                self.endpoint_out = None
                self.endpoint_in = None


class TCPPrinterBridge:
    """TCP server that bridges connections to USB ESC/POS printer"""

    def __init__(
        self,
        printer: USBPrinter,
        host: str = "0.0.0.0",
        port: int = 9100,
        timeout: int = 3,
    ):
        self.printer = printer
        self.host = host
        self.port = port
        self.timeout = timeout
        self.server_socket = None
        self.running = False

    def start(self) -> None:
        """Start the TCP server"""
        logger.info(f"Starting TCP-to-USB ESC/POS bridge on port {self.port}")

        self.printer.connect()
        self.printer.write(b"\x1b\x40")  # Initialize printer (ESC @)

        try:
            self.server_socket = socket.create_server((self.host, self.port))
            self.server_socket.settimeout(1.0)
            self.running = True
            logger.info(f"Server listening on port {self.port}")

            while self.running:
                try:
                    client_socket, client_address = self.server_socket.accept()
                except socket.timeout:
                    continue

                logger.info(f"Client connected from {client_address}")
                with client_socket:
                    client_socket.settimeout(self.timeout)
                    self.handle_client(client_socket)
        except Exception as e:
            logger.error(f"Failed to start server: {type(e).__name__}: {e}")
            raise
        finally:
            self.cleanup()

    def handle_client(self, client_socket: socket.socket) -> None:
        """Handle individual client connection"""
        try:
            while self.running:
                try:
                    data = client_socket.recv(8192)
                except socket.timeout:
                    logger.info("Client connection timed out (recv)")
                    break
                except socket.error as e:
                    logger.info(f"Client disconnected (recv): {e}")
                    break
                if not data:
                    logger.info("Client sent no data, closing connection.")
                    break

                logger.debug(f"Received {len(data)} bytes from client")

                # Send to printer
                self.printer.write(data)
                response = self.printer.read(500)
                if response:
                    client_socket.send(response)
                    logger.debug(f"Sent {len(response)} bytes response to client")

        except Exception as e:
            logger.error(f"Client handling error: {type(e).__name__}: {e}")
            raise

    def stop(self):
        """Stop the server"""
        logger.info("Stopping server...")
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
                self.server_socket = None
            except Exception as e:
                logger.error(f"Error closing server socket: {e}")

    def cleanup(self):
        """Clean up resources"""
        self.stop()
        self.printer.disconnect()
        logger.info("Server stopped")


def signal_handler(signum, frame, bridge):
    """Handle shutdown signals"""
    logging.info(f"Received signal {signum}, shutting down...")
    bridge.cleanup()
    sys.exit(0)


def parse_hex(value: str) -> int:
    """Parse hexadecimal string to integer."""
    return int(value, 16)


def main():
    """Main application entry point"""
    parser = argparse.ArgumentParser(description="TCP-to-USB ESC/POS Printer Bridge")
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to listen on (default: 0.0.0.0)",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=9100,
        help="TCP port to listen on (default: 9100)",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=3,
        help="Connection timeout in seconds (default: 3)",
    )
    parser.add_argument(
        "--vid", type=parse_hex, required=True, help="USB Vendor ID (hex)"
    )
    parser.add_argument(
        "--pid", type=parse_hex, required=True, help="USB Product ID (hex)"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Set debug logging if requested
    if args.debug:
        logger.setLevel(logging.DEBUG)

    # Create bridge
    printer = USBPrinter(args.vid, args.pid)
    bridge = TCPPrinterBridge(printer, args.host, args.port, args.timeout)

    # Setup signal handlers
    signal.signal(signal.SIGINT, lambda s, f: signal_handler(s, f, bridge))
    signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s, f, bridge))

    # Start server
    try:
        bridge.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
