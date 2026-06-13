"""Training-dependency detection & install planning (Milestone 2, req #3).

Training a custom wake word needs a heavy ML stack (PyTorch, TensorFlow, and a
few openWakeWord training extras) that MimOSA does **not** ship by default --
they total a couple of gigabytes and most users never train a custom word. So
before any training starts we check what's already installed and, if something
is missing, tell the user exactly how big the one-time download is and let them
decide.

This module is intentionally dependency-free and **never imports** the heavy
packages -- it only probes for their presence with :func:`importlib.util.
find_spec`, so it's safe and fast on any machine and easy to unit-test by
injecting a fake probe.

User-facing flow (driven by the UI):

* All deps present  -> training can start immediately.
* Some deps missing -> show a dialog: *"Training requires ~2.5 GB download
  (PyTorch, TensorFlow). Continue?"* with **[Download & Train]**,
  **[Use Mimosa Instead]**, **[Cancel]**.
* User declines / download fails -> fall back to the default "Mimosa" wake word
  and suggest training later from Settings. Nothing ever crashes.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrainingDependency:
    """One required package for the training pipeline.

    Attributes:
        module: Importable module name probed with ``find_spec``.
        pip_name: Name to ``pip install`` (may differ from ``module``).
        purpose: Short, friendly explanation of what it's for.
        approx_mb: Approximate download size in megabytes (for the prompt).
        optional: If ``True`` its absence does not block training (it only
            reduces augmentation/quality), so it isn't counted as "missing"
            for the blocking prompt.
    """

    module: str
    pip_name: str
    purpose: str
    approx_mb: int
    optional: bool = False


#: The heavy stack required to train a custom wake word. Sizes are approximate
#: CPU-wheel download sizes; the prompt rounds the total to ~GB.
TRAINING_DEPENDENCIES: tuple = (
    TrainingDependency(
        "torch", "torch", "the neural-network training engine (PyTorch)", 1900
    ),
    TrainingDependency(
        "tensorflow", "tensorflow",
        "audio feature extraction used by openWakeWord", 580,
    ),
    TrainingDependency(
        "torchinfo", "torchinfo", "model summaries during training", 1
    ),
    TrainingDependency(
        "pronouncing", "pronouncing",
        "phonetic lookups for generating negative samples", 5,
    ),
    # Optional niceties: improve augmentation quality but training works without.
    TrainingDependency(
        "scipy", "scipy", "high-quality audio resampling & reverb", 35,
        optional=True,
    ),
    TrainingDependency(
        "soundfile", "soundfile", "fast audio file I/O", 2, optional=True,
    ),
)


@dataclass
class DependencyReport:
    """Result of a dependency scan.

    Attributes:
        satisfied: ``True`` when every *required* dependency is importable.
        missing_required: Required deps that are absent (block training).
        missing_optional: Optional deps that are absent (quality only).
        present: Modules found to be importable.
    """

    satisfied: bool
    missing_required: List[TrainingDependency] = field(default_factory=list)
    missing_optional: List[TrainingDependency] = field(default_factory=list)
    present: List[str] = field(default_factory=list)

    @property
    def download_mb(self) -> int:
        """Approximate total download size (MB) for the missing *required* deps."""
        return sum(d.approx_mb for d in self.missing_required)

    @property
    def download_size_text(self) -> str:
        """A human-friendly size like ``"2.5 GB"`` or ``"610 MB"``."""
        mb = self.download_mb
        if mb >= 1024:
            return f"{mb / 1024:.1f} GB"
        return f"{mb} MB"

    def prompt_message(self) -> str:
        """The friendly confirmation text shown before downloading.

        Mirrors the milestone's required copy while naming the biggest pieces.
        """
        names = ", ".join(
            d.pip_name.capitalize()
            for d in self.missing_required
            if d.approx_mb >= 100
        ) or "PyTorch, TensorFlow"
        return (
            f"Training your own wake word needs a one-time download of about "
            f"{self.download_size_text} ({names}). It runs entirely on your "
            f"device — nothing is uploaded. Would you like to download it and "
            f"train now?"
        )

    def pip_install_args(self) -> List[str]:
        """The ``pip install`` target list for the missing required deps."""
        return [d.pip_name for d in self.missing_required]


def _default_probe(module: str) -> bool:
    """Return ``True`` if ``module`` is importable, without importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:  # pragma: no cover - defensive (malformed env)
        logger.debug("find_spec failed for %s", module, exc_info=True)
        return False


