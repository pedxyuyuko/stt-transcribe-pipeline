from __future__ import annotations

import yaml
from pathlib import Path

from pydantic import ValidationError

from app.config.schema import AppConfig, PipelineConfig


class ConfigError(Exception):
    pass


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with open(path) as f:
            data: dict = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}")
    if data is None or not isinstance(data, dict):
        raise ConfigError(
            f"Config file {path} must contain a YAML mapping, got: {type(data)}"
        )
    return data


def load_app_config(config_dir: Path) -> AppConfig:
    path = config_dir / "config.yml"
    try:
        data = _load_yaml(path)
        return AppConfig(**data)
    except ConfigError:
        try:
            path_v1 = config_dir / "app.yaml"
            data_v1 = _load_yaml(path_v1)
            return AppConfig(**data_v1)
        except (ConfigError, ValidationError):
            pass
        raise
    except ValidationError as e:
        raise ConfigError(f"Invalid app config in {path}: {e}")


def load_presets(config_dir: Path) -> dict[str, PipelineConfig]:
    presets_dir = config_dir / "presets"
    if not presets_dir.exists() or not presets_dir.is_dir():
        raise ConfigError(f"Presets directory not found: {presets_dir}")

    presets: dict[str, PipelineConfig] = {}
    yaml_files = sorted(presets_dir.glob("*.yaml"))
    if not yaml_files:
        raise ConfigError(f"No preset YAML files found in {presets_dir}")

    for yaml_file in yaml_files:
        preset_name = yaml_file.stem
        try:
            data = _load_yaml(yaml_file)
            preset = PipelineConfig(**data)
            presets[preset_name] = preset
        except ConfigError:
            raise
        except ValidationError as e:
            raise ConfigError(f"Invalid preset in {yaml_file}: {e}")

    return presets


def load_all_configs(
    config_dir: Path,
) -> tuple[AppConfig, dict[str, PipelineConfig]]:
    app_config = load_app_config(config_dir)
    presets = load_presets(config_dir)

    if app_config.default_preset not in presets:
        raise ConfigError(
            f"default_preset '{app_config.default_preset}' not found. "
            f"Available presets: {list(presets.keys())}"
        )

    for preset_name, preset in presets.items():
        for block in preset.blocks:
            for task in block.tasks:
                model_field = task.model
                if "/" not in model_field:
                    if model_field not in app_config.model_groups:
                        raise ConfigError(
                            f"Preset '{preset_name}': task '{block.tag}.{task.tag}' references "
                            f"model_group '{model_field}' which does not exist in config"
                        )
                    for entry in app_config.model_groups[model_field]:
                        provider_id = entry.split("/")[0]
                        if provider_id not in app_config.providers:
                            raise ConfigError(
                                f"Preset '{preset_name}': model_group '{model_field}' entry '{entry}' "
                                f"references provider '{provider_id}' which does not exist in config"
                            )
                else:
                    provider_id = model_field.split("/")[0]
                    if provider_id not in app_config.providers:
                        raise ConfigError(
                            f"Preset '{preset_name}': task '{block.tag}.{task.tag}' references "
                            f"provider '{provider_id}' which does not exist in config"
                        )

    return app_config, presets
