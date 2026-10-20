"""The Helm chart, rendered with `helm template` (skipped when helm is not installed)."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "deploy" / "helm" / "iceberg-data-platform"
HELM = shutil.which("helm") or (ROOT / ".local" / "bin" / "helm")
pytestmark = pytest.mark.skipif(not Path(HELM).exists(), reason="helm is not installed")


def render(*settings):
    command = [str(HELM), "template", "t", str(CHART), "-n", "iceberg", "--set", "baseDomain=example.com"]
    for setting in settings:
        command += ["--set", setting]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def env(docs, kind, name, container=None):
    resource = next(d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name)
    spec = resource["spec"]["template"]["spec"]
    containers = spec.get("initContainers", []) + spec["containers"]
    found = next(c for c in containers if container in (None, c["name"]))
    return {e["name"]: e.get("value", e.get("valueFrom")) for e in found["env"]}


EXTERNAL = ("postgres.external.enabled=true", "postgres.external.host=10.1.2.3",
            "postgres.external.existingSecret=pg", "postgres.external.polaris.database=catalog")


def test_bundled_postgres_is_the_default():
    docs = render()
    assert any(d["kind"] == "StatefulSet" and d["metadata"]["name"] == "postgres" for d in docs)
    polaris = env(docs, "Deployment", "polaris", "polaris")
    assert polaris["QUARKUS_DATASOURCE_JDBC_URL"].startswith("jdbc:postgresql://postgres:5432/polaris?")
    assert polaris["QUARKUS_DATASOURCE_PASSWORD"]["secretKeyRef"] == {"name": "iceberg-platform",
                                                                      "key": "POSTGRES_PASSWORD"}


def test_external_postgres_replaces_the_bundled_one():
    docs = render(*EXTERNAL)
    assert not any(d["metadata"]["name"] in ("postgres", "postgres-init") for d in docs)
    for container in ("bootstrap", "polaris"):
        polaris = env(docs, "Deployment", "polaris", container)
        assert polaris["QUARKUS_DATASOURCE_JDBC_URL"] == (
            "jdbc:postgresql://10.1.2.3:5432/catalog?currentSchema=public&sslmode=require")
        assert polaris["QUARKUS_DATASOURCE_PASSWORD"]["secretKeyRef"] == {"name": "pg", "key": "POLARIS_DB_PASSWORD"}
    keycloak = env(docs, "Deployment", "keycloak", "keycloak")
    assert keycloak["KC_DB_URL_HOST"] == "10.1.2.3" and keycloak["KC_DB_URL_PROPERTIES"] == "?sslmode=require"
    assert keycloak["KC_DB_PASSWORD"]["secretKeyRef"] == {"name": "pg", "key": "KEYCLOAK_DB_PASSWORD"}
    monitor = env(docs, "Deployment", "monitor")
    assert (monitor["PGHOST"], monitor["PGDATABASE"], monitor["PGSSLMODE"]) == ("10.1.2.3", "catalog", "require")
    assert not any(d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "postgres" for d in docs)


def test_external_postgres_needs_its_host_and_secret():
    with pytest.raises(RuntimeError, match="postgres.external needs host and existingSecret"):
        render("postgres.external.enabled=true", "postgres.external.host=10.1.2.3")


def test_isolated_namespaces_cannot_reach_sign_in_or_the_portals():
    docs = render()
    notebooks = next(d for d in docs if d["kind"] == "Namespace")
    assert notebooks["metadata"]["labels"]["io.iceberg-platform.isolated"] == "true"
    policies = {d["metadata"]["name"]: d["spec"] for d in docs if d["kind"] == "NetworkPolicy"}
    for name in ("keycloak", "portal", "users"):
        allowed = policies[name]["ingress"][0]["from"]
        assert allowed[0]["namespaceSelector"]["matchExpressions"] == [
            {"key": "io.iceberg-platform.isolated", "operator": "DoesNotExist"}]
    # Only the user portal also takes notebooks, for their session token.
    assert len(policies["users"]["ingress"][0]["from"]) == 2 and len(policies["keycloak"]["ingress"][0]["from"]) == 1


def test_external_polaris_keeps_the_bridge_names_and_writes_the_official_values():
    docs = render("polaris.external.enabled=true", "imagePullSecrets[0].name=mirror")
    assert not any(d["kind"] == "Deployment" and d["metadata"]["name"] == "polaris" for d in docs)
    services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    for name in ("polaris", "polaris-control-plane"):
        assert services[name]["spec"]["selector"] == {"app.kubernetes.io/part-of": "iceberg-data-platform",
                                                      "app.kubernetes.io/component": "polaris"}
    job = next(d for d in docs if d["kind"] == "Job" and d["metadata"]["name"] == "polaris-bootstrap")
    assert job["metadata"]["annotations"]["helm.sh/hook-weight"] == "-5"
    assert job["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/component"] == "polaris-bootstrap"
    values = yaml.safe_load(next(d for d in docs if d["kind"] == "ConfigMap"
                                 and d["metadata"]["name"] == "iceberg-polaris-values")["data"]["values.yaml"])
    assert values["podLabels"]["app.kubernetes.io/component"] == "polaris"
    assert values["imagePullSecrets"] == ["mirror"]
    assert values["persistence"]["relationalJdbc"]["secret"]["name"] == "iceberg-polaris-db"
    assert values["advancedConfig"]["quarkus.oidc.token.issuer"] == "https://auth.example.com/realms/iceberg"
    database = next(d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == "iceberg-polaris-db")
    assert database["stringData"]["jdbcUrl"].startswith("jdbc:postgresql://postgres:5432/polaris?")
    assert database["stringData"]["password"]
