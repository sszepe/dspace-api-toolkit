"""
Fixtures for integration tests against a real DSpace / DSpace-CRIS server.

Configuration (environment, usually set by integration/run_matrix.py):

    DSPACE_IT_URL        REST API root, e.g. http://localhost:18090/server/api
    DSPACE_IT_USER       admin email
    DSPACE_IT_PASSWORD   admin password

Without DSPACE_IT_URL every test here is skipped, so the normal unit test
run is unaffected.

Everything a run creates lives below one sandbox community
("Toolkit IT <run id>"), which is deleted at the end, together with the
groups and epersons the tests create. Tests that hit a feature the server
does not offer call ``unsupported(...)`` / use ``unsupported_on(...)``;
the report plugin records that as "unsupported", not as a failure.
"""

import os
import time
import uuid as uuidlib
from pathlib import Path

import pytest

from dspace_api.auth import DSpaceAuth
from dspace_api.authorizations import AuthorizationManager
from dspace_api.bundles import BundleManager
from dspace_api.collections import CollectionManager, build_collection_metadata
from dspace_api.communities import CommunityManager, build_dso_metadata
from dspace_api.config import DSpaceConfig
from dspace_api.discovery import DiscoveryManager
from dspace_api.entity_types import EntityTypeManager
from dspace_api.epersons import EPersonManager
from dspace_api.exceptions import DSpaceError
from dspace_api.groups import GroupManager
from dspace_api.items import ItemManager
from dspace_api.metadata import MetadataManager
from dspace_api.permissions import PermissionManager
from dspace_api.relationships import RelationshipManager
from dspace_api.site import SiteManager
from dspace_api.versions import VersionManager
from dspace_api.vocabulary import VocabularyManager
from dspace_api.workflow import WorkflowManager
from dspace_api.workspaces import WorkspaceManager
from integration.features import FEATURES

from .helpers import UNSUPPORTED_STATUSES  # noqa: F401  (re-exported for tests)

HERE = Path(__file__).parent
IT_URL = os.getenv("DSPACE_IT_URL")


def pytest_configure(config):
    config.addinivalue_line("markers", "feature(*ids): toolkit capability exercised by an integration test")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if HERE not in Path(str(item.fspath)).parents:
            continue
        item.add_marker(pytest.mark.integration)
        features = [f for m in item.iter_markers("feature") for f in m.args]
        if not features:
            raise pytest.UsageError(f"{item.nodeid}: integration tests need @pytest.mark.feature(...)")
        unknown = [f for f in features if f not in FEATURES]
        if unknown:
            raise pytest.UsageError(f"{item.nodeid}: unknown feature id(s) {unknown}; add them to "
                                    "integration/features.py")
        if not IT_URL:
            item.add_marker(pytest.mark.skip(reason="DSPACE_IT_URL not set (needs a DSpace server)"))


# ---------------------------------------------------------------------------
# Session: connection and managers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def it_config():
    return {
        "url": IT_URL,
        "user": os.getenv("DSPACE_IT_USER", "admin@example.org"),
        "password": os.getenv("DSPACE_IT_PASSWORD", ""),
        "family": os.getenv("DSPACE_IT_FAMILY", ""),
        "base_version": os.getenv("DSPACE_IT_BASE_VERSION", ""),
    }


def make_auth(it_config):
    config = DSpaceConfig(it_config["url"], all_languages=False, max_retries=3, retry_backoff=2,
                          timeout=120)
    auth = DSpaceAuth(config)
    auth.login(it_config["user"], it_config["password"])
    # DSpace-CRIS refuses almost every request (403 "No user agreement accepted")
    # until the user has accepted the end-user agreement - in the UI that is
    # the agreement page after the first login. The freshly created test admin
    # never saw it, so accept it once here (plain DSpace just stores the flag).
    epersons = EPersonManager(auth)
    me = epersons.get_current_user()
    if me and not epersons.has_accepted_end_user_agreement(me):
        epersons.accept_end_user_agreement(me["uuid"])     # also refreshes the token
    return auth


@pytest.fixture(scope="session")
def auth(it_config):
    auth = make_auth(it_config)
    yield auth
    auth.logout()


@pytest.fixture(scope="session")
def new_auth(it_config):
    """Factory for an independent, logged-in session (for tests that break their token)."""
    created = []

    def factory():
        a = make_auth(it_config)
        created.append(a)
        return a
    yield factory
    for a in created:
        try:
            a.logout()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture(scope="session")
def items(auth):
    return ItemManager(auth)


@pytest.fixture(scope="session")
def bundles(auth):
    return BundleManager(auth)


@pytest.fixture(scope="session")
def permissions(auth):
    return PermissionManager(auth)


@pytest.fixture(scope="session")
def groups(auth):
    return GroupManager(auth)


@pytest.fixture(scope="session")
def epersons(auth):
    return EPersonManager(auth)


@pytest.fixture(scope="session")
def communities(auth):
    return CommunityManager(auth)


@pytest.fixture(scope="session")
def collections(auth):
    return CollectionManager(auth)


@pytest.fixture(scope="session")
def site(auth):
    return SiteManager(auth)


@pytest.fixture(scope="session")
def entity_types(auth):
    return EntityTypeManager(auth)


@pytest.fixture(scope="session")
def discovery(auth):
    return DiscoveryManager(auth)


@pytest.fixture(scope="session")
def metadata(auth):
    return MetadataManager(auth)


@pytest.fixture(scope="session")
def workspace(auth):
    return WorkspaceManager(auth)


@pytest.fixture(scope="session")
def authorizations(auth):
    return AuthorizationManager(auth)


@pytest.fixture(scope="session")
def versions(auth):
    return VersionManager(auth)


@pytest.fixture(scope="session")
def vocabulary(auth):
    return VocabularyManager(auth)


@pytest.fixture(scope="session")
def relationships(auth):
    return RelationshipManager(auth)


@pytest.fixture(scope="session")
def workflow(auth):
    return WorkflowManager(auth)


@pytest.fixture(scope="session")
def me(epersons):
    return epersons.get_current_user()


# ---------------------------------------------------------------------------
# Session: sandbox content
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def run_id():
    return time.strftime("%Y%m%d%H%M%S") + "-" + uuidlib.uuid4().hex[:6]


@pytest.fixture(scope="session")
def entity_type(entity_types):
    """'Publication' where configurable entities exist, else None (plain items)."""
    try:
        labels = {t.get("label") for t in entity_types.get_entity_types()}
    except DSpaceError:
        return None
    return "Publication" if "Publication" in labels else None


@pytest.fixture(scope="session")
def sandbox(communities, run_id):
    community = communities.create_community(
        build_dso_metadata(f"Toolkit IT {run_id}", description="Temporary - created by the integration tests"))
    yield community
    try:
        communities.delete_community(community["uuid"])
    except DSpaceError as exc:  # pragma: no cover - best effort cleanup
        print(f"WARNING: could not delete sandbox community {community['uuid']}: {exc}")


@pytest.fixture(scope="session")
def collection(collections, sandbox, entity_type, run_id):
    title = f"IT collection {run_id}"
    return collections.create_collection(
        sandbox["uuid"], title, metadata=build_collection_metadata(title, entity_type=entity_type))


@pytest.fixture
def cleanup():
    """Register callables to run after the test (in reverse order), errors ignored."""
    tasks = []
    yield tasks.append
    for task in reversed(tasks):
        try:
            task()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def new_item(items, collection, entity_type, run_id, cleanup):
    """Factory: create an archived item in the sandbox collection (deleted after the test)."""
    counter = iter(range(1, 10_000))

    def factory(title=None, metadata=None):
        md = {"dc.title": title or f"IT item {run_id}-{next(counter)}", "dc.date.issued": "2026"}
        md.update(metadata or {})
        item = items.create_item(collection["uuid"], md, entity_type)
        cleanup(lambda: items.delete_item(item["uuid"]))
        return item
    return factory


@pytest.fixture
def new_eperson(epersons, run_id, cleanup):
    counter = iter(range(1, 10_000))

    def factory(**kwargs):
        email = f"it-{run_id}-{next(counter)}@example.org"
        person = epersons.create_eperson(email, first_name="Integration", last_name="Test", **kwargs)
        cleanup(lambda: epersons.delete_eperson(person["uuid"]))
        return person
    return factory


@pytest.fixture
def new_group(groups, run_id, cleanup):
    counter = iter(range(1, 10_000))

    def factory(name=None):
        group = groups.create_group(name or f"IT group {run_id}-{next(counter)}", description="integration test")
        cleanup(lambda: groups.delete_group(group["uuid"]))
        return group
    return factory


@pytest.fixture
def new_collection(collections, sandbox, entity_type, run_id, cleanup):
    counter = iter(range(1, 10_000))

    def factory():
        title = f"IT collection {run_id}-{next(counter)}"
        coll = collections.create_collection(
            sandbox["uuid"], title, metadata=build_collection_metadata(title, entity_type=entity_type))
        cleanup(lambda: collections.delete_collection(coll["uuid"]))
        return coll
    return factory


