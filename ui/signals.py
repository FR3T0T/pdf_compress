"""Qt signal bridge for thread-safe communication."""

from PySide6.QtCore import Signal, QObject


class Signals(QObject):
    progress = Signal(int, int, int, str)       # (file_index, cur, total, status)
    file_done = Signal(int, object)             # (file_index, result/error)
    all_done = Signal(float)                    # (elapsed_seconds)
    analysis_done = Signal(list)                # list of (path, PDFAnalysis) tuples
