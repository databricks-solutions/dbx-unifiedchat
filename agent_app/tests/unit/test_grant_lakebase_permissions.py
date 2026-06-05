from pathlib import Path
from types import SimpleNamespace
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scripts.grant_lakebase_permissions as grant_module
from databricks.sdk.service import apps as apps_service

from scripts.grant_lakebase_permissions import (
    CatalogSchemaTarget,
    PermissionGrantConfig,
    apply_permission_grants,
    hydrate_config_from_bundle,
    parse_catalog_schema_targets,
    resolve_data_uc_targets,
    sync_app_resource_permissions,
)


def test_parse_catalog_schema_targets_supports_multiple_catalog_groups():
    assert parse_catalog_schema_targets(
        "catalog_1:schema1, schema2; catalog_2:schema_a, schema_b"
    ) == [
        CatalogSchemaTarget("catalog_1", "schema1"),
        CatalogSchemaTarget("catalog_1", "schema2"),
        CatalogSchemaTarget("catalog_2", "schema_a"),
        CatalogSchemaTarget("catalog_2", "schema_b"),
    ]


def test_parse_catalog_schema_targets_rejects_missing_colon():
    with pytest.raises(ValueError, match="Expected format"):
        parse_catalog_schema_targets("catalog_1.schema1")


def test_resolve_data_uc_targets_prefers_multi_catalog_mapping():
    assert resolve_data_uc_targets(
        PermissionGrantConfig(
            memory_type="langgraph-short-term",
            data_catalog_name="catalog_1",
            data_schema_name="legacy_schema",
            data_catalog_schemas="catalog_1:schema1, schema2",
        )
    ) == [
        CatalogSchemaTarget("catalog_1", "schema1"),
        CatalogSchemaTarget("catalog_1", "schema2"),
    ]


def test_hydrate_config_from_bundle_resolves_volume_name(tmp_path):
    bundle_config = tmp_path / "databricks.yml"
    bundle_config.write_text(
        """
variables:
  volume_name:
    default: "default-volume"
targets:
  dev:
    default: true
    variables:
      volume_name: "dev-volume"
""".strip()
    )

    resolved = hydrate_config_from_bundle(
        PermissionGrantConfig(
            memory_type="langgraph-short-term",
            app_name="test-app",
            target="dev",
            bundle_config_path=str(bundle_config),
        )
    )

    assert resolved.volume_name == "dev-volume"


def test_hydrate_config_from_bundle_prefers_autoscaling_project_and_branch(tmp_path):
    bundle_config = tmp_path / "databricks.yml"
    bundle_config.write_text(
        """
variables:
  lakebase_project:
    default: "default-project"
  lakebase_branch:
    default: "production"
targets:
  dev:
    default: true
    variables:
      lakebase_project: "dev-project"
      lakebase_branch: "dev-branch"
""".strip()
    )

    resolved = hydrate_config_from_bundle(
        PermissionGrantConfig(
            memory_type="langgraph-short-term",
            app_name="test-app",
            target="dev",
            bundle_config_path=str(bundle_config),
        )
    )

    assert resolved.project == "dev-project"
    assert resolved.branch == "dev-branch"
    assert resolved.instance_name is None


def test_hydrate_config_from_bundle_resolves_data_catalog_schemas(tmp_path):
    bundle_config = tmp_path / "databricks.yml"
    bundle_config.write_text(
        """
variables:
  data_catalog_schemas:
    default: "default_catalog:default_schema"
targets:
  dev:
    default: true
    variables:
      data_catalog_schemas: "catalog_1:schema1, schema2; catalog_2:schema_a"
""".strip()
    )

    resolved = hydrate_config_from_bundle(
        PermissionGrantConfig(
            memory_type="langgraph-short-term",
            app_name="test-app",
            target="dev",
            bundle_config_path=str(bundle_config),
        )
    )

    assert resolved.data_catalog_schemas == "catalog_1:schema1, schema2; catalog_2:schema_a"


