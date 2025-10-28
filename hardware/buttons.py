"""Überwachung physischer Taster über lgpio."""

from __future__ import annotations

import glob
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Literal

try:  # pragma: no cover - Import wird separat getestet
    import lgpio as GPIO
except ImportError:  # pragma: no cover - Verhalten wird in Tests geprüft
    GPIO = None  # type: ignore[assignment]
    GPIO_AVAILABLE = False
else:
    GPIO_AVAILABLE = True

PullType = Literal["up", "down", "none"]
EdgeType = Literal["rising", "falling", "both"]


@dataclass
class ButtonAssignment:
    """Beschreibt einen GPIO-Taster und die zugehörige Aktion."""

    name: str
    pin: int
    callback: Callable[..., None] = field(repr=False)
    pull: PullType = "up"
    edge: EdgeType = "falling"
    debounce_ms: int = 150
    args: Tuple[Any, ...] = field(default_factory=tuple)
    kwargs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.pull = self.pull.lower()  # type: ignore[assignment]
        self.edge = self.edge.lower()  # type: ignore[assignment]
        if self.pull not in {"up", "down", "none"}:
            raise ValueError(f"Ungültiger Pull-Modus für Button '{self.name}': {self.pull}")
        if self.edge not in {"rising", "falling", "both"}:
            raise ValueError(f"Ungültiger Flankentyp für Button '{self.name}': {self.edge}")
        if self.debounce_ms < 0:
            raise ValueError(f"Negative Entprellzeit für Button '{self.name}' ist nicht erlaubt")


@dataclass
class _RuntimeButton:
    assignment: ButtonAssignment
    last_level: Optional[int] = None
    last_event_ts: float = 0.0


