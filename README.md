# claude-rules-guard

A Claude Code `PreToolUse` hook. Before every `Write` or `Edit`, it adds the coding rules that apply to that file to Claude's context:

- sections of `~/.claude/CLAUDE.md` and of the project's `CLAUDE.md` (following `@path` imports) whose heading matches the file's topic (controllers, specs, migrations…);
- files in the project's `.claude/rules/` whose `paths:` frontmatter matches the file — in full the first time in a session, then only their path.

When a project rule and a global rule disagree, the project rule wins.

## Install

Requires Python 3.13+.

1. Clone the repo:
   ```bash
   git clone https://github.com/NicolasAguirreCampi/claude-rules-guard.git
   ```
2. Copy the hook into your Claude folder:
   ```bash
   mkdir -p ~/.claude/hooks
   cp claude-rules-guard/rules_guard.py ~/.claude/hooks/
   ```
3. Add it to `~/.claude/settings.json`:
   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Write|Edit",
           "hooks": [
             { "type": "command", "command": "python3 ~/.claude/hooks/rules_guard.py", "timeout": 10 }
           ]
         }
       ]
     }
   }
   ```
4. Restart Claude Code.

## Tests

```bash
python3 -m unittest discover tests
```
