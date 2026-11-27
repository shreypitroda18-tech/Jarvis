"""Startup stage timing tracker and reporter for JARVIS."""

import time
from typing import Dict, List, Tuple
from jarvis.config import logger


class StartupTracker:
    """Tracks duration of key startup stages and writes them to logs/jarvis.log."""

    def __init__(self):
        self._start_time = time.perf_counter()
        self._stages: List[Tuple[str, float, str]] = []  # (name, duration, notes)
        self._stage_starts: Dict[str, float] = {}
        self.show_timing_summary: bool = False

    def start_stage(self, name: str) -> None:
        """Starts timing a specific named startup stage."""
        self._stage_starts[name] = time.perf_counter()

    def end_stage(self, name: str, notes: str = "") -> float:
        """Ends timing for a stage, records it, and logs to logs/jarvis.log."""
        start = self._stage_starts.pop(name, self._start_time)
        duration = time.perf_counter() - start
        self._stages.append((name, duration, notes))
        log_line = f"Startup stage '{name}': {duration:.3f}s"
        if notes:
            log_line += f" ({notes})"
        logger.info(log_line)
        return duration

    def record_stage(self, name: str, duration: float, notes: str = "") -> None:
        """Directly records a pre-timed or asynchronous stage."""
        self._stages.append((name, duration, notes))
        log_line = f"Startup stage '{name}': {duration:.3f}s"
        if notes:
            log_line += f" ({notes})"
        logger.info(log_line)

    def print_summary_table(self) -> None:
        """Prints a clean summary table to stdout if --timing flag is enabled."""
        if not self.show_timing_summary:
            return

        total_elapsed = time.perf_counter() - self._start_time
        print("\n================== JARVIS Startup Timing Summary ==================")
        print(f"{'Stage':<32} {'Duration':<10} {'Notes'}")
        print("-" * 65)
        for name, duration, notes in self._stages:
            print(f"{name:<32} {duration:>7.3f}s   {notes}")
        print("-" * 65)
        print(f"{'Total Startup Elapsed':<32} {total_elapsed:>7.3f}s")
        print("===================================================================\n")


# Global tracker singleton
tracker = StartupTracker()
