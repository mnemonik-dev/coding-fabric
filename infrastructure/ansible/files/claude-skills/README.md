# claude-skills — curated command + skill bundle

Curated snapshot of the molyanov-ai-dev methodology bundle, deployed by the
`telegram-ai-agent` Ansible role into every HOME a `claude` process may use:

1. `/var/lib/telegram-ai-agent/.claude` — bot-spawned engine sessions
2. `/home/op/.claude` — Symphony-spawned agents + operator SSH sessions

This directory is the **single source of truth** for deployed skills and
commands. Do not also symlink the raw upstream clone
(`/opt/molyanov-ai-dev`) into `~/.claude/skills/` — that produced
duplicate-skill-name conflicts (removed 2026-07).

## Naming convention — why every command exists twice

`commands/` intentionally ships two spellings of each multi-word command:

| Variant | Example | Why |
|---------|---------|-----|
| dash (canonical) | `do-task.md` → `/do-task` | Matches molyanov-ai-dev docs and Claude Code CLI convention |
| underscore (Telegram alias) | `do_task.md` → `/do_task` | Telegram bot commands only allow `[a-z0-9_]{1,32}` — a dash command **cannot be typed as a Telegram command** |

Both files must stay byte-identical (CI-friendly check:
`diff commands/do-task.md commands/do_task.md`). When editing a command,
edit the dash file and copy it over the underscore twin.

`skills/` ships **dash names only**. Skills are invoked by the agent via the
Skill tool using the frontmatter `name:` — they are never typed as Telegram
commands, so no underscore alias is needed. Never add an underscore skill
directory: two directories with the same frontmatter `name:` make skill
resolution unreliable in every session (this was the pre-2026-07 bug behind
"agent does not recognize the command at once").

## Adding a new command

1. Create `commands/<name-with-dashes>.md` (frontmatter: `description`,
   optional `allowed-tools`).
2. If the name contains a dash, copy it to `commands/<name_with_underscores>.md`.
3. If it delegates to a skill, reference the skill by its dash name
   (`Use the `task-decomposition` skill.`).
4. Redeploy: `ansible-playbook playbooks/deploy.yml --tags telegram-ai-agent`
   (the sync is additive; renames/removals also need a purge task — see
   `telegram_ai_agent_legacy_underscore_skills` in the role defaults for the
   pattern).
