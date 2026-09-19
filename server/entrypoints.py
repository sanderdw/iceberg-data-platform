"""Application entrypoints shared by source and published container images."""

import os


def identity(portal):
    from .oidc import OIDC

    env = dict(os.environ)
    env.setdefault("OIDC_CLIENT_ID", "iceberg-admin" if portal == "admin" else "iceberg-users")
    env.setdefault("OIDC_CLIENT_SECRET", env.get(f"OIDC_{'PORTAL' if portal == 'admin' else 'USERS'}_SECRET", ""))
    env.setdefault("OIDC_ORIGIN", env.get(f"{'PORTAL' if portal == 'admin' else 'USER'}_ORIGIN", ""))
    if not env["OIDC_CLIENT_SECRET"]:
        raise RuntimeError("Keycloak client secret is required")
    return OIDC(env)


def admin():
    from .app import create_app

    oidc = identity("admin")
    from .identity import UserManagement

    return create_app(oidc=oidc, secure_cookie=oidc.secure,
                      session_cookie="iceberg_admin", user_management=UserManagement())


def users():
    from user_portal.app import create_app

    oidc = identity("users")
    return create_app(oidc=oidc, session_cookie="iceberg_user")