def check_dependencies(
    probe: Optional[Callable[[str], bool]] = None,
    dependencies: tuple = TRAINING_DEPENDENCIES,
) -> DependencyReport:
    """Scan for the training stack and report what's present/missing.

    Args:
        probe: Optional injectable predicate ``module_name -> bool`` (used by
            tests to simulate installed/missing packages). Defaults to a real
            ``importlib`` probe that never imports the heavy modules.
        dependencies: The dependency catalogue to check (override for tests).

    Returns:
        A :class:`DependencyReport`. ``satisfied`` is ``True`` only when all
        *required* dependencies are importable; optional ones never block.
    """
    probe = probe or _default_probe
    present: List[str] = []
    missing_required: List[TrainingDependency] = []
    missing_optional: List[TrainingDependency] = []

    for dep in dependencies:
        if probe(dep.module):
            present.append(dep.module)
        elif dep.optional:
            missing_optional.append(dep)
        else:
            missing_required.append(dep)

    return DependencyReport(
        satisfied=not missing_required,
        missing_required=missing_required,
        missing_optional=missing_optional,
        present=present,
    )


def dependencies_satisfied(probe: Optional[Callable[[str], bool]] = None) -> bool:
    """Convenience: ``True`` when the required training stack is installed."""
    return check_dependencies(probe=probe).satisfied


def install_dependencies(
    report: Optional[DependencyReport] = None,
    *,
    on_output: Optional[Callable[[str], None]] = None,
    runner: Optional[Callable[..., int]] = None,
    timeout: Optional[float] = None,
) -> bool:
    """Best-effort install of the missing required training dependencies.

    Runs ``pip install`` in a subprocess so a long download can't block or crash
    the GUI thread (callers should invoke this from a worker thread and stream
    ``on_output`` lines to a progress view).

    Args:
        report: A prior :class:`DependencyReport`; re-scanned if omitted.
        on_output: Optional sink for human-readable progress/status lines.
        runner: Injectable command runner (for tests). Receives the argv list
            and returns a process exit code. Defaults to a real
            ``subprocess`` call.
        timeout: Optional overall timeout (seconds) for the default runner.

    Returns:
        ``True`` if, after the attempt, all required deps are importable.
        Never raises -- a failed/cancelled download returns ``False`` so the
        caller can fall back to the default "Mimosa" wake word.
    """
    report = report or check_dependencies()
    if report.satisfied:
        return True

    targets = report.pip_install_args()
    if not targets:
        return True

    def _emit(line: str) -> None:
        if on_output is not None:
            try:
                on_output(line)
            except Exception:  # pragma: no cover - sink must never break us
                logger.debug("dependency on_output sink failed", exc_info=True)

    _emit(f"Downloading {report.download_size_text} of training tools…")

    runner = runner or _default_pip_runner
    argv = [sys.executable, "-m", "pip", "install", *targets]
    try:
        code = runner(argv, on_output=_emit, timeout=timeout)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Dependency install failed: %s", exc)
        _emit(f"Couldn't install training tools: {exc}")
        return False

    if code != 0:
        _emit("The download didn't finish. You can try again later from Settings.")
        return False

    # Re-scan: importlib caches; invalidate so freshly-installed deps are seen.
    importlib.invalidate_caches()
    ok = check_dependencies().satisfied
    _emit("Training tools are ready!" if ok else
          "Some tools are still missing after install.")
    return ok


def _default_pip_runner(
    argv: List[str],
    *,
    on_output: Optional[Callable[[str], None]] = None,
    timeout: Optional[float] = None,
) -> int:
    """Run pip, streaming stdout lines to ``on_output``; return the exit code."""
    if shutil.which(argv[0]) is None and not argv[0].endswith("python") \
            and "-m" not in argv:  # pragma: no cover - sanity only
        logger.debug("pip runner: interpreter %s not found", argv[0])
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line and on_output is not None:
                on_output(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:  # pragma: no cover - long-running guard
        proc.kill()
        return 1
    return int(proc.returncode or 0)
