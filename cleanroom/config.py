"""Cleanroom configuration.

Loads every setting from environment variables with sensible defaults, or
from the YAML config file. Paths resolve to absolute under the project root
(parent of cleanroom/) unless an env var overrides them.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def package_root() -> Path:
    """Installed or source package directory (absolute)."""
    return Path(__file__).resolve().parent


def project_root() -> Path:
    """Return the checkout root, or the current directory when installed.

    A wheel must never default runtime writes into ``site-packages``. Source
    checkouts use their repository root; regular installations use the
    operator's current working directory unless an explicit path is supplied.
    """
    checkout = package_root().parent
    if (checkout / "pyproject.toml").is_file():
        return checkout
    return Path.cwd().resolve()


def _abs_under_root(path: Path | str, root: Path | None = None) -> Path:
    """Resolve path to absolute; relative paths are relative to project root."""
    root = root or project_root()
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = root / p
    return p.resolve()


def yaml_config_path() -> Path:
    """Path to the YAML config file (CLEANROOM_CONFIG or the project default)."""
    if v := os.environ.get("CLEANROOM_CONFIG"):
        return Path(v).expanduser()
    return project_root() / "cleanroom.yaml"


# YAML omlx: key → (Settings field, type coercion). The API key is deliberately
# absent — secrets belong in the OMLX_API_KEY env var, not in a config file.
_OMLX_YAML_KEYS: dict[str, tuple[str, type]] = {
    "base_url": ("omlx_base_url", str),
    "model": ("omlx_model", str),
    "model_judge": ("omlx_model_judge", str),
    "model_final_judge": ("omlx_model_final_judge", str),
    "timeout": ("omlx_timeout", float),
    "max_tokens": ("omlx_max_tokens", int),
    "use_responses_api": ("omlx_use_responses_api", bool),
}

# DGX YAML: same shape as omlx, plus enable_thinking. Key stays out of the
# DGX_API_KEY / VLLM_API_KEY env vars for the same reason as above.
_DGX_YAML_KEYS: dict[str, tuple[str, type]] = {
    "base_url": ("dgx_base_url", str),
    "model": ("dgx_model", str),
    "model_judge": ("dgx_model_judge", str),
    "model_final_judge": ("dgx_model_final_judge", str),
    "timeout": ("dgx_timeout", float),
    "max_tokens": ("dgx_max_tokens", int),
    "enable_thinking": ("dgx_enable_thinking", bool),
}

_LLM_PROVIDERS: tuple[str, ...] = ("omlx", "dgx")
_PROVIDER_ALIASES: dict[str, str] = {"mlx": "omlx", "vllm": "dgx"}


def normalize_llm_provider(value: Any) -> str:
    """Normalize a provider name, raising ValueError on anything unknown."""
    p = str(value).strip().lower()
    p = _PROVIDER_ALIASES.get(p, p)
    if p not in _LLM_PROVIDERS:
        raise ValueError(
            f"llm_provider must be one of {sorted(_LLM_PROVIDERS)} "
            f"(aliases: {sorted(_PROVIDER_ALIASES)}), got {value!r}"
        )
    return p


def _coerce_bool(v: Any) -> bool:
    """Strict bool coercion for config files — bool('false') is True."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    raise ValueError(f"must be a boolean, got {v!r}")


def _parse_llm_section(
    path: Path, data: dict[str, Any], section: str, keys: dict[str, tuple[str, type]]
) -> dict[str, Any]:
    """Parse one YAML LLM section (omlx:/dgx:) into {Settings field: value}."""
    raw = data.get(section) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: '{section}' must be a mapping")
    unknown = set(raw) - set(keys)
    if unknown:
        raise ValueError(
            f"{path}: unknown {section} key(s) {sorted(unknown)} "
            f"(supported: {sorted(keys)})"
        )
    out: dict[str, Any] = {}
    for key, (field_name, coerce) in keys.items():
        if key not in raw:
            continue
        v = raw[key]
        try:
            if coerce is str:
                out[field_name] = "" if v is None else str(v)
            elif coerce is bool:
                out[field_name] = _coerce_bool(v)
            else:
                out[field_name] = coerce(v)
        except (TypeError, ValueError):
            raise ValueError(
                f"{path}: {section}.{key} must be {coerce.__name__}, got {v!r}"
            ) from None
    return out


