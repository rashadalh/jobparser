# local_skills

Agent skills written for this repo specifically. They're committed so everyone working on
jdparser gets them, but they live here rather than in `.claude/skills/` so they're not
tied to one editor or agent setup.

| Skill | For |
|---|---|
| `jdparser-refactor-audit/` | Auditing or refactoring this codebase, and investigating bugs reported from live runs |

## Using one

Claude Code discovers skills under `.claude/skills/`. Symlink rather than copy, so edits
in one place take effect everywhere:

```bash
mkdir -p .claude/skills
ln -s ../../local_skills/jdparser-refactor-audit .claude/skills/jdparser-refactor-audit
```

Then invoke it as `/jdparser-refactor-audit`.

The symlink itself is a local setup choice, not part of the project. Add
`.claude/skills/` to `.gitignore` if you'd rather it stayed out of `git status`.

## Keeping it useful

The value of `jdparser-refactor-audit` is its landmine list (§4), and every entry is there
because it cost someone real debugging time. When something bites you and the cause was
non-obvious, add it. A landmine that only lives in a commit message gets rediscovered the
hard way.
