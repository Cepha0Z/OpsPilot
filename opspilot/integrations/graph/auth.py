import msal
from ...config.defaults import TENANT_ID, CLIENT_ID, CLIENT_SECRET


_app = None


def get_msal_app():
    """Create one MSAL client per process so its built-in token cache is reused."""
    global _app
    if _app is None:
        if not all((TENANT_ID, CLIENT_ID, CLIENT_SECRET)):
            raise RuntimeError("Microsoft Graph credentials are not configured.")

        authority = f"https://login.microsoftonline.com/{TENANT_ID}"
        _app = msal.ConfidentialClientApplication(
            CLIENT_ID,
            authority=authority,
            client_credential=CLIENT_SECRET,
        )
    return _app


def get_access_token():

    app = get_msal_app()

    result = app.acquire_token_for_client(
        scopes=[
            "https://graph.microsoft.com/.default"
        ]
    )

    if "access_token" not in result:
        raise RuntimeError("Microsoft Graph token acquisition failed.")

    return result["access_token"]
