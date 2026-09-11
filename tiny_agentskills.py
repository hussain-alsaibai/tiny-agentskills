"""
tiny-agentskills — single-file, zero-dependency runtime for Agent Skills.

What is an Agent Skill?
----------------------
An "Agent Skill" (à la Anthropic's Skills pattern, OpenAI's skills manifest,
hexstrike-ai's MCP skill bundles) is a directory of:

  SKILL.md          Frontmatter + natural-language instructions for the model
  scripts/*.py      Executable helpers the agent can call
  references/*.md   Long-form reference docs the agent can load on demand
  assets/*.json     Static resources (prompts, schemas, fixtures)

This module is the runtime layer: discover skills on disk, parse their
frontmatter, render descriptions, and route script invocations — all without
any external dependency.

The goal: let any agent (Claude Code, Codex, OpenClaw, a custom harness) load
and execute a community-built skill the same way, with one well-known API.

Example
-------
    >>> from tiny_agentskills import SkillRegistry
    >>> reg = SkillRegistry(["./skills/", "~/.agents/skills/"])
    >>> reg.discover()
    >>> reg.list()
    [{'name': 'pdf', 'description': 'Render and extract PDF content...'}]
    >>> reg.run('pdf', 'extract', file='foo.pdf')

Designed to be embedded inside an MCP server, a CLI, or a web framework.
The whole file is <500 LOC.

Author: OpenClaw / hussain-alsaibai
License: MIT
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# Frontmatter parsing (YAML-ish, stdlib only)
# ---------------------------------------------------------------------------

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z_][\w\-]*):\s*(.*?)\s*$")
_LIST_RE = re.compile(r"^\s*-\s*(.*?)\s*$")


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Parse a YAML subset: scalars and lists of scalars.

    Enough for typical SKILL.md frontmatter like:

        ---
        name: pdf
        description: Render and extract PDF content...
        version: 0.1.0
        tags:
          - documents
          - extraction
        ---
    """
    m = _FRONT_RE.match(raw)
    if not m:
        return {}, raw
    head, body = m.group(1), m.group(2)
    meta: dict[str, Any] = {}
    current_key: str | None = None
    for line in head.splitlines():
        if not line.strip():
            continue
        kv = _KV_RE.match(line)
        if kv:
            current_key = kv.group(1)
            value = kv.group(2)
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                meta[current_key] = [
                    _coerce(x.strip()) for x in inner.split(",") if x.strip()
                ]
            else:
                meta[current_key] = _coerce(value)
            continue
        li = _LIST_RE.match(line)
        if li and current_key:
            if current_key not in meta or not isinstance(meta[current_key], list):
                meta[current_key] = []
            meta[current_key].append(_coerce(li.group(1).strip().strip('"').strip("'")))
    return meta, body


