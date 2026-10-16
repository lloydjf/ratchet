---
name: dependency-updater
description: Update the pinned Python dependencies in requirements.txt of an acme repository. Checks each package's changelog for newer releases, bumps safe minor and patch versions, and opens a pull request.
---

# Dependency updater

Use this skill when the user asks you to update, upgrade or bump the dependencies or
requirements of a repository.

## Steps

1. Read the repository (`acme/<name>`) from the task. Call `shell.run` with
   `{"cmd": "cat requirements.txt"}` to read the pinned requirements (`<package>==<version>`
   lines; keep comment lines unchanged).
2. For each pinned package call `web.fetch` with
   `{"url": "https://pkgs.example.com/<package>/changelog"}`. The first `## <version> (<date>)`
   heading is the latest release.
3. Decide per package:
   - latest is newer and has the same major version: update the pin;
   - latest has a higher major version: keep the pin and report it as skipped;
   - the changelog cannot be fetched: keep the pin and report it as unknown.
4. If nothing changes, reply `All dependencies are up to date.` and stop: do not write files
   and do not open a pull request.
5. Write the updated file with `fs.write` to `requirements.txt`, same order, same comments.
6. Call `github.open_pr` once with `{"repo": "<owner/name>", "title": "Update dependencies", "head": "deps/update-<YYYY-MM-DD of the Current date>", "base": "main", "body": "<one line per update>"}`.
7. Reply with one line per package, in file order:
   - `Updated <package>: <old> -> <new>`
   - `Skipped <package>: <new> is a major update`
   - `Unknown <package>: no changelog found`
   - `Unchanged <package>: <version> is the latest`
   then `Opened pull request #<number> in <owner/name>.` if you opened one.
