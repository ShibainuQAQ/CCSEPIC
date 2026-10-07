# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Cross-tool constants: USB identity, ROM defaults, and serial port naming.

Other constants live in next to the code that uses them.
"""

__all__ = [
    'USB_JTAG_SERIAL_PID',
    'ESPRESSIF_VID',
    'ESP_ROM_BAUD',
    'HARDWARE_FLOW_CONTROL_VID_PIDS',
    'MACOS_PORT_EXCLUDE_LIST',
    'LINUX_DEVICE_PATTERNS',
    'MACOS_DEVICE_PATTERNS',
]

# --- Hardware identifiers ---
ESPRESSIF_VID = 0x303A
"""Espressif USB Vendor ID"""

USB_JTAG_SERIAL_PID = 0x1001
"""Known Espressif Product ID for JTAG serial ports"""

ESP_ROM_BAUD = 115200
"""Default serial baud rate for ROM bootloader communication"""

HARDWARE_FLOW_CONTROL_VID_PIDS = (
    (0x10C4, 0xEA64),  # SiLabs CP2102C USB to UART Bridge Controller
)
"""USB ``(VID, PID)`` pairs for adapters with always-on hardware flow control.

CP2102C-class bridges tie their CTS line to the chip's RTS, so the standard
reset sequences need to skip trailing DTR writes (which would loop back as
RTS and glitch ``EN``) and clear ``HUPCL`` before close. The reset helpers
in `esp_pylib.serial_reset` take a ``flow_control=True`` flag for that
mode; this list drives the detection.
"""

# --- Serial port discovery (sorting / filtering) ---
MACOS_PORT_EXCLUDE_LIST = ('Bluetooth-Incoming-Port', 'wlan-debug', 'cu.debug-console')
"""macOS virtual ports to exclude (not real serial devices)"""

LINUX_DEVICE_PATTERNS = ('ttyUSB', 'ttyACM')
"""Linux device name patterns (used for sorting priority; order is significant)."""

MACOS_DEVICE_PATTERNS = ('usbserial', 'usbmodem')
"""macOS device name patterns (used for sorting priority; order is significant)."""
