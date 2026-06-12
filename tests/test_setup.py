"""M1.1 setup-validation tests.

These tests confirm that the project scaffold is correct and the LLM
abstraction layer is wired up. They are deliberately fast and do **not**
require network access or heavy optional dependencies (Whisper, openWakeWord,
PyAudio) -- those are checked separately by ``scripts/health_check.py``.

Run with:
    pytest
or specifically:
    pytest tests/test_setup.py -v
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Project root = parent of the tests/ directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 1. Directory structure
# ---------------------------------------------------------------------------

REQUIRED_DIRS = [
    "mimosa",
    "mimosa/core",
    "mimosa/llm",
    "mimosa/voice",
    "mimosa/memory",
    "mimosa/skills",
    "mimosa/system",
    "mimosa/ui",
    "mimosa/utils",
    "data",
    "tests",
    "scripts",
    "config",
    "docs",
]


@pytest.mark.parametrize("rel_dir", REQUIRED_DIRS)
def test_required_directory_exists(rel_dir: str) -> None:
    """Every required project directory must exist."""
    path = PROJECT_ROOT / rel_dir
    assert path.is_dir(), f"Missing required directory: {rel_dir}"


PACKAGE_DIRS = [
    "mimosa",
    "mimosa/core",
    "mimosa/llm",
    "mimosa/voice",
    "mimosa/memory",
    "mimosa/skills",
    "mimosa/system",
    "mimosa/ui",
    "mimosa/utils",
]


@pytest.mark.parametrize("pkg_dir", PACKAGE_DIRS)
def test_package_has_init(pkg_dir: str) -> None:
    """Every Python package directory must contain an __init__.py."""
    init_file = PROJECT_ROOT / pkg_dir / "__init__.py"
    assert init_file.is_file(), f"Missing __init__.py in {pkg_dir}"


# ---------------------------------------------------------------------------
# 2. Config files present
# ---------------------------------------------------------------------------

REQUIRED_FILES = [
    ".gitignore",
    ".env.example",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "docs/ARCHITECTURE.md",
    "scripts/health_check.py",
]


@pytest.mark.parametrize("rel_file", REQUIRED_FILES)
def test_required_file_exists(rel_file: str) -> None:
    """Required top-level config/documentation files must exist."""
    path = PROJECT_ROOT / rel_file
    assert path.is_file(), f"Missing required file: {rel_file}"


def test_env_example_has_expected_keys() -> None:
    """.env.example should document the core configuration keys."""
    content = (PROJECT_ROOT / ".env.example").read_text()
    for key in ("ABACUS_API_KEY", "USE_LOCAL_LLM", "LOG_LEVEL"):
        assert key in content, f"{key} missing from .env.example"


# ---------------------------------------------------------------------------
# 3. Core (non-optional) dependencies importable
# ---------------------------------------------------------------------------

CORE_DEPENDENCIES = ["requests", "dotenv", "psutil"]


@pytest.mark.parametrize("module_name", CORE_DEPENDENCIES)
def test_core_dependency_importable(module_name: str) -> None:
    """Core dependencies needed for setup validation must import."""
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.fail(f"Could not import core dependency {module_name!r}: {exc}")


# ---------------------------------------------------------------------------
# 4. LLM abstraction layer initializes correctly
# ---------------------------------------------------------------------------

def test_mimosa_package_imports() -> None:
    """The top-level package imports and exposes a version string."""
    import mimosa

    assert isinstance(mimosa.__version__, str) and mimosa.__version__


def test_provider_factory_defaults_to_abacus() -> None:
    """With no overrides, the factory returns the Abacus (cloud) provider."""
    from mimosa.llm import create_provider

    provider = create_provider()
    assert provider.name == "abacus"
    assert provider.is_local is False


def test_provider_factory_local_path() -> None:
    """use_local=True must return an on-device provider (Privacy Guard path)."""
    from mimosa.llm import create_provider

    provider = create_provider(use_local=True)
    assert provider.name == "local"
    assert provider.is_local is True


def test_provider_factory_respects_use_local_env(monkeypatch) -> None:
    """The USE_LOCAL_LLM env var should select the local provider."""
    from mimosa.llm import create_provider

    monkeypatch.setenv("USE_LOCAL_LLM", "true")
    provider = create_provider()
    assert provider.is_local is True


def test_provider_factory_rejects_unknown_provider() -> None:
    """Requesting an unregistered provider must raise ValueError."""
    from mimosa.llm import create_provider

    with pytest.raises(ValueError):
        create_provider("does-not-exist")


def test_all_registered_providers_subclass_base() -> None:
    """Every registered provider must implement the abstract interface."""
    from mimosa.llm.base_provider import BaseLLMProvider
    from mimosa.llm.provider_factory import PROVIDER_REGISTRY

    assert PROVIDER_REGISTRY, "Provider registry should not be empty"
    for key, cls in PROVIDER_REGISTRY.items():
        assert issubclass(cls, BaseLLMProvider), f"{key} is not a BaseLLMProvider"


def test_message_serialization_roundtrip() -> None:
    """Message.to_dict produces the role/content shape chat APIs expect."""
    from mimosa.llm import Message, Role

    msg = Message(role=Role.USER, content="hello")
    assert msg.to_dict() == {"role": "user", "content": "hello"}


def test_local_provider_chat_not_implemented() -> None:
    """LocalProvider is a placeholder: chat() must raise NotImplementedError."""
    from mimosa.llm import Message, Role, create_provider

    provider = create_provider(use_local=True)
    with pytest.raises(NotImplementedError):
        provider.chat([Message(role=Role.USER, content="hi")])
