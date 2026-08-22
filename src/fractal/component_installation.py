"""Recoverable installation of Fractal-generated platform component projections."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from fractal.adapters import smoke_adapter
from fractal.codex_app_server import CodexAppServerClient, audit_codex_config_projection
from fractal.component_governance import active_components, load_component_registry


class ComponentInstallationError(RuntimeError):
    """Raised when a governed projection cannot be installed or restored safely."""


class CodexComponentInstaller:
    """Install only a verified Codex candidate and quarantine unmanaged extras."""

    def __init__(self, state_root: Path, quarantine_root: Path) -> None:
        self.state_root = Path(state_root)
        self.quarantine_root = Path(quarantine_root)

    def install(self, built: Path, home: Path) -> dict[str, Any]:
        """Switch the generated entrypoint, Hooks, roles, and Skills recoverably."""
        built = Path(built).resolve(strict=True)
        home = Path(home).expanduser().resolve()
        smoke = smoke_adapter(built)
        if smoke["platform"] != "codex":
            raise ComponentInstallationError("Codex installer received another platform")
        registry_path = built / "fractal" / "component-registry.json"
        if not registry_path.is_file():
            raise ComponentInstallationError("Candidate lacks a universal component registry")
        registry = load_component_registry(registry_path)
        live_codex_home = Path("~/.codex").expanduser().resolve()
        if home == live_codex_home:
            with CodexAppServerClient() as client:
                config_projection = audit_codex_config_projection(
                    client, registry, cwd=Path.cwd()
                )
            if not config_projection["clean"]:
                raise ComponentInstallationError(
                    "Codex MCP config does not match the candidate. Use the governed "
                    "config-apply route before installation: "
                    + ", ".join(config_projection["mismatched"])
                )
        else:
            config_projection = {
                "record_type": "codex-config-projection-audit",
                "clean": None,
                "claim_level": "staged-non-live-home",
                "secret_values_included": False,
            }
        install_id = f"component-install-{uuid.uuid4()}"
        state = self.state_root / install_id
        backup = state / "backup"
        quarantine = self.quarantine_root / install_id
        home.mkdir(parents=True, exist_ok=True)

        links: dict[str, Path] = {
            "AGENTS.md": built / "AGENTS.md",
            "fractal": built / "fractal",
        }
        for role in sorted((built / "agents").glob("*.toml")):
            links[f"agents/{role.name}"] = role
        generated_role_names = {
            Path(relative).name for relative in links if relative.startswith("agents/")
        }
        generated_skill_names = set()
        for component in active_components(registry, "codex"):
            projection = component["projection"]
            if component["kind"] != "skill" or projection["mode"] != "generated-copy":
                continue
            name = Path(projection["target"]).name
            generated_skill_names.add(name)
            source = built / "skills" / name
            if not source.is_dir():
                raise ComponentInstallationError(f"Candidate Skill is missing: {name}")
            links[f"skills/{name}"] = source

        previous: dict[str, dict[str, str]] = {}
        managed = [*sorted(links), "hooks.json"]
        for relative in managed:
            target = home / relative
            previous[relative] = self._preserve(target, backup / relative)

        quarantined = []
        skills_root = home / "skills"
        skills_root.mkdir(parents=True, exist_ok=True)
        for path in sorted(skills_root.iterdir()):
            if path.name == ".system" or path.name in generated_skill_names:
                continue
            destination = quarantine / "skills" / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            quarantined.append({"relative": f"skills/{path.name}", "quarantine": str(destination)})
        agents_root = home / "agents"
        agents_root.mkdir(parents=True, exist_ok=True)
        for path in sorted(agents_root.iterdir()):
            if path.name in generated_role_names:
                continue
            destination = quarantine / "agents" / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            quarantined.append({"relative": f"agents/{path.name}", "quarantine": str(destination)})

        for relative, source in links.items():
            target = home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
        hooks_target = home / "hooks.json"
        shutil.copy2(built / "hooks.json", hooks_target)
        record = {
            "record_type": "codex-component-install",
            "record_version": 1,
            "install_id": install_id,
            "candidate": str(built),
            "home": str(home),
            "managed": managed,
            "previous": previous,
            "quarantined": quarantined,
            "persistent_system_version_activated": False,
            "config_projection": config_projection,
            "smoke": smoke,
        }
        state.mkdir(parents=True, exist_ok=True)
        (state / "install.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return record

    def restore(self, install_id: str) -> dict[str, Any]:
        """Restore the exact pre-install paths and quarantined extras."""
        state = self.state_root / install_id
        record_path = state / "install.json"
        if not record_path.is_file():
            raise ComponentInstallationError(f"Unknown install id: {install_id}")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        home = Path(record["home"])
        backup = state / "backup"
        restored = []
        for relative in reversed(record["managed"]):
            target = home / relative
            self._remove_installed(target)
            descriptor = record["previous"][relative]
            kind = descriptor["kind"]
            if kind == "absent":
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind == "symlink":
                target.symlink_to(descriptor["target"])
            else:
                preserved = backup / relative
                shutil.move(str(preserved), str(target))
            restored.append(relative)
        restored_quarantine = []
        for item in record["quarantined"]:
            target = home / item["relative"]
            if target.exists() or target.is_symlink():
                raise ComponentInstallationError(
                    f"Restore target unexpectedly exists: {item['relative']}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(item["quarantine"], str(target))
            restored_quarantine.append(item["relative"])
        return {
            "install_id": install_id,
            "restored": sorted(restored),
            "restored_quarantine": sorted(restored_quarantine),
        }

    @staticmethod
    def _preserve(target: Path, backup: Path) -> dict[str, str]:
        if target.is_symlink():
            descriptor = {"kind": "symlink", "target": str(target.readlink())}
            target.unlink()
            return descriptor
        if not target.exists():
            return {"kind": "absent", "target": ""}
        backup.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            shutil.move(str(target), str(backup))
            return {"kind": "directory", "target": str(backup)}
        shutil.move(str(target), str(backup))
        return {"kind": "file", "target": str(backup)}

    @staticmethod
    def _remove_installed(target: Path) -> None:
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            raise ComponentInstallationError(
                f"Installed path became a directory and will not be removed: {target}"
            )


class ClaudeComponentInstaller(CodexComponentInstaller):
    """Install a verified Claude candidate while preserving non-Fractal settings."""

    def install(self, built: Path, home: Path) -> dict[str, Any]:
        """Switch Claude entrypoints, Hooks, roles, and approved Skills recoverably."""
        built = Path(built).resolve(strict=True)
        home = Path(home).expanduser().resolve()
        smoke = smoke_adapter(built)
        if smoke["platform"] != "claude":
            raise ComponentInstallationError("Claude installer received another platform")
        registry_path = built / "fractal" / "component-registry.json"
        if not registry_path.is_file():
            raise ComponentInstallationError("Candidate lacks a universal component registry")
        registry = load_component_registry(registry_path)
        settings_target = home / "settings.json"
        settings = (
            json.loads(settings_target.read_text(encoding="utf-8"))
            if settings_target.is_file()
            else {}
        )
        active = active_components(registry, "claude")
        active_plugins = {
            item["external_identifier"] for item in active if item["kind"] == "plugin"
        }
        registered_plugins = {
            item["external_identifier"]
            for item in registry["components"]
            if item["kind"] == "plugin" and "claude" in item["platforms"]
        }
        enabled_plugins = settings.get("enabledPlugins", {})
        if not isinstance(enabled_plugins, dict):
            raise ComponentInstallationError("Claude enabledPlugins must be an object")
        missing_plugins = sorted(
            identifier
            for identifier in active_plugins
            if not enabled_plugins.get(identifier, False)
        )
        if missing_plugins:
            raise ComponentInstallationError(
                "Registered Claude Plugin is not enabled: " + ", ".join(missing_plugins)
            )

        install_id = f"component-install-{uuid.uuid4()}"
        state = self.state_root / install_id
        backup = state / "backup"
        quarantine = self.quarantine_root / install_id
        home.mkdir(parents=True, exist_ok=True)
        links: dict[str, Path] = {
            "CLAUDE.md": built / "CLAUDE.md",
            "fractal": built / "fractal",
        }
        for role in sorted((built / "agents").glob("*.md")):
            links[f"agents/{role.name}"] = role
        generated_role_names = {
            Path(relative).name for relative in links if relative.startswith("agents/")
        }
        generated_skill_names: set[str] = set()
        for component in active:
            if component["kind"] != "skill":
                continue
            projection = component["projection"]
            if projection["mode"] == "generated-copy":
                name = Path(projection["target"]).name
                source = built / "skills" / name
            elif (
                projection["mode"] == "platform-reference"
                and component["source"]["kind"] == "local-snapshot"
            ):
                source = Path(projection["target"]).expanduser().resolve(strict=True)
                name = source.name
            else:
                continue
            if not source.is_dir():
                raise ComponentInstallationError(f"Candidate Skill is missing: {name}")
            generated_skill_names.add(name)
            links[f"skills/{name}"] = source

        previous: dict[str, dict[str, str]] = {}
        managed = [*sorted(links), "settings.json"]
        for relative in managed:
            previous[relative] = self._preserve(home / relative, backup / relative)

        quarantined: list[dict[str, str]] = []
        for directory, expected in (
            ("skills", generated_skill_names),
            ("agents", generated_role_names),
        ):
            root = home / directory
            root.mkdir(parents=True, exist_ok=True)
            for path in sorted(root.iterdir()):
                if path.name in expected:
                    continue
                destination = quarantine / directory / path.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(destination))
                quarantined.append(
                    {
                        "relative": f"{directory}/{path.name}",
                        "quarantine": str(destination),
                    }
                )

        for relative, source in links.items():
            target = home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
        fragment = json.loads((built / "settings.fragment.json").read_text(encoding="utf-8"))
        settings["hooks"] = fragment["hooks"]
        model_route = fragment.get("model_route")
        applied_model_route = None
        if model_route is not None:
            environment = settings.setdefault("env", {})
            if not isinstance(environment, dict):
                raise ComponentInstallationError("Claude settings env must be an object")
            if not any(
                environment.get(name) for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
            ):
                raise ComponentInstallationError(
                    "Claude model route requires an existing platform-owned credential"
                )
            for name in model_route["remove_environment"]:
                environment.pop(name, None)
            environment.update(model_route["environment"])
            environment["ANTHROPIC_BASE_URL"] = model_route["gateway"]["base_url"]
            settings["model"] = model_route["model"]
            settings["availableModels"] = model_route["available_models"]
            settings["enforceAvailableModels"] = model_route["enforce_available_models"]
            settings["modelOverrides"] = model_route["model_overrides"]
            applied_model_route = {
                "component_id": "claude-model-route",
                "gateway_component_id": model_route["gateway"]["component_id"],
                "model": model_route["model"],
                "model_overrides": model_route["model_overrides"],
                "platform_version": model_route["platform_version"],
            }
        settings["enabledPlugins"] = {
            identifier: identifier in active_plugins
            for identifier in sorted(registered_plugins)
        }
        settings_target.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        record = {
            "record_type": "claude-component-install",
            "record_version": 1,
            "install_id": install_id,
            "candidate": str(built),
            "home": str(home),
            "managed": managed,
            "previous": previous,
            "quarantined": quarantined,
            "disabled_unregistered_plugins": sorted(
                set(enabled_plugins).difference(registered_plugins)
            ),
            "applied_model_route": applied_model_route,
            "persistent_system_version_activated": False,
            "smoke": smoke,
        }
        state.mkdir(parents=True, exist_ok=True)
        (state / "install.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return record


class GeminiComponentInstaller(CodexComponentInstaller):
    """Install the generated Gemini entrypoint and Skills recoverably."""

    def install(self, built: Path, home: Path) -> dict[str, Any]:
        built = Path(built).resolve(strict=True)
        home = Path(home).expanduser().resolve()
        smoke = smoke_adapter(built)
        if smoke["platform"] != "gemini":
            raise ComponentInstallationError("Gemini installer received another platform")
        registry_path = built / "fractal" / "component-registry.json"
        if not registry_path.is_file():
            raise ComponentInstallationError("Candidate lacks a universal component registry")
        load_component_registry(registry_path)
        install_id = f"component-install-{uuid.uuid4()}"
        state = self.state_root / install_id
        backup = state / "backup"
        quarantine = self.quarantine_root / install_id
        links: dict[str, Path] = {
            "GEMINI.md": built / "GEMINI.md",
            "fractal": built / "fractal",
        }
        skills_source = built / "config" / "skills"
        generated_skill_names: set[str] = set()
        for skill in sorted(skills_source.iterdir()):
            if not skill.is_dir():
                continue
            generated_skill_names.add(skill.name)
            links[f"config/skills/{skill.name}"] = skill

        previous: dict[str, dict[str, str]] = {}
        managed = sorted(links)
        for relative in managed:
            previous[relative] = self._preserve(home / relative, backup / relative)

        quarantined: list[dict[str, str]] = []
        skills_root = home / "config" / "skills"
        skills_root.mkdir(parents=True, exist_ok=True)
        for path in sorted(skills_root.iterdir()):
            if path.name in generated_skill_names:
                continue
            destination = quarantine / "config" / "skills" / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            quarantined.append(
                {
                    "relative": f"config/skills/{path.name}",
                    "quarantine": str(destination),
                }
            )

        for relative, source in links.items():
            target = home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)

        record = {
            "record_type": "gemini-component-install",
            "record_version": 1,
            "install_id": install_id,
            "candidate": str(built),
            "home": str(home),
            "managed": managed,
            "previous": previous,
            "quarantined": quarantined,
            "persistent_system_version_activated": False,
            "smoke": smoke,
        }
        state.mkdir(parents=True, exist_ok=True)
        (state / "install.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return record