def test_apply_permission_grants_grants_each_data_catalog_schema(monkeypatch):
    class FakePrivilege:
        def __init__(self, value):
            self.value = value

    class FakeSchemaPrivilege:
        USAGE = FakePrivilege("USAGE")
        CREATE = FakePrivilege("CREATE")

    class FakeSequencePrivilege:
        USAGE = FakePrivilege("USAGE")
        SELECT = FakePrivilege("SELECT")
        UPDATE = FakePrivilege("UPDATE")

    class FakeTablePrivilege:
        SELECT = FakePrivilege("SELECT")
        INSERT = FakePrivilege("INSERT")
        UPDATE = FakePrivilege("UPDATE")
        DELETE = FakePrivilege("DELETE")

    class FakeLakebaseClient:
        def __init__(self, **_kwargs):
            pass

        def create_role(self, *_args):
            pass

        def grant_schema(self, **_kwargs):
            pass

        def grant_table(self, **_kwargs):
            pass

        def grant_all_sequences_in_schema(self, **_kwargs):
            pass

    fake_lakebase = types.ModuleType("databricks_ai_bridge.lakebase")
    fake_lakebase.LakebaseClient = FakeLakebaseClient
    fake_lakebase.SchemaPrivilege = FakeSchemaPrivilege
    fake_lakebase.SequencePrivilege = FakeSequencePrivilege
    fake_lakebase.TablePrivilege = FakeTablePrivilege
    fake_bridge = types.ModuleType("databricks_ai_bridge")
    fake_bridge.lakebase = fake_lakebase
    monkeypatch.setitem(sys.modules, "databricks_ai_bridge", fake_bridge)
    monkeypatch.setitem(sys.modules, "databricks_ai_bridge.lakebase", fake_lakebase)

    grant_calls = []
    monkeypatch.setattr(
        grant_module,
        "grant_uc_permissions",
        lambda **kwargs: grant_calls.append(
            (kwargs["catalog_name"], kwargs["schema_name"])
        ),
    )
    monkeypatch.setattr(
        grant_module,
        "sync_app_resource_permissions",
        lambda *_args, **_kwargs: None,
    )

    resolved_sp_id = apply_permission_grants(
        PermissionGrantConfig(
            memory_type="langgraph-short-term",
            sp_client_id="sp-client-id",
            project="lakebase-project",
            branch="production",
            catalog_name="app_catalog",
            schema_name="app_schema",
            data_catalog_schemas="catalog_1:schema1, schema2; catalog_2:schema_a, schema_b",
        ),
        workspace_client=SimpleNamespace(),
    )

    assert resolved_sp_id == "sp-client-id"
    assert grant_calls == [
        ("app_catalog", "app_schema"),
        ("catalog_1", "schema1"),
        ("catalog_1", "schema2"),
        ("catalog_2", "schema_a"),
        ("catalog_2", "schema_b"),
    ]


def test_sync_app_resource_permissions_adds_write_only_volume_resource():
    updates = []

    current_app = SimpleNamespace(
        resources=[
            apps_service.AppResource(
                name="trace-volume-read",
                uc_securable=apps_service.AppResourceUcSecurable(
                    securable_full_name="main.app.trace",
                    securable_type=apps_service.AppResourceUcSecurableUcSecurableType.VOLUME,
                    permission=apps_service.AppResourceUcSecurableUcSecurablePermission.READ_VOLUME,
                ),
            ),
            SimpleNamespace(name="keep-me", genie_space=None, uc_securable=None),
        ]
    )

    workspace_client = SimpleNamespace(
        apps=SimpleNamespace(
            get=lambda _app_name: current_app,
            update=lambda app_name, app: updates.append((app_name, app)),
        )
    )

    sync_app_resource_permissions(
        PermissionGrantConfig(
            memory_type="langgraph-short-term",
            app_name="test-app",
            instance_name="lakebase-instance",
            database_name="databricks_postgres",
            catalog_name="main",
            schema_name="app",
            volume_name="trace",
            genie_space_ids=[],
        ),
        workspace_client=workspace_client,
    )

    assert len(updates) == 1
    app_name, app = updates[0]
    assert app_name == "test-app"

    resource_names = [resource.name for resource in app.resources]
    assert "keep-me" in resource_names
    assert "database" in resource_names
    assert "trace-volume-write" in resource_names
    assert resource_names.count("trace-volume-read") == 0

    volume_resources = [
        resource for resource in app.resources if getattr(resource, "uc_securable", None) is not None
    ]
    assert len(volume_resources) == 1
    assert (
        volume_resources[0].uc_securable.permission
        == apps_service.AppResourceUcSecurableUcSecurablePermission.WRITE_VOLUME
    )


