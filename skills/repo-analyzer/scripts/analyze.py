#!/usr/bin/env python3
"""
Repository analysis script - bundled with the repo-analyzer skill.

This runs INSIDE the sandbox. The LLM reads the SKILL.md instructions,
which tell it to execute this script, then uses the JSON output to
write a human-readable report.

Usage:
    python analyze.py /input/ --output /output/report.json
"""

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path


LANGUAGE_EXTENSIONS = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".java": "Java", ".cs": "C#", ".go": "Go", ".rs": "Rust",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".kt": "Kotlin",
    ".c": "C", ".cpp": "C++", ".h": "C/C++ Header",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
    ".md": "Markdown", ".txt": "Text",
    ".sh": "Shell", ".bash": "Shell",
    ".sql": "SQL", ".xml": "XML",
}

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".next", ".nuxt", "target", "bin", "obj",
}

PACKAGE_FILES = {
    "requirements.txt": "pip",
    "Pipfile": "pipenv",
    "pyproject.toml": "poetry/pip",
    "package.json": "npm",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
    "Cargo.toml": "cargo",
    "go.mod": "go modules",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "Gemfile": "bundler",
    "composer.json": "composer",
}


def count_lines(filepath: Path) -> int:
    """Count lines in a file, handling encoding errors."""
    try:
        return sum(1 for _ in open(filepath, encoding="utf-8", errors="replace"))
    except (OSError, UnicodeDecodeError):
        return 0


def analyze_directory(root: Path) -> dict:
    """Analyze a directory and return metrics."""
    language_files = Counter()
    language_lines = Counter()
    total_files = 0
    total_lines = 0
    total_size = 0
    largest_files = []
    package_managers = []
    dir_structure = defaultdict(int)

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip ignored directories
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]

        rel_dir = os.path.relpath(dirpath, root)
        depth = rel_dir.count(os.sep) if rel_dir != "." else 0

        for filename in filenames:
            filepath = Path(dirpath) / filename
            if not filepath.is_file():
                continue

            total_files += 1
            size = filepath.stat().st_size
            total_size += size
            ext = filepath.suffix.lower()

            # Track language
            lang = LANGUAGE_EXTENSIONS.get(ext, "Other")
            language_files[lang] += 1
            lines = count_lines(filepath)
            language_lines[lang] += lines
            total_lines += lines

            # Track largest files
            largest_files.append((str(filepath.relative_to(root)), size, lines))

            # Track package managers
            if filename in PACKAGE_FILES:
                package_managers.append({
                    "file": filename,
                    "manager": PACKAGE_FILES[filename],
                    "path": str(filepath.relative_to(root)),
                })

            # Track directory structure (top 2 levels)
            if depth <= 2:
                dir_structure[rel_dir] += 1

    # Sort largest files
    largest_files.sort(key=lambda x: x[1], reverse=True)

    return {
        "overview": {
            "total_files": total_files,
            "total_lines": total_lines,
            "total_size_bytes": total_size,
            "total_size_human": _human_size(total_size),
        },
        "languages": {
            "by_files": dict(language_files.most_common(20)),
            "by_lines": dict(language_lines.most_common(20)),
        },
        "structure": {
            "directories": dict(sorted(dir_structure.items())),
        },
        "dependencies": {
            "package_managers": package_managers,
        },
        "largest_files": [
            {"path": p, "size": s, "lines": l}
            for p, s, l in largest_files[:20]
        ],
    }


def _human_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def main():
    parser = argparse.ArgumentParser(description="Analyze a code repository")
    parser.add_argument("directory", help="Directory to analyze")
    parser.add_argument("--output", "-o", default="/output/report.json", help="Output JSON path")
    args = parser.parse_args()

    root = Path(args.directory)
    if not root.is_dir():
        print(f"Error: {args.directory} is not a directory")
        return 1

    print(f"Analyzing {root}...")
    result = analyze_directory(root)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Analysis complete: {result['overview']['total_files']} files, "
          f"{result['overview']['total_lines']} lines")
    print(f"Report written to {output_path}")

    # Also print summary to stdout for the LLM
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    exit(main())
