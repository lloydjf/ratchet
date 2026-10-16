# OpenClaw layout fixture (read-only compatibility)

Mirrors an OpenClaw workspace: `~/.openclaw/workspace/skills/<name>/SKILL.md`, the
agentskills.io format plus OpenClaw's frontmatter extras (`homepage`, `user-invocable`,
`metadata` as single-line JSON under `openclaw`: `requires`, `os`, `primaryEnv`).
Ratchet treats `workspace/skills/` as the skills repo (`RATCHET_SKILLS_REPO`); it reads
these files and never writes OpenClaw's config. Used by
`tests/integration/test_openclaw_layout.py`.
