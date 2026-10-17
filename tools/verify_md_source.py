"""Read-only audit of the regular Millennium Dawn identifiers used by Voices."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "middleware")]
from md_compat import require_regular_md, quoted_field, WORKSHOP_ID
from profiles import MD_EQUIPMENT, MD_IDEOLOGY
from unified_mod import render

TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|#[^\n]*|[{}]')


def block(text, start):
    depth = 1
    for token in TOKENS.finditer(text, start):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return text[start:token.start()]
    raise ValueError("Unclosed source definition")


def verify(source):
    source = Path(source)
    require_regular_md(source / "descriptor.mod")
    metadata = (source / "descriptor.mod").read_text(encoding="utf-8-sig")
    version = quoted_field(metadata, "supported_version")
    if version != "1.19.*":
        raise ValueError("Revalidate the adapter for MD's supported game version: " + version)
    files = {}
    def read(relative):
        data = (source / relative).read_bytes()
        files[relative] = hashlib.sha256(data).hexdigest()
        return data.decode("utf-8-sig")
    read("descriptor.mod")
    ideologies = read("common/ideologies/00_ideologies.txt")
    labels = read("localisation/english/parties_l_english.yml")
    exported = render("millennium_dawn")["common/scripted_effects/ai_diplo_export.txt"].decode("utf-8-sig")
    groups = re.findall(r"has_government = (\w+) \} set_temp_variable = \{ aid_ideo = (\d+)", exported)
    if len(groups) != 5 or {number for _, number in groups} != set(MD_IDEOLOGY) - {"0"}:
        raise ValueError("The MD export must map exactly five outlooks.")
    for group, number in groups:
        if not re.search(r"(?m)^\t" + re.escape(group) + r"\s*=\s*\{", ideologies):
            raise ValueError("Missing regular MD outlook definition: " + group)
    # Localization uses colon syntax, independently checked against the adapter labels.
    for group, number in groups:
        match = re.search(r'(?m)^\s*' + re.escape(group) + r':\s*(?:\d+\s*)?"([^"\r\n]+)"', labels)
        if not match or match.group(1) != MD_IDEOLOGY[number]:
            raise ValueError("Outlook label mismatch: " + group)
    equipment = {}
    for path in sorted((source / "common/units/equipment").glob("*.txt")):
        text = path.read_text(encoding="utf-8-sig")
        for identifier in MD_EQUIPMENT:
            match = re.search(r"(?m)^\t" + re.escape(identifier) + r"\s*=\s*\{", text)
            if match:
                definition = block(text, match.end())
                if not re.search(r"(?m)^\s*is_archetype\s*=\s*yes\b", definition):
                    raise ValueError("Equipment is not an archetype: " + identifier)
                if identifier in equipment:
                    raise ValueError("Duplicate equipment definition: " + identifier)
                relative = path.relative_to(source).as_posix()
                read(relative)
                equipment[identifier] = relative
    if set(equipment) != MD_EQUIPMENT:
        raise ValueError("Missing regular MD equipment: " + str(MD_EQUIPMENT - set(equipment)))
    templates = read("common/ai_templates/MD_generic.txt")
    priorities = render("millennium_dawn")["common/ai_strategy/voices_priorities.txt"].decode()
    roles = sorted(set(re.findall(r"\bid\s*=\s*(\w+)", priorities)))
    for role in roles:
        if not re.search(r"\brole\s*=\s*" + re.escape(role) + r"\b", templates):
            raise ValueError("Missing regular MD template role: " + role)
    return {"repository": "MillenniumDawn/Millennium-Dawn", "workshop_id": WORKSHOP_ID,
            "supported_version": version, "outlooks": groups, "equipment": equipment,
            "priority_roles": roles, "source_sha256": files}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.source), indent=2, sort_keys=True))
