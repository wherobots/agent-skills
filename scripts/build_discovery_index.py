"""Build .well-known/agent-skills/index.json from every SKILL.md.

The index follows Agent Skills Discovery v0.2.0
(https://github.com/cloudflare/agent-skills-discovery-rfc). Each entry's digest
is sha256 over the exact bytes of the SKILL.md on main, so the index must be
rebuilt whenever a skill changes. wherobots.com and other hosts copy their skill
entries from this file.

Usage:
    python3 scripts/build_discovery_index.py          # write the index
    python3 scripts/build_discovery_index.py --check  # fail if it is out of date
"""

import hashlib
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / ".agents" / "skills"
INDEX = ROOT / ".well-known" / "agent-skills" / "index.json"
SCHEMA = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"
URL = "https://raw.githubusercontent.com/wherobots/agent-skills/main/.agents/skills/{}/SKILL.md"


def build() -> str:
    skills = []
    for skill_file in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        raw = skill_file.read_bytes()
        frontmatter = yaml.safe_load(raw.decode("utf-8").split("---")[1])
        skills.append(
            {
                "name": frontmatter["name"],
                "type": "skill-md",
                "description": frontmatter["description"],
                "url": URL.format(frontmatter["name"]),
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
        )
    return json.dumps({"$schema": SCHEMA, "skills": skills}, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    expected = build()
    if "--check" in sys.argv[1:]:
        current = INDEX.read_text(encoding="utf-8") if INDEX.exists() else ""
        if current != expected:
            print(f"ERROR: {INDEX.relative_to(ROOT)} is out of date. Run: python3 scripts/build_discovery_index.py")
            return 1
        count = len(json.loads(expected)["skills"])
        print(f"Discovery index OK: {count} skills")
        return 0
    INDEX.write_text(expected, encoding="utf-8")
    print(f"Wrote {INDEX.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