def test_sync_app_resource_permissions_drops_stale_database_resource_for_autoscaling():
    typed_updates = []
    raw_updates = []

    current_app = SimpleNamespace(
        resources=[
            apps_service.AppResource(
                name="database",
                database=apps_service.AppResourceDatabase(
                    instance_name="legacy-instance",
                    database_name="databricks_postgres",
                    permission=apps_service.AppResourceDatabaseDatabasePermission.CAN_CONNECT_AND_CREATE,
                ),
            ),
            SimpleNamespace(name="keep-me", genie_space=None, uc_securable=None),
        ]
    )

    workspace_client = SimpleNamespace(
        apps=SimpleNamespace(
            get=lambda _app_name: current_app,
            update=lambda app_name, app: typed_updates.append((app_name, app)),
        ),
        api_client=SimpleNamespace(
            do=lambda method, path, body=None: (
                {
                    "databases": [
                        {
                            "name": "projects/autoscaling-project/branches/production/databases/db-123",
                            "status": {"postgres_database": "databricks_postgres"},
                        }
                    ]
                }
                if method == "GET"
                and path == "/api/2.0/postgres/projects/autoscaling-project/branches/production/databases"
                else (
                {
                    "resources": [
                        {
                            "name": "database",
                            "database": {
                                "instance_name": "legacy-instance",
                                "database_name": "databricks_postgres",
                                "permission": "CAN_CONNECT_AND_CREATE",
                            },
                        },
                        {"name": "keep-me"},
                    ]
                }
                if method == "GET"
                else raw_updates.append((path, body))
                )
            )
        ),
    )

    sync_app_resource_permissions(
        PermissionGrantConfig(
            memory_type="langgraph-short-term",
            app_name="test-app",
            project="autoscaling-project",
            branch="production",
            genie_space_ids=[],
        ),
        workspace_client=workspace_client,
    )

    if hasattr(apps_service, "AppResourcePostgres"):
        assert len(typed_updates) == 1
        app_name, app = typed_updates[0]
        assert app_name == "test-app"

        resource_names = [resource.name for resource in app.resources]
        assert "keep-me" in resource_names
        assert "database" not in resource_names
        assert "postgres" in resource_names

        postgres_resource = next(resource for resource in app.resources if resource.name == "postgres")
        assert postgres_resource.postgres.branch == "projects/autoscaling-project/branches/production"
        assert (
            postgres_resource.postgres.database
            == "projects/autoscaling-project/branches/production/databases/db-123"
        )
        assert (
            postgres_resource.postgres.permission
            == apps_service.AppResourcePostgresPostgresPermission.CAN_CONNECT_AND_CREATE
        )
        assert raw_updates == []
    else:
        assert len(raw_updates) == 1
        path, body = raw_updates[0]
        assert path == "/api/2.0/apps/test-app"

        resource_names = [resource["name"] for resource in body["resources"]]
        assert "keep-me" in resource_names
        assert "database" not in resource_names
        assert "postgres" in resource_names


