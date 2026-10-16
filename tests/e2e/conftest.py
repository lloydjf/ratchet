"""E2E fixtures live in ``e2e_support`` so the orchestrator chaos tests can share them.

The e2e suite (marker ``e2e``: tests/e2e plus tests/chaos/test_orchestrator_chaos.py) takes
about 70 s, so the default run excludes it (pyproject ``addopts``). Run it with::

    uv run pytest -m e2e
"""

from e2e_support import demo, demo_template, running

__all__ = ["demo", "demo_template", "running"]
