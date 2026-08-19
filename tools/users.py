from graph.client import (
    GraphError,
    graph_delete,
    graph_get,
    graph_get_collection,
    graph_patch,
    graph_post,
    odata_string_literal,
    path_segment,
)
from config.defaults import (
    DEFAULT_DOMAIN,
    FORCE_PASSWORD_CHANGE,
    NEW_USER_TEMPORARY_PASSWORD,
)

import secrets


def generate_temporary_password():
    """Generate a unique credential; never return or log it after Graph accepts it."""
    return secrets.token_urlsafe(18) + "A1!"


def configured_new_user_temporary_password():
    """Return the operator-configured first-login credential, never a fallback."""
    if not NEW_USER_TEMPORARY_PASSWORD:
        raise ValueError(
            "New-user temporary password is not configured. Set "
            "NEBULOUS_NEW_USER_TEMPORARY_PASSWORD."
        )
    return NEW_USER_TEMPORARY_PASSWORD


def find_users(name):
    """
    Search users by display name.
    """

    return graph_get_collection(
        "/users",
        params={
            "$filter": f"startswith(displayName,{odata_string_literal(name)})",
            "$select": "id,displayName,userPrincipalName,department,accountEnabled",
        },
    )


def create_user(employee):
    """
    Creates a Microsoft 365 user.
    """

    first_name = employee["first_name"].strip()
    last_name = employee["last_name"].strip()
    department = employee["department"].strip()

    company_email = (
        f"{first_name.lower()}{last_name.lower()}@{DEFAULT_DOMAIN}"
    )

    password = configured_new_user_temporary_password()

    user_data = {
        "accountEnabled": True,
        "displayName": f"{first_name} {last_name}",
        "mailNickname": first_name.lower() + last_name.lower(),
        "userPrincipalName": company_email,
        "department": department,
        "usageLocation": "IN",
        "passwordProfile": {
            "forceChangePasswordNextSignIn": FORCE_PASSWORD_CHANGE,
            "password": password,
        },
    }

    graph_user = graph_post("/users", user_data)

    return {
        "user_id": graph_user["id"],
        "message": f"Created {graph_user['displayName']}.",
        "display_name": graph_user["displayName"],
        "company_email": company_email,
        "temporary_password": password,
        "personal_email": employee.get("personal_email"),
        "department": department,
    }



def get_user(parameters):

    user_name = parameters["user"]

    users = find_users(user_name)

    if len(users) == 0:
        return {
            "success": False,
            "message": f"No user found matching '{user_name}'."
        }

    if len(users) > 1:
        return {
            "success": False,
            "message": f"Multiple users found matching '{user_name}'.",
            "matches": users
        }

    user = users[0]

    data = graph_get(
        f"/users/{path_segment(user['id'])}",
        params={"$select": "id,displayName,userPrincipalName,mail,department,jobTitle,accountEnabled"},
    )

    return {
        "success": True,
        "message": (
            f"Found {data.get('displayName', user_name)} "
            f"({'active' if data.get('accountEnabled') else 'disabled'})."
        ),
        "data": {
            "id": data.get("id"),
            "displayName": data.get("displayName"),
            "userPrincipalName": data.get("userPrincipalName"),
            "mail": data.get("mail"),
            "department": data.get("department"),
            "jobTitle": data.get("jobTitle"),
            "accountEnabled": data.get("accountEnabled")
        }
    }


def update_user(user_id, updates):

    return graph_patch(f"/users/{path_segment(user_id)}", updates)


def disable_user(parameters):

    user_name = parameters["user"]

    users = find_users(user_name)

    if len(users) == 0:
        return {
            "success": False,
            "message": f"No user found matching '{user_name}'."
        }

    if len(users) > 1:
        return {
            "success": False,
            "message": f"Multiple users found matching '{user_name}'.",
            "matches": users
        }

    user = users[0]

    update_user(
        user["id"],
        {
            "accountEnabled": False
        }
    )

    return {
        "success": True,
        "message": f"{user['displayName']} has been disabled."
    }



def enable_user(parameters):

    user_name = parameters["user"]

    users = find_users(user_name)

    if len(users) == 0:
        return {
            "success": False,
            "message": f"No user found matching '{user_name}'."
        }

    if len(users) > 1:
        return {
            "success": False,
            "message": f"Multiple users found matching '{user_name}'.",
            "matches": users
        }

    user = users[0]

    update_user(
        user["id"],
        {
            "accountEnabled": True
        }
    )

    return {
        "success": True,
        "message": f"{user['displayName']} has been enabled."
    }


def reset_password(parameters):

    user_name = parameters["user"]

    users = find_users(user_name)

    if len(users) == 0:
        return {
            "success": False,
            "message": f"No user found matching '{user_name}'."
        }

    if len(users) > 1:
        return {
            "success": False,
            "message": f"Multiple users found matching '{user_name}'.",
            "matches": users
        }

    user = users[0]
    password = configured_new_user_temporary_password()

    update_user(
        user["id"],
        {
            "passwordProfile": {
                "password": password,
                "forceChangePasswordNextSignIn": FORCE_PASSWORD_CHANGE,
            }
        }
    )

    return {
        "success": True,
        "message": f"Password reset successfully for {user['displayName']}.",
        "temporary_password": password,
    }