def test_sync_app_resource_permissions_fails_when_autoscaling_database_missing(monkeypatch):
    monkeypatch.setattr("scripts.grant_lakebase_permissions.time.sleep", lambda _seconds: None)

    current_app = SimpleNamespace(resources=[SimpleNamespace(name="keep-me", genie_space=None, uc_securable=None)])
    workspace_client = SimpleNamespace(
        apps=SimpleNamespace(
            get=lambda _app_name: current_app,
            update=lambda app_name, app: None,
        ),
        api_client=SimpleNamespace(
            do=lambda method, path, body=None: (
                {"databases": []}
                if method == "GET"
                and path == "/api/2.0/postgres/projects/autoscaling-project/branches/production/databases"
                else {"resources": [{"name": "keep-me"}]}
            )
        ),
    )

    with pytest.raises(RuntimeError, match="did not become available within 30 seconds"):
        sync_app_resource_permissions(
            PermissionGrantConfig(
                memory_type="langgraph-short-term",
                app_name="test-app",
                project="autoscaling-project",
                branch="production",
                genie_space_ids=[],
            ),
            workspace_client=workspace_client,
        )


# ---------------------------------------------------------------------------
# grant_genie_space_manage
# ---------------------------------------------------------------------------

class _FakeApiClient:
    """Records api_client.do calls; returns canned GET ACLs keyed by behaviour."""

    def __init__(self, get_levels_by_space, patch_raises=False):
        self._get_levels = get_levels_by_space
        self._patch_raises = patch_raises
        self.calls = []

    def do(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "PATCH":
            if self._patch_raises:
                raise RuntimeError("permission denied")
            return {}
        if method == "GET":
            space_id = path.rsplit("/", 1)[-1]
            levels = self._get_levels.get(space_id, [])
            return {"access_control_list": [
                {"service_principal_name": "sp-1",
                 "all_permissions": [{"permission_level": lvl} for lvl in levels]}
            ]}
        return {}


def _cfg(space_ids):
    return PermissionGrantConfig(
        memory_type="langgraph-short-term",
        project="proj", branch="production",
        genie_space_ids=space_ids,
    )


def test_grant_genie_space_manage_success_is_verified():
    api = _FakeApiClient({"s1": ["CAN_MANAGE"]})
    ws = SimpleNamespace(api_client=api)
    results = grant_module.grant_genie_space_manage(_cfg(["s1"]), "sp-1", ws)
    assert results == [{"space_id": "s1", "granted": True, "error": None}]
    methods = [c[0] for c in api.calls]
    assert "PATCH" in methods and "GET" in methods  # read-back verification happened


def test_grant_genie_space_manage_flags_unverified_grant():
    # PATCH "succeeds" but the read-back shows the SP without CAN_MANAGE.
    api = _FakeApiClient({"s1": ["CAN_RUN"]})
    ws = SimpleNamespace(api_client=api)
    results = grant_module.grant_genie_space_manage(_cfg(["s1"]), "sp-1", ws)
    assert results[0]["granted"] is False
    assert results[0]["error"]


def test_grant_genie_space_manage_handles_patch_exception():
    api = _FakeApiClient({}, patch_raises=True)
    ws = SimpleNamespace(api_client=api)
    results = grant_module.grant_genie_space_manage(_cfg(["s1"]), "sp-1", ws)
    assert results[0]["granted"] is False
    assert "permission denied" in results[0]["error"]


def test_grant_genie_space_manage_skips_placeholder_ids():
    api = _FakeApiClient({})
    ws = SimpleNamespace(api_client=api)
    results = grant_module.grant_genie_space_manage(
        _cfg(["<comma-separated-genie-space-ids>", "  "]), "sp-1", ws
    )
    assert results == []
    assert api.calls == []  # no API calls for placeholders


def test_grant_genie_space_manage_empty_is_noop():
    api = _FakeApiClient({})
    ws = SimpleNamespace(api_client=api)
    assert grant_module.grant_genie_space_manage(_cfg([]), "sp-1", ws) == []
    assert api.calls == []
