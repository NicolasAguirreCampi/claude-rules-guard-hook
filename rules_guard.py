#!/usr/bin/env python3
"""Inject the coding rules relevant to the file being written, from the global
~/.claude/CLAUDE.md and the project's CLAUDE.md (with its @imports). Where the
two disagree, the project rule wins."""

import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

# Topic -> (path patterns that select it, heading keywords used to find its rules)
TOPICS = {
    "controllers": (
        [r"/app/controllers/", r"_controller\.rb$"],
        ["controller"],
    ),
    "services": (
        [r"/app/services/"],
        ["service", "solid_service", "application_service"],
    ),
    "queries": (
        [r"/app/queries/"],
        ["quer", "service"],
    ),
    "models": (
        [r"/app/models/"],
        ["model", "callback", "enum", "idempot"],
    ),
    "specs": (
        [r"_spec\.rb$", r"/spec/"],
        ["spec", "test", "fixture", "rspec", "factor"],
    ),
    "views": (
        [r"\.html\.erb$", r"/app/views/", r"/app/components/"],
        ["view", "partial", "component", "frontend", "accessib", "icon", "svg"],
    ),
    "migrations": (
        [r"/db/migrate/", r"/db/data/"],
        ["migration", "zero-downtime", "index"],
    ),
    "javascript": (
        [r"\.js$", r"\.ts$", r"/app/javascript/"],
        ["stimulus", "javascript", "turbo", "frontend"],
    ),
}

ALWAYS = ["comment"]  # rules that apply to every file we touch


def sections_for(markdown: str, keywords: list[str]) -> list[str]:
    """Return heading sections whose title mentions any keyword."""
    if not markdown:
        return []
    lines = markdown.splitlines()
    heads = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and (m := re.match(r"^(#{1,6})\s+(.*)", line)):
            heads.append((i, len(m.group(1))))
    out = []
    covered_until = -1
    for idx, (line_no, level) in enumerate(heads):
        if line_no < covered_until:
            continue
        title = lines[line_no].lstrip("#").strip().lower()
        if not any(k in title for k in keywords):
            continue
        end = len(lines)
        for later_no, later_level in heads[idx + 1:]:
            if later_level <= level:
                end = later_no
                break
        covered_until = end
        body = "\n".join(lines[line_no:end]).strip()
        if body:
            out.append(body)
    return out


IMPORT = re.compile(r"(?<![\w`])@((?:~/|\.{0,2}/)?[\w.-][\w./-]*)")
MAX_IMPORT_DEPTH = 5


def with_imports(path: Path, seen: set[Path] | None = None, depth: int = 0) -> list[tuple[Path, str]]:
    """Read a CLAUDE.md and, recursively, the files its @path imports name."""
    seen = set() if seen is None else seen
    resolved = path.resolve()
    if resolved in seen or depth > MAX_IMPORT_DEPTH:
        return []
    seen.add(resolved)
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    files = [(path, text)]
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    prose = re.sub(r"`[^`\n]*`", "", prose)
    for target in IMPORT.findall(prose):
        imported = Path(os.path.expanduser(target))
        if not imported.is_absolute():
            imported = path.parent / imported
        if imported.is_file():
            files.extend(with_imports(imported, seen, depth + 1))
    return files


def nearest_project_rules(start: Path) -> list[tuple[Path, str]]:
    for parent in [start] + list(start.parents):
        candidate = parent / "CLAUDE.md"
        if candidate.is_file():
            return with_imports(candidate)
    return []


FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def rule_globs(text: str) -> list[str]:
    """The `paths:` globs in a .claude/rules file's frontmatter."""
    frontmatter = FRONTMATTER.match(text)
    if not frontmatter:
        return []
    block = re.search(r"^paths:\s*\n((?:[ \t]+-.*\n?)+)", frontmatter.group(1) + "\n", re.M)
    if not block:
        return []
    return [g.strip().strip("\"'") for g in re.findall(r"^\s+-\s*(.+)$", block.group(1), re.M)]


def matching_rule_files(root: Path, target: Path) -> list[tuple[Path, str]]:
    try:
        relative = PurePosixPath(target.relative_to(root).as_posix())
    except ValueError:
        return []
    found = {}
    for candidate in glob.glob(str(root / ".claude" / "rules" / "**" / "*.md"), recursive=True):
        resolved = Path(candidate).resolve()
        if resolved in found:
            continue
        try:
            text = resolved.read_text(errors="replace")
        except OSError:
            continue
        if any(relative.full_match(g) for g in rule_globs(text)):
            found[resolved] = text
    return sorted(found.items())


def first_time_this_session(seen_dir: Path, session: str, rule_file: Path) -> bool:
    seen_dir.mkdir(parents=True, exist_ok=True)
    marker = seen_dir / f"{session}-{hashlib.sha1(str(rule_file).encode()).hexdigest()[:12]}"
    if marker.exists():
        return False
    marker.write_text(str(rule_file))
    return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path") or ""
    if not raw_path:
        return 0

    path = Path(raw_path)
    posix = path.as_posix()

    topics = [name for name, (patterns, _) in TOPICS.items()
              if any(re.search(p, posix) for p in patterns)]

    project_files = nearest_project_rules(path.parent)
    project_root = project_files[0][0].parent if project_files else None
    rule_files = matching_rule_files(project_root, path) if project_root else []
    if not topics and not rule_files:
        return 0

    keywords = ALWAYS.copy()
    for name in topics:
        keywords.extend(TOPICS[name][1])

    home = Path(os.path.expanduser("~"))
    global_files = with_imports(home / ".claude" / "CLAUDE.md")

    global_sections = [s for _, text in global_files for s in sections_for(text, keywords)]
    project_by_file = [(p, found) for p, text in project_files
                       if (found := sections_for(text, keywords))]
    if not global_sections and not project_by_file and not rule_files:
        return 0

    labels = topics or [p.stem for p, _ in rule_files]
    parts = [f"Rules that apply to this file ({', '.join(labels)}). "
             "Follow them over whatever the neighbouring files happen to do — "
             "many predate the current conventions. If a project rule contradicts "
             "a rule from ~/.claude/CLAUDE.md, follow the project rule."]
    if global_sections:
        parts.append("## From ~/.claude/CLAUDE.md\n\n" + "\n\n".join(global_sections))
    for project_path, found in project_by_file:
        parts.append(f"## From {project_path}\n\n" + "\n\n".join(found))

    session = payload.get("session_id") or "nosession"
    seen_dir = home / ".claude" / "hooks" / ".rules-seen"
    already_injected = []
    for rule_file, text in rule_files:
        if first_time_this_session(seen_dir, session, rule_file):
            parts.append(f"## From {rule_file}\n\n" + FRONTMATTER.sub("", text).strip())
        else:
            already_injected.append(f"- {rule_file}")
    if already_injected:
        parts.append("## Also applies (injected in full earlier this session; re-read it if "
                     "it is no longer in context)\n\n" + "\n".join(already_injected))

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "\n\n".join(parts),
        }
    }

    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
