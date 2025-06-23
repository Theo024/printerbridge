import argparse
import asyncio
import logging
import signal
import sys
from contextlib import contextmanager
from typing import Optional

import usb.core
import usb.util

# Configure logging
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

    @contextmanager
    def connection(self):
        """Context manager for printer connection."""
        try:
            self.connect()
            yield self
        finally:
            self.disconnect()

    def connect(self) -> None:
        """Connect to the USB printer."""
        # Find the printer
        self.device = usb.core.find(idVendor=self.vendor_id, idProduct=self.product_id)
        if not self.device:
            raise USBPrinterError(
                f"Printer not found (VID: 0x{self.vendor_id:04x}, "
                f"PID: 0x{self.product_id:04x})"
            )

        # Detach kernel driver if necessary
        if self.device.is_kernel_driver_active(0):
            try:
                self.device.detach_kernel_driver(0)
                logger.info("Detached kernel driver")
            except usb.core.USBError as e:
                raise USBPrinterError(f"Could not detach kernel driver: {e}")

        # Set configuration (ignore errors if already configured)
        try:
            self.device.set_configuration()
        except usb.core.USBError:
            pass

        # Find endpoints
        cfg = self.device.get_active_configuration()
        intf = cfg[(0, 0)]

        self.endpoint_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_OUT,
        )

        if not self.endpoint_out:
            raise USBPrinterError("Could not find output endpoint")

        self.endpoint_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_IN,
        )

        logger.info(
            f"Connected to printer (VID: 0x{self.vendor_id:04x}, "
            f"PID: 0x{self.product_id:04x})"
        )

    def write(self, data: bytes) -> None:
        """Write data to the printer."""
        if not self.endpoint_out:
            raise USBPrinterError("Not connected to printer")

        try:
            # Write data in chunks
            chunk_size = self.endpoint_out.wMaxPacketSize
            for i in range(0, len(data), chunk_size):
                chunk = data[i : i + chunk_size]
                self.endpoint_out.write(chunk)
        except usb.core.USBError as e:
            raise USBPrinterError(f"USB write error: {e}")

    def read(self, size: int = 64, timeout: int = 100) -> Optional[bytes]:
        """Read data from the printer (if supported)."""
        if not self.endpoint_in:
            return None

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


class PrinterBridge:
    """TCP server that bridges network connections to USB printer."""

    def __init__(self, printer: USBPrinter, host: str = "0.0.0.0", port: int = 9100):
        self.printer = printer
        self.host = host
        self.port = port
        self.server = None
        self._shutdown_event = asyncio.Event()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a client connection."""
        client_addr = writer.get_extra_info("peername")
        logger.info(f"Client connected from {client_addr}")

        try:
            while not reader.at_eof():
                # Read data from client
                data = await reader.read(4096)
                if not data:
                    break

                logger.debug(f"Received {len(data)} bytes from client")

                # Forward to printer
                try:
                    self.printer.write(data)
                    logger.debug("Data sent to printer successfully")

                    # Try to read response from printer
                    if response := self.printer.read(timeout=50):
                        logger.debug(f"Received {len(response)} bytes from printer")
                        writer.write(response)
                        await writer.drain()
                except USBPrinterError as e:
                    logger.error(f"Printer error: {e}")
                    break

        except Exception as e:
            logger.error(f"Error handling client: {e}")
        finally:
            logger.info(f"Client {client_addr} disconnected")
            writer.close()
            await writer.wait_closed()

    async def start(self) -> None:
        """Start the TCP server."""
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )

        addr = self.server.sockets[0].getsockname()
        logger.info(f"Printer bridge listening on {addr[0]}:{addr[1]}")

        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown_event.set)

        async with self.server:
            # Wait for shutdown signal
            await self._shutdown_event.wait()
            logger.info("Shutting down server...")


def parse_hex(value: str) -> int:
    """Parse hexadecimal string to integer."""
    return int(value, 16)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="USB Network Bridge")
    parser.add_argument(
        "--vid", type=parse_hex, required=True, help="USB Vendor ID (hex)"
    )
    parser.add_argument(
        "--pid", type=parse_hex, required=True, help="USB Product ID (hex)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to listen on (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=9100, help="Port to listen on (default: 9100)"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Set debug logging if requested
    if args.debug:
        logger.setLevel(logging.DEBUG)

    # Create and run the bridge
    printer = USBPrinter(args.vid, args.pid)

    try:
        with printer.connection():
            bridge = PrinterBridge(printer, args.host, args.port)
            asyncio.run(bridge.start())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except USBPrinterError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
