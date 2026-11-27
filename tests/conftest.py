import pytest

from forgemind.auth import Principal
from forgemind.config import Settings
from forgemind.service import ForgeMind

PASSWORD = "correct horse battery"
SECRET = "s" * 40


@pytest.fixture
def core(tmp_path):
    return ForgeMind(Settings(db_path=str(tmp_path / "t.db"), secret=SECRET, llm_provider="extractive",
                              embed_provider="hashing"))


@pytest.fixture
def people(core):
    for tenant, name, role, groups in (("acme", "root", "admin", []), ("acme", "hana", "editor", ["hr"]),
                                       ("acme", "eli", "viewer", ["eng"]),
                                       ("globex", "gil", "admin", [])):
        core.users.create(tenant, name, PASSWORD, role, groups)
    return {n: core.users.get(t, n) for t, n in (("acme", "root"), ("acme", "hana"),
                                                 ("acme", "eli"), ("globex", "gil"))}


def add(core, principal, name, text, groups=()):
    job = core.jobs.submit(principal, name, text.encode() if isinstance(text, str) else text, list(groups))
    while core.jobs.run_once():
        pass
    return core.jobs.get(principal, job)
