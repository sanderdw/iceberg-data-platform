"""Pilot user lifecycle. Polaris holds the resumable operation record, never passwords."""

import os
import secrets
import time
from typing import Literal

import httpx
from fastapi import Query
from pydantic import Field

from server.models import Input, ServiceError, UserInput
from server.polaris import enc

PREFIX = "portal.identity-"


class CreateIdentity(UserInput):
    role: Literal["reader", "writer", "admin"]
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)


class LinkIdentity(UserInput):
    role: Literal["reader", "writer", "admin"]
    subject: str = Field(min_length=1, max_length=128)


class ExistingIdentity(Input):
    subject: str = Field(min_length=1, max_length=128)


class KeycloakAdmin:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.issuer = env["OIDC_ISSUER"].rstrip("/")
        internal = env["OIDC_INTERNAL_ISSUER"].rstrip("/")
        base, realm = internal.rsplit("/realms/", 1)
        self.realm = realm
        self.url = base + "/admin/realms/" + enc(realm)
        self.token_url = internal + "/protocol/openid-connect/token"
        self.secret = env["OIDC_MANAGEMENT_SECRET"]
        self.http = httpx.Client(timeout=15, trust_env=False)
        self.token, self.until = None, 0

    def close(self):
        self.http.close()

    def request(self, path, method="GET", body=None, params=None, *, missing_ok=False):
        try:
            if self.until <= time.monotonic():
                response = self.http.post(self.token_url, data={
                    "grant_type": "client_credentials", "client_id": "iceberg-provisioner",
                    "client_secret": self.secret,
                })
                response.raise_for_status()
                result = response.json()
                self.token = result["access_token"]
                self.until = time.monotonic() + max(0, result["expires_in"] - 30)
            response = self.http.request(method, self.url + path, json=body, params=params,
                                         headers={"Authorization": "Bearer " + self.token})
            if response.status_code == 404 and missing_ok:
                return None
            if response.status_code == 409:
                raise ServiceError(409, "That Keycloak account already exists. Use Link existing account.")
            if response.status_code == 404:
                raise ServiceError(404, "The Keycloak account no longer exists.")
            if response.status_code == 400:
                raise ServiceError(422, "Keycloak rejected the account details. Check its user profile requirements.")
            if response.status_code == 401:
                self.until = 0
            response.raise_for_status()
            return response.json() if response.content else None
        except (httpx.HTTPError, ValueError, KeyError):
            raise ServiceError(503, "Keycloak user management is unavailable. Try again after it recovers.") from None

    def account(self, subject, *, missing_ok=False):
        return self.request("/users/" + enc(subject), missing_ok=missing_ok)

    def find(self, username):
        return self.request("/users", params={"username": username, "exact": "true", "max": 20})

    def administrator(self, subject):
        clients = self.request("/clients", params={"clientId": "iceberg-admin"})
        if len(clients) != 1:
            raise ServiceError(503, "The platform administrator client is unavailable.")
        roles = self.request(f"/users/{enc(subject)}/role-mappings/clients/{enc(clients[0]['id'])}/composite")
        return any(role["name"] == "platform-admin" for role in roles)

    @staticmethod
    def binding(account):
        names = account.get("attributes", {}).get("polaris_name", [])
        return names[0] if len(names) == 1 else (None if not names else "invalid")