def load_yaml_settings(path: Path | None = None) -> dict[str, Any]:
    """Read the YAML config file into {Settings field: value}.

    Missing file → {}. Invalid YAML, unknown sections/keys, or wrong types
    raise ValueError — a config file with a silent typo is worse than none.
    """
    path = path or yaml_config_path()
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError as e:
        raise ValueError(f"PyYAML is required to read {path} (pip install pyyaml)") from e
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping, got {type(data).__name__}")
    unknown_top = set(data) - {"provider", "omlx", "dgx", "clean"}
    if unknown_top:
        raise ValueError(
            f"{path}: unknown section(s) {sorted(unknown_top)} "
            f"(supported: 'provider', 'omlx', 'dgx', 'clean')"
        )
    out: dict[str, Any] = {}
    if "provider" in data:
        try:
            out["llm_provider"] = normalize_llm_provider(data["provider"])
        except ValueError as e:
            raise ValueError(f"{path}: provider: {e}") from None
    out.update(_parse_llm_section(path, data, "omlx", _OMLX_YAML_KEYS))
    out.update(_parse_llm_section(path, data, "dgx", _DGX_YAML_KEYS))

    clean = data.get("clean") or {}
    if not isinstance(clean, dict):
        raise ValueError(f"{path}: 'clean' must be a mapping")
    _CLEAN_KEYS = ("default_strength", "max_judge_iterations", "judge_score_threshold")
    unknown_clean = set(clean) - set(_CLEAN_KEYS)
    if unknown_clean:
        raise ValueError(
            f"{path}: unknown clean key(s) {sorted(unknown_clean)} "
            f"(supported: {sorted(_CLEAN_KEYS)})"
        )
    for key in _CLEAN_KEYS:
        if key in clean:
            out[key] = clean[key]
    return out


