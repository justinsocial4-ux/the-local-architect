"""pytest configuration for the Fable Method enforcer.

Auto-assigns a unique, non-existent ``basetemp`` when one isn't supplied on the CLI.

Why: on some overlay filesystems (e.g. certain sandboxes) pytest cannot clear a ``basetemp``
left over from a previous run, so its reset-then-mkdir fails and surfaces as a flood of
misleading "errors" at fixture setup (every test that uses ``tmp_path``). A fresh, per-run
path that does not yet exist sidesteps the reset path entirely — so a plain ``pytest`` just
works, with no need to remember ``--basetemp=$(mktemp -u ...)``.

An explicit ``--basetemp`` on the command line still takes precedence.
"""
import os
import tempfile
import uuid


def pytest_configure(config):
    if not getattr(config.option, "basetemp", None):
        unique = f"fable_pytest_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        config.option.basetemp = os.path.join(tempfile.gettempdir(), unique)
