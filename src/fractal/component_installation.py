"""Recoverable installation of Fractal-generated platform component projections."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from fractal.adapters import smoke_adapter
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
        config_target = home / "config.toml"
        config_text = config_target.read_text(encoding="utf-8") if config_target.is_file() else None
        projected_config = (
            self._project_mcp_activation(config_text, registry)
            if config_text is not None
            else None
        )
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
        if projected_config is not None and projected_config != config_text:
            managed.append("config.toml")
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
        if "config.toml" in managed:
            config_target.write_text(projected_config, encoding="utf-8")

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
            "smoke": smoke,
        }
        state.mkdir(parents=True, exist_ok=True)
        (state / "install.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return record

    @staticmethod
    def _project_mcp_activation(config_text: str, registry: dict[str, Any]) -> str:
        """Project registered config-backed MCP activation without exposing secrets."""
        projected = config_text
        for component in registry["components"]:
            if component["kind"] != "mcp" or "codex" not in component["platforms"]:
                continue
            locator = component["source"]["locator"]
            if not locator.startswith("~/.codex/config.toml#mcp_servers."):
                continue
            name = locator.rsplit(".", 1)[-1]
            header = f"[mcp_servers.{name}]"
            header_match = re.search(
                rf"(?m)^\[mcp_servers\.{re.escape(name)}\]\s*$", projected
            )
            if header_match is None:
                if component["status"]["active"]:
                    raise ComponentInstallationError(
                        f"Registered active MCP is missing from Codex config: {name}"
                    )
                continue
            next_header = re.search(r"(?m)^\[", projected[header_match.end() :])
            section_end = (
                header_match.end() + next_header.start()
                if next_header is not None
                else len(projected)
            )
            body = projected[header_match.end() : section_end]
            desired = "true" if component["status"]["active"] else "false"
            enabled_match = re.search(r"(?m)^enabled\s*=\s*(?:true|false)\s*$", body)
            if enabled_match is None:
                replacement = f"{header}\nenabled = {desired}"
                projected = (
                    projected[: header_match.start()]
                    + replacement
                    + projected[header_match.end() :]
                )
            else:
                absolute_start = header_match.end() + enabled_match.start()
                absolute_end = header_match.end() + enabled_match.end()
                projected = (
                    projected[:absolute_start]
                    + f"enabled = {desired}"
                    + projected[absolute_end:]
                )
        return projected

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