class ButtonMonitor:
    """Überwacht konfigurierte GPIO-Taster in einem Hintergrund-Thread."""

    def __init__(
        self,
        assignments: Iterable[ButtonAssignment],
        *,
        chip_id: Optional[int] = None,
        poll_interval: float = 0.01,
        name: str = "gpio-buttons",
        chip_candidates: Optional[Iterable[int]] = None,
    ) -> None:
        self._assignments: List[ButtonAssignment] = list(assignments)
        self._chip_id = chip_id
        self._extra_chip_candidates = list(chip_candidates or [])
        self._poll_interval = max(0.001, float(poll_interval))
        self._name = name
        self._handle: Optional[int] = None
        self._chip_in_use: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        self._buttons: List[_RuntimeButton] = []
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> bool:
        """Startet die Überwachung, falls möglich."""

        if not GPIO_AVAILABLE:
            logging.info("GPIO-Button-Monitor: lgpio nicht verfügbar – Überwachung deaktiviert")
            return False

        if not self._assignments:
            logging.info("GPIO-Button-Monitor: Keine Button-Zuordnungen konfiguriert")
            return False

        with self._lock:
            if self.running:
                return True

            handle = self._open_handle()
            if handle is None:
                return False

            self._handle = handle
            self._buttons = []
            stop_event = threading.Event()
            self._stop_event = stop_event

            try:
                for assignment in self._assignments:
                    runtime_button = self._claim_line(assignment)
                    self._buttons.append(runtime_button)
            except Exception as exc:  # pragma: no cover - Fehlerfall schwer auszulösen
                logging.error(
                    "GPIO-Button-Monitor: Initialisierung fehlgeschlagen (%s)", exc
                )
                self._release_all_lines()
                self._close_handle()
                return False

            thread = threading.Thread(
                target=self._run,
                args=(stop_event,),
                name=f"{self._name}-thread",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            logging.info(
                "GPIO-Button-Monitor gestartet (Chip %s, %s Buttons)",
                self._chip_in_use,
                len(self._buttons),
            )
            return True

    def stop(self, timeout: Optional[float] = None) -> None:
        """Stoppt die Überwachung und gibt alle Ressourcen frei."""

        with self._lock:
            thread = self._thread
            if thread is None:
                self._release_all_lines()
                self._close_handle()
                return

            stop_event = self._stop_event
            stop_event.set()

        thread.join(timeout=timeout)

        with self._lock:
            self._thread = None
            self._stop_event = threading.Event()
            self._release_all_lines()
            self._close_handle()
            logging.info("GPIO-Button-Monitor gestoppt")

    # --- interne Hilfsfunktionen -------------------------------------------------

    def _open_handle(self) -> Optional[int]:
        if self._handle is not None:
            return self._handle

        if GPIO is None:  # pragma: no cover - Schutz falls Import zur Laufzeit fehlschlägt
            return None

        candidates = self._build_candidates()
        errors: List[Tuple[int, Exception]] = []

        for candidate in candidates:
            try:
                handle = GPIO.gpiochip_open(candidate)
            except (GPIO.error, OSError) as exc:
                errors.append((candidate, exc))
                continue
            else:
                self._chip_in_use = candidate
                return handle

        if errors:
            for chip_id, error in errors:
                logging.debug(
                    "GPIO-Button-Monitor: gpiochip%s konnte nicht geöffnet werden: %s",
                    chip_id,
                    error,
                )
        logging.error("GPIO-Button-Monitor: Kein verfügbarer GPIO-Chip gefunden")
        return None

    def _build_candidates(self) -> List[int]:
        seen: set[int] = set()
        result: List[int] = []

        def _add_candidate(candidate: Optional[int]) -> None:
            if candidate is None:
                return
            if candidate in seen:
                return
            seen.add(candidate)
            result.append(candidate)

        _add_candidate(self._chip_id)

        for candidate in self._extra_chip_candidates:
            _add_candidate(candidate)

        raw_env_chip = os.getenv("GPIO_BUTTON_CHIP")
        if raw_env_chip:
            for part in raw_env_chip.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    _add_candidate(int(part, 0))
                except ValueError:
                    logging.warning(
                        "GPIO-Button-Monitor: Ungültiger Wert '%s' in GPIO_BUTTON_CHIP",
                        part,
                    )

        raw_env_candidates = os.getenv("GPIO_BUTTON_CHIP_CANDIDATES")
        if raw_env_candidates:
            for part in raw_env_candidates.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    _add_candidate(int(part, 0))
                except ValueError:
                    logging.warning(
                        "GPIO-Button-Monitor: Ungültiger Wert '%s' in GPIO_BUTTON_CHIP_CANDIDATES",
                        part,
                    )

        for default_candidate in (4, 0):
            _add_candidate(default_candidate)

        for path in sorted(glob.glob("/dev/gpiochip*")):
            suffix = path[len("/dev/gpiochip") :]
            if suffix.isdigit():
                _add_candidate(int(suffix))

        return result

    def _claim_line(self, assignment: ButtonAssignment) -> _RuntimeButton:
        assert self._handle is not None
        assert GPIO is not None

        flags = 0
        if assignment.pull == "up":
            flags |= GPIO.SET_PULL_UP
        elif assignment.pull == "down":
            flags |= GPIO.SET_PULL_DOWN

        # Laut offizieller lgpio-Referenz (https://abyz.me.uk/lg/py_lgpio.html#gpio_claim_input)
        # lautet die Signatur gpio_claim_input(handle, line, flags). Daher muss der Pin als
        # zweites Argument übergeben werden und die Flags folgen als drittes Argument.
        GPIO.gpio_claim_input(self._handle, assignment.pin, flags)
        level = GPIO.gpio_read(self._handle, assignment.pin)
        logging.debug(
            "GPIO-Button-Monitor: Button '%s' auf Pin %s initialer Pegel %s",
            assignment.name,
            assignment.pin,
            level,
        )
        return _RuntimeButton(assignment=assignment, last_level=level)

    def _release_all_lines(self) -> None:
        handle = self._handle
        if handle is None or GPIO is None:
            self._buttons.clear()
            return

        for runtime_button in self._buttons:
            try:
                GPIO.gpio_free(handle, runtime_button.assignment.pin)
            except (GPIO.error, OSError):  # pragma: no cover - reine Aufräumlogik
                logging.debug(
                    "GPIO-Button-Monitor: Fehler beim Freigeben von Pin %s",
                    runtime_button.assignment.pin,
                    exc_info=True,
                )
        self._buttons.clear()

    def _close_handle(self) -> None:
        if self._handle is None or GPIO is None:
            self._chip_in_use = None
            self._handle = None
            return
        try:
            GPIO.gpiochip_close(self._handle)
        except (GPIO.error, OSError):  # pragma: no cover - reine Aufräumlogik
            logging.debug(
                "GPIO-Button-Monitor: Fehler beim Schließen des GPIO-Handles",
                exc_info=True,
            )
        finally:
            self._handle = None
            self._chip_in_use = None

    def _run(self, stop_event: threading.Event) -> None:
        assert GPIO is not None
        handle = self._handle
        if handle is None:
            return

        while not stop_event.is_set():
            for runtime_button in self._buttons:
                self._process_button(handle, runtime_button)
            stop_event.wait(self._poll_interval)

    def _process_button(self, handle: int, runtime_button: _RuntimeButton) -> None:
        assignment = runtime_button.assignment
        try:
            level = GPIO.gpio_read(handle, assignment.pin)
        except (GPIO.error, OSError) as exc:
            logging.error(
                "GPIO-Button-Monitor: Fehler beim Lesen von Pin %s: %s",
                assignment.pin,
                exc,
            )
            return

        if runtime_button.last_level is None:
            runtime_button.last_level = level
            return

        if level == runtime_button.last_level:
            return

        previous_level = runtime_button.last_level
        runtime_button.last_level = level

        event: Optional[str] = None
        if (
            previous_level == 1
            and level == 0
            and assignment.edge in {"falling", "both"}
        ):
            event = "falling"
        elif (
            previous_level == 0
            and level == 1
            and assignment.edge in {"rising", "both"}
        ):
            event = "rising"

        if event is None:
            return

        now = time.monotonic()
        if (now - runtime_button.last_event_ts) * 1000.0 < assignment.debounce_ms:
            logging.debug(
                "GPIO-Button-Monitor: Flanke %s auf Pin %s verworfen (Entprellung)",
                event,
                assignment.pin,
            )
            return

        runtime_button.last_event_ts = now
        logging.info(
            "GPIO-Button-Monitor: Flanke %s auf Pin %s → Aktion '%s'",
            event,
            assignment.pin,
            assignment.name,
        )
        self._dispatch_callback(assignment)

    def _dispatch_callback(self, assignment: ButtonAssignment) -> None:
        def _invoke() -> None:
            try:
                assignment.callback(*assignment.args, **assignment.kwargs)
            except Exception:  # pragma: no cover - Callback-Fehler werden geloggt
                logging.exception(
                    "GPIO-Button-Monitor: Fehler im Callback '%s'",
                    assignment.name,
                )

        thread = threading.Thread(
            target=_invoke,
            name=f"{self._name}-{assignment.name}",
            daemon=True,
        )
        thread.start()


__all__ = ["ButtonMonitor", "ButtonAssignment", "GPIO_AVAILABLE"]
