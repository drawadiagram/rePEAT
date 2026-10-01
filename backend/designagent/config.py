"""Application settings.

Everything is overridable by environment variable so the same image runs on a
laptop (local process pool, public REST APIs) and against real HPC (Orbit).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="DESIGNAGENT_", extra="ignore"
    )

    # --- LLM ---
    # Read without the DESIGNAGENT_ prefix: it is the conventional name.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    model: str = "claude-sonnet-5-5"
    max_tokens: int = 2048

    # --- storage ---
    data_dir: Path = Path("./data")

    # --- compute ---
    pool_workers: int = 4
    # Wall-clock ceiling for a single local task. None disables it.
    task_timeout_sec: float | None = 900.0
    # Rounds of redesign the orchestrator may run before it must summarize.
    max_rounds: int = 3
    # Route node bodies through flowgentic's EXECUTION_BLOCK as well as tasks.
    # Off by default: it runs nodes outside LangGraph's runnable context, which
    # silently disables status and token streaming. See graph/build.py.
    wrap_nodes: bool = False

    # --- Orbit ---
    orbit_enabled: bool = False
    orbit_endpoint: str = ""
    orbit_broker_url: str = Field(default="", alias="RADICAL_ORBIT_BROKER_URL")
    orbit_broker_token: str = Field(default="", alias="RADICAL_ORBIT_BROKER_TOKEN")
    orbit_broker_cert: str = Field(default="", alias="RADICAL_ORBIT_BROKER_CERT")

    # --- external services ---
    fold_backend: Literal["esmatlas", "local", "hpc"] = "esmatlas"
    http_timeout_sec: float = 60.0
    user_agent: str = "designagent/0.1 (protein redesign agent)"

    # --- derived paths -------------------------------------------------
    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def blobs_dir(self) -> Path:
        """Raw task outputs (structures, FASTA, logs) referenced by tier 1."""
        return self.data_dir / "blobs"

    @property
    def graph_db_path(self) -> Path:
        return self.data_dir / "lake" / "graph"

    @property
    def scores_db_path(self) -> Path:
        return self.data_dir / "lake" / "scores.sqlite"

    @property
    def golden_dir(self) -> Path:
        return self.data_dir / "lake" / "golden"

    @property
    def checkpoint_db_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def flow_work_dir(self) -> Path:
        """Keep asyncflow session dirs out of the repo root."""
        return self.data_dir / "flow"

    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir,
            self.artifacts_dir,
            self.blobs_dir,
            self.graph_db_path.parent,
            self.golden_dir,
            self.flow_work_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)

    @property
    def llm_available(self) -> bool:
        return bool(self.anthropic_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
