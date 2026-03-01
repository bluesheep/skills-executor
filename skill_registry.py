"""
Skill Registry: discovers skills from filesystem, provides progressive disclosure.

The key insight: the LLM starts by seeing only skill metadata (name + description,
~100 tokens each). Full skill content is loaded ON DEMAND via tool calls. Supporting
files (reference docs, scripts) are only read when the LLM explicitly asks for them.

This means you can have 20+ skills registered without blowing up your context window.
"""

from dataclasses import dataclass, field
from pathlib import Path
import yaml
import logging

logger = logging.getLogger(__name__)


@dataclass
class SkillMetadata:
    """Lightweight metadata - this is ALL the LLM sees initially."""
    name: str
    description: str
    path: Path  # absolute path to the skill directory
    tags: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    author: str = ""

    def to_catalog_entry(self) -> str:
        """Format for the LLM's skill catalog (injected into system prompt)."""
        tags_str = f" [{', '.join(self.tags)}]" if self.tags else ""
        return f"- **{self.name}**{tags_str}: {self.description}"


@dataclass
class SkillContent:
    """Full skill content - loaded only when the LLM requests it."""
    metadata: SkillMetadata
    instructions: str  # the markdown body of SKILL.md
    supporting_files: dict[str, str]  # relative_path -> description/preview
    scripts: list[str]  # relative paths to executable scripts


class SkillRegistry:
    """
    Scans directories for SKILL.md files and provides progressive disclosure.

    Level 0: Catalog (name + description for all skills) → always in context
    Level 1: Full SKILL.md body for a specific skill → loaded via tool call
    Level 2: Supporting files (reference.md, scripts/) → loaded via tool call
    """

    def __init__(self, skill_paths: list[str]):
        self._skills: dict[str, SkillMetadata] = {}
        self._content_cache: dict[str, SkillContent] = {}
        self._scan_paths = [Path(p) for p in skill_paths]

    def discover(self) -> int:
        """Scan all configured paths for SKILL.md files. Returns count found."""
        self._skills.clear()
        self._content_cache.clear()

        for base_path in self._scan_paths:
            if not base_path.exists():
                continue

            # Each subdirectory with a SKILL.md is a skill
            for skill_dir in sorted(base_path.iterdir()):
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.is_file():
                    continue

                try:
                    metadata = self._parse_metadata(skill_file, skill_dir)
                    if metadata.name in self._skills:
                        logger.warning(
                            f"Duplicate skill '{metadata.name}' at {skill_dir}, "
                            f"keeping first from {self._skills[metadata.name].path}"
                        )
                        continue
                    self._skills[metadata.name] = metadata
                    logger.info(f"Discovered skill: {metadata.name} at {skill_dir}")
                except Exception as e:
                    logger.error(f"Failed to parse {skill_file}: {e}")

        logger.info(f"Discovered {len(self._skills)} skills total")
        return len(self._skills)

    def _parse_metadata(self, skill_file: Path, skill_dir: Path) -> SkillMetadata:
        """Parse ONLY the YAML frontmatter - do not read the full body yet."""
        text = skill_file.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError(f"No YAML frontmatter found in {skill_file}")

        frontmatter = yaml.safe_load(parts[1])
        if not frontmatter or "name" not in frontmatter:
            raise ValueError(f"Missing 'name' in frontmatter of {skill_file}")

        return SkillMetadata(
            name=frontmatter["name"],
            description=frontmatter.get("description", "No description provided."),
            path=skill_dir.resolve(),
            tags=frontmatter.get("tags", []),
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", ""),
        )

    # ─── Level 0: Catalog ────────────────────────────────────────────────

    def get_catalog(self) -> str:
        """
        Returns a compact skill catalog for the system prompt.
        This is the ONLY skill info the LLM gets upfront.
        """
        if not self._skills:
            return "No skills are currently available."

        lines = ["## Available Skills", ""]
        for meta in self._skills.values():
            lines.append(meta.to_catalog_entry())
        lines.append("")
        lines.append(
            "To use a skill, call `load_skill` with the skill name. "
            "This will load the full instructions for that skill."
        )
        return "\n".join(lines)

    def list_skills(self) -> list[dict]:
        """Return skill metadata as dicts (for tool responses)."""
        return [
            {
                "name": m.name,
                "description": m.description,
                "tags": m.tags,
                "has_scripts": any(
                    (m.path / "scripts").iterdir()
                ) if (m.path / "scripts").is_dir() else False,
                "supporting_files": [
                    f.name for f in m.path.iterdir()
                    if f.is_file() and f.name != "SKILL.md" and f.suffix in (".md", ".txt", ".json")
                ],
            }
            for m in self._skills.values()
        ]

    # ─── Level 1: Full SKILL.md ──────────────────────────────────────────

    def load_skill(self, skill_name: str) -> SkillContent | None:
        """
        Load the full SKILL.md content for a skill. This is called by the LLM
        via a tool when it decides a skill is relevant.
        """
        if skill_name in self._content_cache:
            return self._content_cache[skill_name]

        meta = self._skills.get(skill_name)
        if meta is None:
            return None

        skill_file = meta.path / "SKILL.md"
        text = skill_file.read_text(encoding="utf-8")

        # Extract the markdown body (after frontmatter)
        parts = text.split("---", 2)
        instructions = parts[2].strip() if len(parts) >= 3 else text

        # Inventory supporting files (don't read them yet)
        supporting_files = {}
        for f in sorted(meta.path.iterdir()):
            if f.is_file() and f.name != "SKILL.md" and f.name != "LICENSE.txt":
                if f.suffix in (".md", ".txt", ".json", ".yaml", ".yml"):
                    # Read first line as preview
                    first_line = f.read_text(encoding="utf-8").split("\n")[0][:100]
                    supporting_files[f.name] = first_line

        # Inventory scripts
        scripts = []
        scripts_dir = meta.path / "scripts"
        if scripts_dir.is_dir():
            for script in sorted(scripts_dir.rglob("*")):
                if script.is_file() and script.suffix in (".py", ".sh", ".js", ".ts"):
                    scripts.append(str(script.relative_to(meta.path)))

        content = SkillContent(
            metadata=meta,
            instructions=instructions,
            supporting_files=supporting_files,
            scripts=scripts,
        )
        self._content_cache[skill_name] = content
        return content

    # ─── Level 2: Supporting files ───────────────────────────────────────

    def read_skill_file(self, skill_name: str, relative_path: str) -> str | None:
        """
        Read a supporting file from a skill's directory. Called by the LLM
        when it needs reference docs, examples, or script source.
        """
        meta = self._skills.get(skill_name)
        if meta is None:
            return None

        file_path = (meta.path / relative_path).resolve()

        # Security: ensure the resolved path is still under the skill directory
        if not str(file_path).startswith(str(meta.path.resolve())):
            logger.warning(f"Path traversal attempt blocked: {relative_path}")
            return None

        if not file_path.is_file():
            return None

        try:
            return file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"[Binary file: {file_path.name}, {file_path.stat().st_size} bytes]"

    def get_skill_dir(self, skill_name: str) -> Path | None:
        """Get the absolute path to a skill's directory (for sandbox mounting)."""
        meta = self._skills.get(skill_name)
        return meta.path if meta else None
