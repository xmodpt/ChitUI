"""
uart/
-----
UART printer communication module for ChiTUI.

Exports:
    UARTPrinter      - serial backend for one Chitu printer
    UART_SUPPORT     - bool, True if pyserial is installed
    register_uart_routes - register all Flask API routes
"""

from uart.printer import UARTPrinter, UART_SUPPORT
from uart.routes import register_uart_routes

__all__ = ['UARTPrinter', 'UART_SUPPORT', 'register_uart_routes']