class UserManagement:
    def __init__(self, keycloak=None):
        self.kc = keycloak or KeycloakAdmin()

    def close(self):
        self.kc.close()

    def users(self, provider):
        principals = provider.management("/principals")["principals"]
        result = []
        for principal in principals:
            if not provider.managed(principal):
                continue
            props = principal["properties"]
            linked = bool(props.get("portal.oidc-subject"))
            result.append({**provider.user(principal), "identity": {
                "status": props.get(PREFIX + "status", "linked" if linked else "unlinked"),
                "subject": props.get("portal.oidc-subject"),
                "managed": props.get(PREFIX + "mode") == "new",
            }})
        return result

    @staticmethod
    def principal(provider, id):
        principal = provider.require("/principals/" + enc(id))
        if not provider.managed(principal):
            raise ServiceError(404, "This is not a platform user.")
        return principal

    @staticmethod
    def update(provider, principal, changes):
        return provider.update_properties("/principals/" + enc(principal["name"]),
                                          {**principal["properties"], **changes})

    def available(self, provider, account, principal_name=None):
        if account.get("serviceAccountClientId") or not account.get("enabled"):
            raise ServiceError(409, "Select an enabled human account.")
        if self.kc.binding(account) not in (None, principal_name):
            raise ServiceError(409, "This Keycloak account is already linked to another platform user.")
        for principal in provider.management("/principals")["principals"]:
            props = principal.get("properties", {})
            if (props.get("portal.oidc-subject") == account["id"]
                    and props.get("portal.oidc-issuer") == self.kc.issuer
                    and principal["name"] != principal_name):
                raise ServiceError(409, "This Keycloak account is already linked to another platform user.")

    def create(self, provider, data, mode):
        provider.require_teams(data.teams)
        properties = {PREFIX + "status": "pending", PREFIX + "mode": mode,
                      "portal.oidc-issuer": self.kc.issuer}
        if mode == "new":
            if self.kc.find(data.name):
                raise ServiceError(409, "That Keycloak username already exists. Use Link existing account.")
            properties.update({PREFIX + "email": data.email, PREFIX + "first-name": data.first_name,
                               PREFIX + "last-name": data.last_name})
        else:
            account = self.kc.account(data.subject)
            self.available(provider, account)
            properties["portal.oidc-subject"] = account["id"]
        result = provider.create_user(data, identity_properties=properties, activate=False)
        return self.resume(provider, result["user"]["id"])

    def link(self, provider, id, subject):
        principal = self.principal(provider, id)
        props = principal["properties"]
        if props.get("portal.oidc-subject") or props.get(PREFIX + "status"):
            raise ServiceError(409, "This user already has an identity operation. Use Retry setup or Revoke.")
        account = self.kc.account(subject)
        self.available(provider, account, id)
        self.update(provider, principal, {PREFIX + "status": "pending", PREFIX + "mode": "link",
                                         "portal.oidc-subject": subject, "portal.oidc-issuer": self.kc.issuer})
        return self.resume(provider, id)

    def resume(self, provider, id):
        principal = self.principal(provider, id)
        props = principal["properties"]
        if props.get(PREFIX + "status") == "linked":
            return {"user": provider.user(principal), "identity": {"status": "linked"}}
        if props.get(PREFIX + "status") != "pending":
            raise ServiceError(409, "This user has no incomplete setup.")
        role_path = f"/principals/{enc(id)}/principal-roles/{enc(id)}"
        provider.remove(role_path)
        password = None
        try:
            # Recover a principal whose original Polaris create request lost its
            # response before role provisioning finished. Grants are idempotent.
            user = provider.user(principal)
            provider.require_teams(user["teams"])
            try:
                provider.management("/principal-roles/" + enc(id))
            except ServiceError as exc:
                if exc.status != 404:
                    raise
                provider.management("/principal-roles", "POST", {"principalRole": {"name": id}})
            provider.sync_access(user, {}, provider.access(user, provider.list_databases()), [])
            subject = props.get("portal.oidc-subject")
            if subject:
                account = self.kc.account(subject)
            else:
                accounts = self.kc.find(props["portal.name"])
                if not accounts:
                    self.kc.request("/users", "POST", {
                        "username": props["portal.name"], "email": props[PREFIX + "email"],
                        "firstName": props[PREFIX + "first-name"], "lastName": props[PREFIX + "last-name"],
                        "enabled": False, "attributes": {"iceberg_provisioning_id": [id]},
                    })
                    accounts = self.kc.find(props["portal.name"])
                if len(accounts) != 1 or accounts[0].get("attributes", {}).get("iceberg_provisioning_id") != [id]:
                    raise ServiceError(409, "Username belongs to another account. Revoke this incomplete setup and link explicitly.")
                account = accounts[0]
                subject = account["id"]
                principal = self.update(provider, principal, {"portal.oidc-subject": subject})
            if props[PREFIX + "mode"] == "new":
                if account.get("attributes", {}).get("iceberg_provisioning_id") != [id]:
                    raise ServiceError(409, "This account was not created by this setup operation.")
            else:
                self.available(provider, account, id)
            if self.kc.binding(account) not in (None, id):
                raise ServiceError(409, "This account has been linked elsewhere. Revoke the incomplete setup.")
            attributes = {**account.get("attributes", {}), "polaris_id": ["0"], "polaris_name": [id]}
            self.kc.request("/users/" + enc(subject), "PUT", {**account, "attributes": attributes})
            if props[PREFIX + "mode"] == "new":
                password = secrets.token_urlsafe(24)
                self.kc.request(f"/users/{enc(subject)}/reset-password", "PUT",
                                {"type": "password", "value": password, "temporary": True})
                self.kc.request("/users/" + enc(subject), "PUT", {"enabled": True})
            provider.management(f"/principals/{enc(id)}/principal-roles", "PUT", {"principalRole": {"name": id}})
            principal = self.update(provider, principal, {PREFIX + "status": "linked"})
        except Exception:  # noqa: BLE001 - Keep incomplete operations visible and fail closed where possible.
            # A successful final checkpoint can lose its response. Read it back
            # before undoing the role binding that completed this operation.
            try:
                committed = self.principal(provider, id)
                if committed["properties"].get(PREFIX + "status") == "linked":
                    return {"user": provider.user(committed), "identity": {
                        "status": "linked", "username": account["username"],
                        **({"temporaryPassword": password} if password else {}),
                    }}
            except ServiceError:
                pass
            try:
                self.update(provider, principal, {PREFIX + "status": "pending"})
                provider.remove(role_path)
            except ServiceError:
                pass
            raise ServiceError(503, "Setup is incomplete. Refresh Users and choose Retry setup or Revoke.") from None
        return {"user": provider.user(principal), "identity": {"status": "linked", "username": account["username"],
                **({"temporaryPassword": password} if password else {})}}

    def reset_password(self, provider, id):
        principal = self.principal(provider, id)
        props = principal["properties"]
        if props.get(PREFIX + "mode") != "new" or props.get(PREFIX + "status") != "linked":
            raise ServiceError(409, "Password reset here is only available for accounts created by this portal.")
        account = self.kc.account(props["portal.oidc-subject"])
        if account.get("attributes", {}).get("iceberg_provisioning_id") != [id] or self.kc.binding(account) != id:
            raise ServiceError(409, "Account ownership has changed. Use Keycloak administration.")
        password = secrets.token_urlsafe(24)
        self.kc.request(f"/users/{enc(account['id'])}/reset-password", "PUT",
                        {"type": "password", "value": password, "temporary": True})
        return {"user": provider.user(principal), "identity": {"username": account["username"],
                "temporaryPassword": password, "status": "linked"}}

    def revoke(self, provider, id):
        principal = self.principal(provider, id)
        props = principal["properties"]
        subject = props.get("portal.oidc-subject")
        # Deleting a data identity must not silently remove an administrator or lock out the operator.
        if subject and props.get(PREFIX + "status") != "revoking":
            account = self.kc.account(subject, missing_ok=True)
            if account and self.kc.administrator(subject):
                raise ServiceError(409, "Remove this account's platform-administrator role in Keycloak before revoking it.")
        self.update(provider, principal, {PREFIX + "status": "revoking"})
        provider.remove(f"/principals/{enc(id)}/principal-roles/{enc(id)}")
        provider.remove(f"/principal-roles/{enc(id)}")
        if key := props.get("portal.bucket-access-key"):
            provider.storage.delete_user(key)
        try:
            if not subject and props.get(PREFIX + "mode") == "new":
                accounts = self.kc.find(props["portal.name"])
                account = next((a for a in accounts if a.get("attributes", {}).get("iceberg_provisioning_id") == [id]), None)
                subject = account["id"] if account else None
            account = self.kc.account(subject, missing_ok=True) if subject else None
            if account and self.kc.binding(account) == id:
                attributes = {k: v for k, v in account.get("attributes", {}).items()
                              if k not in ("polaris_id", "polaris_name", "iceberg_provisioning_id")}
                self.kc.request("/users/" + enc(subject), "PUT", {**account, "attributes": attributes})
        except ServiceError:
            raise ServiceError(503, "Data access is revoked. Keycloak cleanup is incomplete; choose Revoke again to retry.") from None
        provider.remove("/principals/" + enc(id))

    def install(self, app, provider):
        @app.get("/api/identity/accounts")
        def accounts(username: str = Query(min_length=1, max_length=254)):
            return [{"id": a["id"], "username": a["username"], "email": a.get("email", ""),
                     "enabled": a.get("enabled", False), "linked": bool(self.kc.binding(a))}
                    for a in self.kc.find(username) if not a.get("serviceAccountClientId")]

        @app.post("/api/identity/users", status_code=201)
        def create(data: CreateIdentity):
            return self.create(provider, data, "new")

        @app.post("/api/identity/links", status_code=201)
        def link(data: LinkIdentity):
            return self.create(provider, data, "link")

        @app.post("/api/users/{id}/identity")
        def link_user(id: str, data: ExistingIdentity):
            return self.link(provider, id, data.subject)

        @app.post("/api/users/{id}/identity/retry")
        def retry(id: str):
            return self.resume(provider, id)

        @app.post("/api/users/{id}/identity/password")
        def reset(id: str):
            return self.reset_password(provider, id)