def _coerce(s: str) -> Any:
    if s in ("true", "True", "yes"):
        return True
    if s in ("false", "False", "no"):
        return False
    if s in ("null", "None", "~"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s.strip('"').strip("'")


# ---------------------------------------------------------------------------
# Skill record
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    """A discovered agent skill on disk."""

    name: str
    path: Path
    description: str = ""
    version: str = "0.0.0"
    tags: list[str] = field(default_factory=list)
    author: str = ""
    body: str = ""
    scripts: dict[str, Path] = field(default_factory=dict)
    references: list[Path] = field(default_factory=list)
    assets: list[Path] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "tags": list(self.tags),
            "author": self.author,
            "path": str(self.path),
            "scripts": sorted(self.scripts),
            "references": [str(p) for p in self.references],
            "assets": [str(p) for p in self.assets],
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SkillRegistry:
    """Discovers, indexes, and runs Agent Skills from one or more roots."""

    def __init__(
        self,
        roots: Iterable[str | os.PathLike] | None = None,
        *,
        max_description_chars: int = 1024,
        allow_exec: bool = True,
        env_path: bool = True,
    ) -> None:
        self.roots: list[Path] = []
        if env_path:
            env_value = os.environ.get("TINY_AGENTSKILLS_PATH", "")
            for token in env_value.split(os.pathsep):
                if token:
                    p = Path(os.path.expanduser(token)).resolve()
                    if p.exists():
                        self.roots.append(p)
        for r in roots or []:
            p = Path(os.path.expanduser(str(r))).resolve()
            if p.exists():
                self.roots.append(p)
        self.max_description_chars = max_description_chars
        self.allow_exec = allow_exec
        self._skills: dict[str, Skill] = {}

    # ---- discovery --------------------------------------------------------

    def discover(self) -> list[str]:
        """Walk every root, find subdirectories containing SKILL.md.

        Returns the list of newly discovered skill names.
        """
        seen: set[str] = set()
        for root in self.roots:
            for path in [root, *root.rglob("*")]:
                if not path.is_dir():
                    continue
                md = path / "SKILL.md"
                if not md.is_file():
                    continue
                skill = self._load_skill(path, md)
                if skill is None:
                    continue
                key = skill.name
                if key in seen:
                    continue  # first-wins on duplicates across roots
                seen.add(key)
                self._skills[key] = skill
        return sorted(self._skills)

    def _load_skill(self, path: Path, md: Path) -> Skill | None:
        try:
            raw = md.read_text(encoding="utf-8")
        except OSError:
            return None
        meta, body = _parse_frontmatter(raw)
        if not meta.get("name"):
            return None
        desc = str(meta.get("description", ""))
        if desc:
            desc = textwrap.shorten(desc, width=self.max_description_chars)
        skill = Skill(
            name=str(meta["name"]),
            path=path,
            description=desc,
            version=str(meta.get("version", "0.0.0")),
            tags=[str(t) for t in (meta.get("tags") or []) if t],
            author=str(meta.get("author", "")),
            body=body.strip(),
        )
        scripts_dir = path / "scripts"
        if scripts_dir.is_dir():
            for s in sorted(scripts_dir.iterdir()):
                if s.is_file() and _is_runnable(s, require_exec=False):
                    skill.scripts[s.stem] = s
        refs_dir = path / "references"
        if refs_dir.is_dir():
            skill.references = sorted(refs_dir.glob("*.md"))
        assets_dir = path / "assets"
        if assets_dir.is_dir():
            skill.assets = sorted(p for p in assets_dir.rglob("*") if p.is_file())
        return skill

    # ---- query ------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in sorted(self._skills.values(), key=lambda s: s.name)]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def search(self, query: str) -> list[Skill]:
        q = query.lower()
        out: list[Skill] = []
        for s in self._skills.values():
            hay = " ".join([s.name, s.description, *s.tags]).lower()
            if q in hay:
                out.append(s)
        return sorted(out, key=lambda s: s.name)

    # ---- rendering --------------------------------------------------------

    def render_index(self, fmt: str = "text") -> str:
        """Render an index suitable for pasting into an agent system prompt."""
        skills = self.list()
        if fmt == "json":
            return json.dumps(skills, indent=2)
        if fmt == "md":
            lines = ["# Available Skills", ""]
            for s in skills:
                lines.append(f"- **{s['name']}** (v{s['version']}): {s['description']}")
            return "\n".join(lines)
        # default text
        lines = [f"{len(skills)} skills available:"]
        for s in skills:
            lines.append(f"  - {s['name']} v{s['version']}: {s['description']}")
        return "\n".join(lines)

    def render_body(self, name: str, max_chars: int = 8192) -> str:
        s = self.get(name)
        if not s:
            raise KeyError(name)
        out = [f"# {s.name} (v{s.version})", "", s.description, "", s.body]
        if s.references:
            out.append("\n## References")
            for r in s.references:
                out.append(f"- {r.name}")
        text = "\n".join(out)
        if len(text) <= max_chars:
            return text
        if max_chars < 20:
            return text[:max_chars]
        return text[: max_chars - 12] + "\n…[truncated]"

    # ---- execution --------------------------------------------------------

    def run(
        self,
        skill_name: str,
        script: str,
        *args: str,
        timeout: int = 60,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run a skill's script with the given args.

        Returns a dict with: ok, returncode, stdout, stderr.
        """
        if not self.allow_exec:
            raise PermissionError("script execution disabled (allow_exec=False)")
        skill = self.get(skill_name)
        if not skill:
            raise KeyError(f"unknown skill: {skill_name}")
        path = skill.scripts.get(script)
        if not path:
            raise KeyError(
                f"skill '{skill_name}' has no script '{script}'. "
                f"available: {sorted(skill.scripts)}"
            )
        cmd = [_interpreter_for(path), str(path), *args]
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        proc = subprocess.run(  # noqa: S603 — args trusted (operator input)
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=full_env,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_RUNNABLE_SUFFIXES = {".py", ".sh", ".js", ".ts", ""}
_RUNNABLE_NAMES = {"Dockerfile", "Makefile"}


def _is_runnable(path: Path, require_exec: bool = True) -> bool:
    if path.name in _RUNNABLE_NAMES:
        return True
    if path.suffix == ".py":
        # .py always runnable via sys.executable
        return True
    return path.suffix in _RUNNABLE_SUFFIXES and (
        not require_exec or os.access(path, os.X_OK)
    )


def _interpreter_for(path: Path) -> str:
    if path.suffix == ".py":
        return sys.executable or "python3"
    if path.suffix == ".sh":
        return "/bin/sh"
    if path.suffix in (".js", ".ts"):
        # best-effort; node/tsx may not be installed everywhere
        return shutil.which("node") or "node"
    return ""  # shebang-driven


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser():
    import argparse

    p = argparse.ArgumentParser(
        prog="tiny-agentskills",
        description="Discover and run Agent Skills (Anthropic-style).",
    )
    p.add_argument(
        "--root",
        action="append",
        default=[],
        help="A directory to scan (can be repeated). "
             "Also reads $TINY_AGENTSKILLS_PATH (colon-separated).",
    )
    p.add_argument("--list", action="store_true", help="List discovered skills.")
    p.add_argument("--format", choices=["text", "md", "json"], default="text")
    p.add_argument("--show", metavar="SKILL", help="Print the body of a skill.")
    p.add_argument("--run", nargs=argparse.REMAINDER, metavar="SKILL SCRIPT ...",
                   help="Run a skill's script with the given args.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    roots = list(args.root)
    env_path = os.environ.get("TINY_AGENTSKILLS_PATH", "")
    if env_path:
        roots.extend([p for p in env_path.split(":") if p])
    if not roots:
        roots.append("./skills")
    reg = SkillRegistry(roots)
    reg.discover()
    if args.list:
        print(reg.render_index(args.format))
        return 0
    if args.show:
        print(reg.render_body(args.show))
        return 0
    if args.run:
        if len(args.run) < 2:
            print("usage: tiny-agentskills --run SKILL SCRIPT [ARGS...]", file=sys.stderr)
            return 2
        skill, script, *rest = args.run
        result = reg.run(skill, script, *rest)
        sys.stdout.write(result["stdout"])
        sys.stderr.write(result["stderr"])
        return 0 if result["ok"] else result["returncode"] or 1
    print(reg.render_index(args.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
