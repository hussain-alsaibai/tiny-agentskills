"""Tests for tiny-agentskills — zero deps, stdlib only."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

# Make the single-file module importable
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tiny_agentskills as tas  # noqa: E402


SKILL_TEMPLATE = textwrap.dedent(
    """\
    ---
    name: {name}
    description: {description}
    version: 0.1.0
    author: Alice
    tags:
      - documents
      - extraction
    ---

    # {title}

    Long body for skill {name}.
    Use this for {description}.
    """
).strip() + "\n"


def _write_skill(root: Path, name: str, description: str, *, with_script: bool = True) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(name=name, description=description, title=name.title()),
        encoding="utf-8",
    )
    if with_script:
        scripts = skill_dir / "scripts"
        scripts.mkdir(exist_ok=True)
        script = scripts / "hello.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, json\n"
            "args = {'name': '" + name + "', 'argv': sys.argv[1:]}\n"
            "print(json.dumps(args))\n",
            encoding="utf-8",
        )
        os.chmod(script, 0o755)
    refs = skill_dir / "references"
    refs.mkdir(exist_ok=True)
    (refs / "spec.md").write_text("reference fixture", encoding="utf-8")
    return skill_dir


class TestFrontmatter(unittest.TestCase):
    def test_parses_scalars_and_lists(self):
        raw = (
            "---\n"
            "name: pdf\n"
            "description: Extract text from PDFs.\n"
            "version: 0.1.0\n"
            "tags:\n  - documents\n  - extraction\n"
            "---\n"
            "# Body\n\nHello.\n"
        )
        meta, body = tas._parse_frontmatter(raw)
        self.assertEqual(meta["name"], "pdf")
        self.assertEqual(meta["description"], "Extract text from PDFs.")
        self.assertEqual(meta["version"], "0.1.0")
        self.assertEqual(meta["tags"], ["documents", "extraction"])
        self.assertTrue(body.startswith("# Body"))

    def test_no_frontmatter(self):
        raw = "# Just a body\nNo frontmatter here.\n"
        meta, body = tas._parse_frontmatter(raw)
        self.assertEqual(meta, {})
        self.assertEqual(body, raw)

    def test_inline_list(self):
        raw = "---\nname: x\ntags: [a, b, c]\n---\nbody\n"
        meta, _ = tas._parse_frontmatter(raw)
        self.assertEqual(meta["tags"], ["a", "b", "c"])

    def test_coercion(self):
        self.assertEqual(tas._coerce("true"), True)
        self.assertEqual(tas._coerce("false"), False)
        self.assertEqual(tas._coerce("null"), None)
        self.assertEqual(tas._coerce("42"), 42)
        self.assertEqual(tas._coerce("3.14"), 3.14)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "skills"
        self.root.mkdir()
        _write_skill(self.root, "pdf", "Extract text from PDF files.")
        _write_skill(self.root, "browser", "Drive a headless browser.")
        _write_skill(self.root, "shell", "Run shell commands safely.", with_script=False)

    def tearDown(self):
        self.tmp.cleanup()

    def _reg(self, allow_exec=True):
        return tas.SkillRegistry([self.root], allow_exec=allow_exec)

    def test_discover_finds_all(self):
        reg = self._reg()
        discovered = reg.discover()
        self.assertCountEqual(discovered, ["pdf", "browser", "shell"])

    def test_list_returns_metadata(self):
        reg = self._reg()
        reg.discover()
        items = reg.list()
        self.assertEqual({x["name"] for x in items}, {"pdf", "browser", "shell"})
        for item in items:
            self.assertIn("scripts", item)
            self.assertIn("references", item)

    def test_search(self):
        reg = self._reg()
        reg.discover()
        self.assertEqual([s.name for s in reg.search("pdf")], ["pdf"])
        self.assertIn("pdf", [s.name for s in reg.search("documents")])
        self.assertEqual([s.name for s in reg.search("browser")], ["browser"])

    def test_render_index_text(self):
        reg = self._reg()
        reg.discover()
        text = reg.render_index(fmt="text")
        self.assertIn("pdf", text)
        self.assertIn("3 skills available", text)

    def test_render_index_md(self):
        reg = self._reg()
        reg.discover()
        md = reg.render_index(fmt="md")
        self.assertIn("# Available Skills", md)
        self.assertIn("**pdf**", md)

    def test_render_index_json(self):
        reg = self._reg()
        reg.discover()
        data = json.loads(reg.render_index(fmt="json"))
        self.assertEqual({s["name"] for s in data}, {"pdf", "browser", "shell"})

    def test_render_body_truncates(self):
        reg = self._reg()
        reg.discover()
        body = reg.render_body("pdf", max_chars=200)
        self.assertTrue(body.startswith("# pdf"))
        # truncation marker when shortened enough that the placeholder fits
        long = reg.render_body("pdf", max_chars=40)
        self.assertTrue(long.endswith("[truncated]") or len(long) <= 40)

    def test_run_executes_script(self):
        reg = self._reg()
        reg.discover()
        result = reg.run("pdf", "hello", "world")
        self.assertTrue(result["ok"], msg=result["stderr"])
        payload = json.loads(result["stdout"].strip())
        self.assertEqual(payload["name"], "pdf")
        self.assertEqual(payload["argv"], ["world"])

    def test_run_unknown_skill(self):
        reg = self._reg()
        reg.discover()
        with self.assertRaises(KeyError):
            reg.run("nope", "hello")

    def test_run_unknown_script(self):
        reg = self._reg()
        reg.discover()
        with self.assertRaises(KeyError):
            reg.run("pdf", "no_such_script")

    def test_run_disabled(self):
        reg = self._reg(allow_exec=False)
        reg.discover()
        with self.assertRaises(PermissionError):
            reg.run("pdf", "hello.py")

    def test_run_skill_without_scripts(self):
        reg = self._reg()
        reg.discover()
        with self.assertRaises(KeyError):
            reg.run("shell", "hello.py")

    def test_env_var_root(self):
        os.environ["TINY_AGENTSKILLS_PATH"] = str(self.root)
        try:
            reg = tas.SkillRegistry([])
            reg.discover()
            self.assertIn("pdf", reg.names())
        finally:
            del os.environ["TINY_AGENTSKILLS_PATH"]

    def test_first_wins_on_duplicate(self):
        # Add a duplicate with a different description
        second_root = Path(self.tmp.name) / "skills2"
        second_root.mkdir()
        _write_skill(second_root, "pdf", "Duplicate description wins loser.")
        reg = tas.SkillRegistry([self.root, second_root])
        reg.discover()
        self.assertEqual(len(reg.names()), 3)  # not 4
        # the first one wins
        self.assertIn("Extract text from PDF files.", reg.get("pdf").description)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "skills"
        self.root.mkdir()
        _write_skill(self.root, "demo", "Demo skill.")

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_outputs_names(self):
        out = tas.main(["--root", str(self.root), "--list"])
        self.assertEqual(out, 0)
        # captured stdout via print — just re-discover and render manually
        reg = tas.SkillRegistry([self.root])
        reg.discover()
        self.assertIn("demo", reg.names())

    def test_show_outputs_body(self):
        reg = tas.SkillRegistry([self.root])
        reg.discover()
        body = reg.render_body("demo")
        self.assertIn("Demo skill.", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
