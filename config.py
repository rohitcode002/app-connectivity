from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


# Pipeline defaults
# MAX_PAGES:
#   > 0  -> process first N pages per PDF
#   = -1 -> process all pages per PDF
MAX_PAGES = 100
MODEL = "gpt-4o-mini"

# Single switch for runtime mode:
#   "laptop" -> direct OpenAI API key mode
#   "vm"     -> script-based VM mode
EXECUTION_TARGET = "vm"   # default: use VM script mode (change to "laptop" for direct API)

# DOWNLOAD_LIMIT:
#   > 0  -> download up to N PDFs per scraper/type
#   = -1 -> download all available PDFs
DOWNLOAD_LIMIT = -1  # default: 5 PDFs per scraper/type
DOWNLOAD_ALL = False  # True downloads every available PDF and ignores DOWNLOAD_LIMIT

# Source selection defaults.
# Empty list/string -> all sources or all regions.
# Source keys should match pipeline handlers/scraper keys such as:
#   cmets, jcc, effectiveness, bayallocation
SOURCE_NAMES = ["cmets", "jcc", "effectiveness", "bayallocation"]
SOURCE_REGIONS = ["Northern Region"]

# Proxy settings for VM downloader
PROXY_ENABLED = False
PROXY_URL = "http://cloudproxy.adani.com:8080"
PROXY_INSECURE_SSL = False

# OCR settings for CMETS first-page meeting metadata.
# On Windows this is usually: C:\Program Files\Tesseract-OCR
TESSERACT_OCR_DIR = r"C:\Program Files\Tesseract-OCR"
TESSERACT_CMD = rf"{TESSERACT_OCR_DIR}\tesseract.exe"


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime settings for LLM access and execution mode."""

    execution_target: str  # "vm" or "laptop"
    vm_mode: bool
    api_key: str
    llm_script_path: Optional[str]
    download_limit: int  # -1 = all, N = first N PDFs per scraper/type
    download_all: bool
    proxy_enabled: bool
    proxy_url: str
    proxy_insecure_ssl: bool
    source_names: tuple[str, ...]
    source_regions: tuple[str, ...]
    max_pages: int  # -1 = all pages, N = first N pages per PDF


def _split_config_list(value) -> tuple[str, ...]:
    """Normalize comma-separated strings or iterables into a tuple of strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value)
    return tuple(str(part).strip() for part in parts if str(part).strip())


def load_runtime_config(
    *,
    mode_override: Optional[str] = None,
    api_key_override: Optional[str] = None,
    llm_script_override: Optional[str] = None,
    download_limit_override: Optional[int] = None,
    download_all_override: Optional[bool] = None,
    max_pages_override: Optional[int] = None,
    require_api_key: bool = True,
) -> RuntimeConfig:
    """
    Resolve runtime config from .env + CLI overrides.

    Priority:
    1) explicit CLI overrides
    2) environment variables
    3) defaults

    Supported env variables:
    - OPENAI_API_KEY
    - LLM_SCRIPT_PATH
    - DOWNLOAD_LIMIT
    - DOWNLOAD_ALL
    - PROXY_ENABLED
    - PROXY_URL
    - PROXY_INSECURE_SSL
    - SOURCE_NAMES
    - SOURCE_REGIONS
    """
    load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=False)

    requested_target = (mode_override or EXECUTION_TARGET).strip().lower()
    if requested_target not in {"vm", "laptop"}:
        requested_target = "laptop"

    vm_mode = requested_target == "vm"

    api_key = (api_key_override or os.getenv("OPENAI_API_KEY") or "").strip()
    llm_script_path = (llm_script_override or os.getenv("LLM_SCRIPT_PATH") or "").strip() or None

    if download_all_override is not None:
        download_all = download_all_override
    else:
        env_download_all = os.getenv("DOWNLOAD_ALL", "").strip().lower()
        download_all = (
            env_download_all in {"1", "true", "yes", "y", "on"}
            if env_download_all
            else DOWNLOAD_ALL
        )

    # Proxy settings
    env_proxy_enabled = os.getenv("PROXY_ENABLED", "").strip().lower()
    proxy_enabled = (
        env_proxy_enabled in {"1", "true", "yes", "y", "on"}
        if env_proxy_enabled
        else PROXY_ENABLED
    )
    proxy_url = os.getenv("PROXY_URL", "").strip() or PROXY_URL
    env_proxy_insecure = os.getenv("PROXY_INSECURE_SSL", "").strip().lower()
    proxy_insecure_ssl = (
        env_proxy_insecure in {"1", "true", "yes", "y", "on"}
        if env_proxy_insecure
        else PROXY_INSECURE_SSL
    )

    # Download limit
    if download_limit_override is not None:
        dl_limit = download_limit_override
    else:
        env_limit = os.getenv("DOWNLOAD_LIMIT", "").strip()
        dl_limit = int(env_limit) if env_limit else DOWNLOAD_LIMIT

    if download_all:
        dl_limit = -1

    env_source_names = os.getenv("SOURCE_NAMES")
    source_names = _split_config_list(env_source_names) if env_source_names is not None else _split_config_list(SOURCE_NAMES)
    env_source_regions = os.getenv("SOURCE_REGIONS")
    source_regions = (
        _split_config_list(env_source_regions)
        if env_source_regions is not None
        else _split_config_list(SOURCE_REGIONS)
    )

    if require_api_key and not vm_mode and not api_key:
        raise SystemExit(
            "ERROR: OPENAI_API_KEY is required in laptop mode. "
            "Set EXECUTION_TARGET=vm (or VM=true) to use VM script mode."
        )

    # Max pages per PDF
    if max_pages_override is not None:
        mp = max_pages_override
    else:
        env_mp = os.getenv("MAX_PAGES", "").strip()
        mp = int(env_mp) if env_mp else MAX_PAGES

    return RuntimeConfig(
        execution_target="vm" if vm_mode else "laptop",
        vm_mode=vm_mode,
        api_key=api_key,
        llm_script_path=llm_script_path,
        download_limit=dl_limit,
        download_all=download_all or dl_limit == -1,
        proxy_enabled=proxy_enabled,
        proxy_url=proxy_url,
        proxy_insecure_ssl=proxy_insecure_ssl,
        source_names=source_names,
        source_regions=source_regions,
        max_pages=mp,
    )