def _license_names(licenses):
    return [
        str(license.get("skuPartNumber", "Unknown license")).replace("_", " ").title()
        for license in licenses
        if isinstance(license, dict)
    ]


def _account_report_summary(reports, department=None):
    if len(reports) == 1:
        report = reports[0]
        licenses = ", ".join(report["licenses"]) if report["licenses"] else "None"
        if not report["licenses_available"]:
            licenses = "Unavailable"
        return (
            f"Account report for {report['name']}:\n"
            f"Email: {report['email'] or 'Not available'}\n"
            f"Department: {report['department'] or 'Not specified'}\n"
            f"Job title: {report['job_title'] or 'Not specified'}\n"
            f"Account: {'Enabled' if report['account_enabled'] else 'Disabled'}\n"
            f"Licenses: {licenses}"
        )

    scope = f" in {department}" if department else ""
    lines = [f"Account report for {len(reports)} users{scope}:"]
    for report in reports:
        licenses = ", ".join(report["licenses"]) if report["licenses"] else "None"
        if not report["licenses_available"]:
            licenses = "Unavailable"
        lines.append(
            f"- {report['name']} — {report['email'] or 'No email'}; "
            f"{report['job_title'] or 'No job title'}; "
            f"{'enabled' if report['account_enabled'] else 'disabled'}; "
            f"licenses: {licenses}."
        )
    return "\n".join(lines)


def get_account_report(parameters):
    """Return a read-only account report for one user or one department."""
    user_name = parameters.get("user")
    department = parameters.get("department")

    if user_name:
        users = find_users(user_name)
        if len(users) == 0:
            return {"success": False, "message": f"No user found matching '{user_name}'."}
        if len(users) > 1:
            return {"success": False, "message": f"Multiple users found matching '{user_name}'.", "matches": users}
    else:
        users = graph_get_collection(
            "/users",
            params={
                "$filter": f"department eq {odata_string_literal(department)}",
                "$select": "id,displayName,userPrincipalName,mail,department,jobTitle,accountEnabled",
                "$orderby": "displayName",
            },
        )
        if not users:
            return {"success": False, "message": f"No users found in the {department} department."}

    reports = []
    for user in users:
        profile = graph_get(
            f"/users/{path_segment(user['id'])}",
            params={"$select": "id,displayName,userPrincipalName,mail,department,jobTitle,accountEnabled"},
        )
        licenses_available = True
        try:
            licenses = graph_get(f"/users/{path_segment(user['id'])}/licenseDetails").get("value", [])
        except GraphError:
            # A license lookup enriches the report but should not suppress the
            # profile data the operator asked to read.
            licenses = []
            licenses_available = False
        reports.append({
            "name": profile.get("displayName", user.get("displayName", "Unknown user")),
            "email": profile.get("mail") or profile.get("userPrincipalName", ""),
            "department": profile.get("department", ""),
            "job_title": profile.get("jobTitle", ""),
            "account_enabled": bool(profile.get("accountEnabled", False)),
            "licenses": _license_names(licenses),
            "licenses_available": licenses_available,
        })

    return {
        "success": True,
        "message": _account_report_summary(reports, department=department),
        "reports": reports,
    }

def revoke_sessions(parameters):

    user_name = parameters["user"]

    users = find_users(user_name)

    if len(users) == 0:
        return {
            "success": False,
            "message": f"No user found matching '{user_name}'."
        }

    if len(users) > 1:
        return {
            "success": False,
            "message": f"Multiple users found matching '{user_name}'.",
            "matches": users
        }

    user = users[0]

    graph_post(f"/users/{path_segment(user['id'])}/revokeSignInSessions", {})

    return {
        "success": True,
        "message": f"All sign-in sessions revoked for {user['displayName']}."
    }


def delete_user(parameters):

    user_name = parameters["user"]

    users = find_users(user_name)

    if len(users) == 0:
        return {
            "success": False,
            "message": f"No user found matching '{user_name}'."
        }

    if len(users) > 1:
        return {
            "success": False,
            "message": f"Multiple users found matching '{user_name}'.",
            "matches": users
        }

    user = users[0]

    graph_delete(f"/users/{path_segment(user['id'])}")

    return {
        "success": True,
        "message": f"{user['displayName']} has been deleted."
    }



def list_users(parameters=None):

    users = graph_get_collection(
        "/users",
        params={
            "$select": "id,displayName,userPrincipalName,department,accountEnabled",
            "$orderby": "displayName",
        },
    )

    return {
        "success": True,
        "message": f"Found {len(users)} users.",
        "users": users
    }
