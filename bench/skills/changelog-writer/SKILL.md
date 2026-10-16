---
name: changelog-writer
description: Write or update the CHANGELOG.md changelog entry and release notes for a release version of an acme repository from its git commit log since the previous tag, grouping commits into Added, Fixed, Documentation and Changed.
---

# Changelog writer

Use this skill when the user asks you to update the changelog or write release notes for a
repository.

## Steps

1. Read the repository (`acme/<name>`), the previous tag (after "since") and the new version
   (after "release" or "version") from the task. Without a new version, use `Unreleased`.
2. Call `git.log` with `{"repo": "<owner/name>", "since": "<previous tag>"}`.
3. Group the commits by message prefix: `feat:` commits go under `### Added`, `fix:` commits go under `### Fixed`, `docs:` commits go under `### Documentation`, everything else under `### Changed`. Skip commits whose message starts with `chore(release)` or `Merge`.
4. Write each entry as `- <message without the prefix, first letter capitalized> (<short sha>)`.
5. Read `CHANGELOG.md` with `fs.read` (it may not exist yet). Write it back with `fs.write`
   with the new section `## <version> - <today's date, YYYY-MM-DD>` directly under the
   `# Changelog` title, above older sections.
6. Reply `Updated CHANGELOG.md with <n> entries for <version>.`
