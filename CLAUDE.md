# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PrinterBridge is a TCP-to-USB ESC/POS printer bridge written in Python. It allows applications to connect to USB thermal printers via TCP socket (port 9100) by bridging TCP connections to USB communication.

## Key Commands

### Development

- `uv sync` - Install dependencies and sync virtual environment
- `uv run printerbridge --vid <hex_vid> --pid <hex_pid>` - Run the application with USB vendor/product IDs
- `uv run printerbridge --help` - Show command-line options

### Docker

- `docker build -t printerbridge .` - Build Docker image
- `docker run --privileged -p 9100:9100 printerbridge --vid <hex_vid> --pid <hex_pid>` - Run in container (requires privileged mode for USB access)

### Installation

- `uv build` - Build distribution packages
- `pip install .` - Install the package locally

## Architecture

### Core Components

1. **USBPrinter** (`src/printerbridge/__init__.py:27-105`) - Handles direct USB communication with ESC/POS printers using pyusb
2. **TCPPrinterBridge** (`src/printerbridge/__init__.py:107-198`) - TCP server that accepts connections on port 9100 and forwards data to USB printer
3. **Main Entry Point** (`src/printerbridge/__init__.py:212-268`) - Command-line interface and signal handling

### Data Flow

1. TCP client connects to bridge on port 9100
2. Bridge forwards all received data directly to USB printer via bulk transfer
3. Any printer responses are sent back to TCP client
4. Connection timeout and cleanup handled automatically

### Key Technical Details

- Uses Python 3.13+ with pyusb for USB communication
- Single-threaded design with socket timeouts for non-blocking operation
- ESC/POS printer initialization on startup (`\x1b\x40`)
- Supports both input and output USB endpoints
- Graceful shutdown with SIGINT/SIGTERM signal handling
- Kubernetes deployment with non-root user and USB device access
- Fail and restart strategy for error handling, relying on Kubernetes to restart the pod

### Configuration

- Vendor ID (--vid) and Product ID (--pid) required as hex values to identify USB printer
- TCP host/port configurable (defaults to 0.0.0.0:9100)
- Connection timeout configurable (default 3 seconds)
- Debug logging available with --debug flag

## Testing USB Printers

To find USB printer IDs: `lsusb` and look for printer device, then use hex values like `--vid 04b8 --pid 0202`
