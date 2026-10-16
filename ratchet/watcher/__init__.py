"""ratchet-watcher: the skills git repository and the file watcher (ADR 3).

``ratchet.watcher.gitops.SkillsRepo`` owns ``main``/``candidate``;
``ratchet.watcher.service.Watcher`` turns file writes into ``SkillEdit`` records.
"""

__all__: list[str] = []
