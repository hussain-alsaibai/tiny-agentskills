# tiny-agentskills

> **Discover, render, and run Agent Skills — Anthropic-style — in one stdlib-only file.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](tiny_agentskills.py)
[![Part of the tiny-* ecosystem](https://img.shields.io/badge/tiny--*-ecosystem-purple.svg)](#ecosystem)
[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](tiny_agentskills.py)

An **Agent Skill** is a directory containing a `SKILL.md` (YAML frontmatter + instructions),
optional `scripts/`, `references/`, and `assets/`. Skills let agents load community-built
capabilities — PDFs, browser automation, MCP bundles, code review patterns — without
hardcoding them.

This module is the runtime: discover, render, search, and execute skills. Drop it in
your MCP server, your CLI, your web framework, or your agent harness.

## Why this exists

2026 is the year skills broke out: Anthropic's Skills pattern, OpenAI's skills manifest,
hexstrike-ai's MCP skill bundles, OpenAI's `openai/skills` repo. Everyone agrees on the
shape (`SKILL.md` + scripts + references), nobody agrees on a runtime.

`tiny-agentskills` is the runtime everyone can adopt without dragging in `pyyaml`,
`click`, `tomllib`, or `python-frontmatter`. **One file, zero deps, <500 LOC, MIT.**

## ✨ Features

- **Discover** skills across one or more root directories (env var, CLI flag, or hard-coded).
- **Parse** SKILL.md frontmatter in a tiny YAML subset (scalars + lists) — no PyYAML.
- **Render** an index in `text`, `md`, or `json` for any agent system prompt.
- **Search** by name, tag, or free-text description.
- **Run** skill scripts safely with a timeout, isolated env, and structured result.
- **MCP-ready** — drop `SkillRegistry` into any MCP server in 5 lines.
- **Tiny** — single file, ~17 KB, zero dependencies.

## Quick Start

### CLI

```bash
# Index default ./skills directory
python -m tiny_agentskills --list

# Render for a system prompt
python -m tiny_agentskills --list --format md

# Show a skill's body
python -m tiny_agentskills --show pdf

# Run a skill's script
python -m tiny_agentskills --run pdf extract --file foo.pdf
```

### Programmatic

```python
from tiny_agentskills import SkillRegistry

reg = SkillRegistry(["./skills", "~/.agents/skills"])
reg.discover()

# Build a system-prompt section
prompt_section = reg.render_index(fmt="md")

# Search
pdf_skills = reg.search("pdf")

# Run
result = reg.run("pdf", "extract", file="foo.pdf")
print(result["stdout"])
```

### Embedded MCP server (5 lines)

```python
from tiny_agentskills import SkillRegistry
from fastmcp import FastMCP   # or tiny-mcp if you'd rather stay zero-dep

mcp = FastMCP("skills")
reg = SkillRegistry(["./skills"])
reg.discover()

@mcp.tool()
def list_skills() -> str:
    return reg.render_index(fmt="md")
```

## Skill directory layout

A skill is any folder that contains a `SKILL.md`:

```
skills/
├── pdf/
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── extract.py
│   │   └── render.sh
│   ├── references/
│   │   └── pdf-spec.md
│   └── assets/
│       └── fonts.json
├── browser/
│   ├── SKILL.md
│   └── scripts/
│       └── navigate.py
└── code-review/
    ├── SKILL.md
    └── references/
        └── style-guide.md
```

`SKILL.md` looks like this:

```markdown
---
name: pdf
description: Render and extract text from PDF files.
version: 0.1.0
author: Alice
tags:
  - documents
  - extraction
---

# PDF Skill

Use this skill when the user wants to extract text, render pages, or
manipulate PDFs.

## Workflow

1. Identify the input PDF.
2. Run `extract.py --file <path>` to get raw text.
3. If the user wants summary, run `summarize.py --file <path>`.
```

## API

### `SkillRegistry(roots, *, max_description_chars=1024, allow_exec=True)`

| Method | Description |
|--------|-------------|
| `discover()` | Walk every root, return list of newly found skill names. |
| `list()` | Return a list of `dict` skill metadata (name, description, version, paths). |
| `names()` | Return sorted skill names. |
| `get(name)` | Return a `Skill` record or `None`. |
| `search(query)` | Substring match on name + description + tags. |
| `render_index(fmt)` | `"text"`, `"md"`, or `"json"` — paste into any system prompt. |
| `render_body(name, max_chars=8192)` | Print the full SKILL.md body + reference list. |
| `run(skill, script, *args, timeout=60, env=None)` | Run a script and return `{"ok", "returncode", "stdout", "stderr"}`. |

### Environment

- `TINY_AGENTSKILLS_PATH` — colon-separated default roots (overridden by `--root`).

## Ecosystem

Tiny Agent Skills is part of the [tiny-* ecosystem](https://github.com/hussain-alsaibai/dev-masterkit):

- `tiny-mcp` — zero-dep MCP server (use this to expose skills via MCP).
- `tiny-agent` — zero-dep agent framework (auto-load skills into system prompts).
- `tiny-cli` — zero-dep CLI builder (wrap `tiny-agentskills` as a subcommand).
- `tiny-validator` — validate frontmatter schemas before publishing.
- `tiny-context` — fit skill descriptions into tight context windows.

## Testing

```bash
python -m unittest test_agentskills.py -v
```

The test suite covers frontmatter parsing, discovery, render formats, search,
and script execution with a stubbed skill directory.

## License

MIT