class Settings(BaseModel):
    """Pipeline configuration loaded from environment / defaults."""

    # ── LLM (OMLX) ──
    omlx_base_url: str = Field(
        default="http://127.0.0.1:8000/v1",
        description="OMLX OpenAI-compatible endpoint",
    )
    omlx_api_key: str = Field(default="test", repr=False)
    omlx_model: str = Field(
        default="DeepSeek-V4-Flash-0731-MXFP4-MLX",
        description="Default model for generation tasks",
    )
    omlx_model_judge: str = Field(
        default="DeepSeek-V4-Flash-0731-MXFP4-MLX",
        description="Model for LLM-as-a-Judge QA evaluation",
    )
    omlx_model_final_judge: str = Field(
        default="",
        description="Independent model for the final pass/fail verdict. "
                    "Empty string disables the independent final judge.",
    )
    omlx_timeout: float = Field(
        default=600.0,
        description="HTTP timeout in seconds (OMLX can be slow)",
    )
    omlx_max_tokens: int = Field(default=16384)
    omlx_use_responses_api: bool = Field(default=False)

    # ── LLM backend select ──
    llm_provider: str = Field(
        default="omlx",
        description="Active LLM backend: 'omlx' (Mac MLX server) or 'dgx' "
                    "(NVIDIA DGX vLLM server). Aliases: mlx, vllm.",
    )

    # ── LLM (DGX vLLM) ──
    dgx_base_url: str = Field(default="http://127.0.0.1:8000/v1")
    dgx_api_key: str = Field(default="test", repr=False)
    dgx_model: str = Field(default="Qwen/Qwen3.6-27B-FP8")
    dgx_model_judge: str = Field(default="Qwen/Qwen3.6-27B-FP8")
    dgx_model_final_judge: str = Field(default="")
    dgx_timeout: float = Field(default=600.0)
    dgx_max_tokens: int = Field(default=4096)
    dgx_enable_thinking: bool = Field(default=False)

    # ── Data paths ──
    workspace: Path = Field(default_factory=project_root)
    output_dir: Path = Field(default_factory=lambda: project_root() / "generated")
    run_ledger_path: Path = Field(default_factory=lambda: project_root() / "cleanroom_runs.jsonl")
    telemetry_path: Path = Field(default_factory=lambda: project_root() / "generated" / "telemetry" / "llm.jsonl")

    # ── Pipeline / QA ──
    default_strength: str = Field(
        default="safe",
        description="Default remediation strength: 'safe' (deterministic only) "
                    "or 'aggressive' (LLM rewrite + metadata strip).",
    )
    max_judge_iterations: int = Field(
        default=3,
        description="Max judge-and-revise loop iterations in QA.",
    )
    judge_score_threshold: float = Field(
        default=0.80,
        description="Minimum judge score (0-1) to mark QA as PASS.",
    )
    auto_apply: bool = Field(
        default=False,
        description="If True, remediation writes the cleaned file without "
                    "requiring the operator to confirm in the dashboard.",
    )

    @field_validator("llm_provider", mode="before")
    @classmethod
    def _normalize_provider(cls, v: Any) -> str:
        return normalize_llm_provider(v)

    @field_validator(
        "workspace",
        "output_dir",
        "run_ledger_path",
        "telemetry_path",
        mode="before",
    )
    @classmethod
    def _coerce_path(cls, v: Any) -> Path:
        if v is None:
            return project_root()
        return Path(v)

    @model_validator(mode="after")
    def _resolve_absolute_and_env(self) -> Settings:
        root = project_root()
        explicit = self.model_fields_set

        for attr, val in load_yaml_settings().items():
            if attr not in explicit:
                object.__setattr__(self, attr, val)
        env_map = {
            "CLEANROOM_WORKSPACE": "workspace",
            "CLEANROOM_OUTPUT_DIR": "output_dir",
            "CLEANROOM_RUN_LEDGER": "run_ledger_path",
            "CLEANROOM_TELEMETRY": "telemetry_path",
        }
        for env_key, attr in env_map.items():
            if attr in explicit:
                continue
            if v := os.environ.get(env_key):
                object.__setattr__(self, attr, Path(v))

        for attr in ("workspace", "output_dir", "run_ledger_path", "telemetry_path"):
            object.__setattr__(self, attr, _abs_under_root(getattr(self, attr), root))

        if "llm_provider" not in explicit:
            pv = os.environ.get("CLEANROOM_LLM_PROVIDER") or os.environ.get("LLM_PROVIDER")
            if pv:
                object.__setattr__(self, "llm_provider", normalize_llm_provider(pv))
        if "omlx_base_url" not in explicit and (v := os.environ.get("OMLX_BASE_URL")):
            object.__setattr__(self, "omlx_base_url", v)
        if "omlx_api_key" not in explicit and (v := os.environ.get("OMLX_API_KEY")):
            object.__setattr__(self, "omlx_api_key", v)
        if "omlx_model" not in explicit and (v := os.environ.get("OMLX_MODEL")):
            object.__setattr__(self, "omlx_model", v)
        if "omlx_model_judge" not in explicit and (v := os.environ.get("OMLX_MODEL_JUDGE")):
            object.__setattr__(self, "omlx_model_judge", v)
        if "omlx_model_final_judge" not in explicit and (v := os.environ.get("OMLX_MODEL_FINAL_JUDGE")):
            object.__setattr__(self, "omlx_model_final_judge", v)

        if "dgx_base_url" not in explicit and (v := os.environ.get("DGX_BASE_URL", os.environ.get("VLLM_BASE_URL"))):
            object.__setattr__(self, "dgx_base_url", v)
        if "dgx_api_key" not in explicit and (v := os.environ.get("DGX_API_KEY", os.environ.get("VLLM_API_KEY"))):
            object.__setattr__(self, "dgx_api_key", v)
        if "dgx_model" not in explicit and (v := os.environ.get("DGX_MODEL", os.environ.get("VLLM_MODEL"))):
            object.__setattr__(self, "dgx_model", v)
        if "dgx_model_judge" not in explicit and (v := os.environ.get("DGX_MODEL_JUDGE")):
            object.__setattr__(self, "dgx_model_judge", v)
        if "dgx_model_final_judge" not in explicit and (v := os.environ.get("DGX_MODEL_FINAL_JUDGE")):
            object.__setattr__(self, "dgx_model_final_judge", v)

        if "default_strength" not in explicit and (v := os.environ.get("CLEANROOM_STRENGTH")):
            object.__setattr__(self, "default_strength", v)
        if "auto_apply" not in explicit:
            aa = os.environ.get("CLEANROOM_AUTO_APPLY", "").strip().lower()
            if aa in ("1", "true", "yes", "on"):
                object.__setattr__(self, "auto_apply", True)
        return self

    # ── Effective LLM accessors ──
    @property
    def is_dgx(self) -> bool:
        return self.llm_provider == "dgx"

    @property
    def llm_base_url(self) -> str:
        return self.dgx_base_url if self.is_dgx else self.omlx_base_url

    @property
    def llm_api_key(self) -> str:
        return self.dgx_api_key if self.is_dgx else self.omlx_api_key

    @property
    def llm_model(self) -> str:
        return self.dgx_model if self.is_dgx else self.omlx_model

    @property
    def llm_model_judge(self) -> str:
        return self.dgx_model_judge if self.is_dgx else self.omlx_model_judge

    @property
    def llm_model_final_judge(self) -> str:
        return self.dgx_model_final_judge if self.is_dgx else self.omlx_model_final_judge

    @property
    def llm_timeout(self) -> float:
        return self.dgx_timeout if self.is_dgx else self.omlx_timeout

    @property
    def llm_max_tokens(self) -> int:
        return self.dgx_max_tokens if self.is_dgx else self.omlx_max_tokens

    @property
    def llm_enable_thinking(self) -> bool:
        return self.is_dgx and self.dgx_enable_thinking

    @property
    def llm_use_responses_api(self) -> bool:
        return (not self.is_dgx) and self.omlx_use_responses_api

    def to_safe_dict(self) -> dict[str, Any]:
        """Return a serializable dict with secrets redacted."""
        return {
            "workspace": str(self.workspace),
            "output_dir": str(self.output_dir),
            "run_ledger_path": str(self.run_ledger_path),
            "omlx_base_url": self.omlx_base_url,
            "omlx_model": self.omlx_model,
            "omlx_model_judge": self.omlx_model_judge,
            "llm_provider": self.llm_provider,
            "default_strength": self.default_strength,
            "max_judge_iterations": self.max_judge_iterations,
            "judge_score_threshold": self.judge_score_threshold,
            "auto_apply": bool(self.auto_apply),
        }


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings