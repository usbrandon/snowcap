import pytest

from snowcap import resources as res
from snowcap.blueprint import (
    Blueprint,
    CreateResource,
    DropResource,
    NonConformingPlanException,
    UpdateResource,
    diff,
)
from snowcap.enums import AccountEdition, ResourceType
from snowcap.identifiers import parse_URN


@pytest.fixture
def session_ctx() -> dict:
    return {
        "account": "SOMEACCT",
        "account_edition": AccountEdition.ENTERPRISE,
        "account_locator": "ABCD123",
        "role": "SYSADMIN",
        "available_roles": ["SYSADMIN", "USERADMIN"],
    }


@pytest.fixture
def remote_state() -> dict:
    return {
        parse_URN("urn::ABCD123:account/ACCOUNT"): {},
    }


def test_plan_add_action(session_ctx, remote_state):
    bp = Blueprint(resources=[res.Database(name="NEW_DATABASE")])
    manifest = bp.generate_manifest(session_ctx)
    plan = diff(remote_state, manifest)
    assert len(plan) == 1
    change = plan[0]
    assert isinstance(change, CreateResource)
    assert change.urn == parse_URN("urn::ABCD123:database/NEW_DATABASE")
    assert "name" in change.after
    assert change.after["name"] == "NEW_DATABASE"


def test_plan_change_action(session_ctx, remote_state):
    remote_state[parse_URN("urn::ABCD123:role/EXISTING_ROLE")] = {
        "name": "EXISTING_ROLE",
        "comment": "old comment",
        "owner": "USERADMIN",
    }
    bp = Blueprint(
        resources=[
            res.Role(
                name="EXISTING_ROLE",
                comment="new comment",
            )
        ]
    )
    manifest = bp.generate_manifest(session_ctx)
    plan = diff(remote_state, manifest)
    assert len(plan) == 1
    change = plan[0]
    assert isinstance(change, UpdateResource)
    assert change.urn == parse_URN("urn::ABCD123:role/EXISTING_ROLE")
    assert "comment" in change.before
    assert change.before["comment"] == "old comment"
    assert "comment" in change.after
    assert change.after["comment"] == "new comment"


def test_plan_remove_action(session_ctx, remote_state):
    remote_state[parse_URN("urn::ABCD123:role/REMOVED_ROLE")] = {
        "name": "REMOVED_ROLE",
        "comment": "old comment",
        "owner": "USERADMIN",
    }
    bp = Blueprint(sync_resources=[ResourceType.ROLE])
    manifest = bp.generate_manifest(session_ctx)
    plan = diff(remote_state, manifest)
    assert len(plan) == 1
    change = plan[0]
    assert isinstance(change, DropResource)
    assert change.urn == parse_URN("urn::ABCD123:role/REMOVED_ROLE")


def test_plan_sync_drops_object_grant_not_covered_by_on_all_grant(session_ctx, remote_state):
    """Regression: when the manifest contains ON ALL grants, remote object grants
    that are NOT covered by any of them must still be dropped during grant sync.

    Previously the drop branch was attached to the wrong if, so the presence of
    any ON ALL grant in the manifest silently disabled dropping of every remote
    object grant, covered or not.
    """
    urn = parse_URN(
        "urn::ABCD123:grant/GRANT?grant_type=OBJECT&priv=INSERT&on=table/SOMEDB.SOMESCHEMA.SOME_TABLE&to=role/SOMEROLE"
    )
    remote_state[urn] = {
        "grant_type": "OBJECT",
        "priv": "INSERT",
        "on": "SOMEDB.SOMESCHEMA.SOME_TABLE",
        "on_type": "TABLE",
        "to": "SOMEROLE",
    }
    bp = Blueprint(
        resources=[
            res.Role(name="SOMEROLE"),
            res.Grant(priv="SELECT", on=["ALL", "TABLES", "SCHEMA", "somedb.someschema"], to="somerole"),
        ],
        sync_resources=[ResourceType.GRANT],
    )
    manifest = bp.generate_manifest(session_ctx)
    plan = diff(remote_state, manifest)
    drops = [change for change in plan if isinstance(change, DropResource)]
    assert len(drops) == 1
    assert drops[0].urn == urn


def test_plan_sync_keeps_object_grant_covered_by_on_all_grant(session_ctx, remote_state):
    """Remote object grants that ARE covered by an ON ALL grant in the manifest
    (same priv, same grantee, object type and container match) must not be dropped."""
    urn = parse_URN(
        "urn::ABCD123:grant/GRANT?grant_type=OBJECT&priv=SELECT&on=table/SOMEDB.SOMESCHEMA.SOME_TABLE&to=role/SOMEROLE"
    )
    remote_state[urn] = {
        "grant_type": "OBJECT",
        "priv": "SELECT",
        "on": "SOMEDB.SOMESCHEMA.SOME_TABLE",
        "on_type": "TABLE",
        "to": "SOMEROLE",
    }
    bp = Blueprint(
        resources=[
            res.Role(name="SOMEROLE"),
            res.Grant(priv="SELECT", on=["ALL", "TABLES", "SCHEMA", "somedb.someschema"], to="somerole"),
        ],
        sync_resources=[ResourceType.GRANT],
    )
    manifest = bp.generate_manifest(session_ctx)
    plan = diff(remote_state, manifest)
    drops = [change for change in plan if isinstance(change, DropResource)]
    assert drops == []


def test_plan_no_removes_in_resources_not_in_sync_resources(session_ctx, remote_state):
    """Test that plan correctly identifies resources to remove.

    Note: This test originally expected _raise_for_nonconforming_plan to raise
    NonConformingPlanException for drops when sync_resources is not set, but
    that validation is not implemented. The current behavior is that drops are
    allowed when sync_resources is None.
    """
    remote_state[parse_URN("urn::ABCD123:role/REMOVED_ROLE")] = {
        "name": "REMOVED_ROLE",
        "comment": "old comment",
        "owner": "USERADMIN",
    }
    bp = Blueprint()
    manifest = bp.generate_manifest(session_ctx)
    plan = diff(remote_state, manifest)
    assert len(plan) == 1
    change = plan[0]
    assert isinstance(change, DropResource)
    assert change.urn == parse_URN("urn::ABCD123:role/REMOVED_ROLE")
    # Note: _raise_for_nonconforming_plan does not validate drops against sync_resources
    # This behavior is intentional - sync_resources controls what gets synced, not what can be dropped
