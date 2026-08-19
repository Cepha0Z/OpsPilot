from tools.users import find_users
from config.licenses import LICENSES
from graph.client import GraphError, graph_get, graph_post, path_segment
from audit_log import log_event


# ============================================================
# GRAPH LICENSE UPDATE
# ============================================================

def graph_update_license(user_id, add=None, remove=None):
    """
    Add/remove Microsoft 365 licenses for a specific Graph user ID.
    """

    data = {
        "addLicenses": add or [],
        "removeLicenses": remove or [],
    }

    return graph_post(f"/users/{path_segment(user_id)}/assignLicense", data)


# ============================================================
# ASSIGN LICENSE
# ============================================================

def assign_license(parameters):
    """
    Assign a Microsoft 365 license to a user.

    Preferred:
        {
            "user_id": "GRAPH-USER-ID",
            "license": "Flow Free"
        }

    Backwards compatible:
        {
            "user": "John Doe",
            "license": "Flow Free"
        }

    The user_id path is preferred because newly-created
    Microsoft 365 users may not immediately be discoverable
    through a display-name search.
    """

    # --------------------------------------------------------
    # Validate license parameter
    # --------------------------------------------------------

    if not isinstance(parameters, dict):

        return {
            "success": False,
            "message": "License parameters must be an object.",
        }

    if "license" not in parameters:

        return {
            "success": False,
            "message": "No license was specified.",
        }

    requested_license = parameters["license"]

    if not isinstance(requested_license, str):

        return {
            "success": False,
            "message": "License name must be a string.",
        }

    license_name = requested_license.strip().lower()

    # --------------------------------------------------------
    # Resolve license SKU
    # --------------------------------------------------------

    if license_name not in LICENSES:

        return {
            "success": False,
            "message": (
                f"Unknown license '{requested_license}'."
            ),
        }

    sku_id = LICENSES[license_name]

    # --------------------------------------------------------
    # Resolve user
    # --------------------------------------------------------

    user_id = parameters.get("user_id")

    user_display_name = parameters.get(
        "display_name"
    )

    # ========================================================
    # PREFERRED:
    # EXACT GRAPH USER ID
    # ========================================================

    if user_id:

        if not isinstance(user_id, str):

            return {
                "success": False,
                "message": "user_id must be a string.",
            }

        user_id = user_id.strip()

        if not user_id:

            return {
                "success": False,
                "message": "user_id cannot be empty.",
            }

        log_event("license.user_resolved_by_id")

    # ========================================================
    # FALLBACK:
    # DISPLAY NAME SEARCH
    # ========================================================

    else:

        user_name = parameters.get("user")

        if not user_name:

            return {
                "success": False,
                "message": (
                    "No user_id or user name was provided."
                ),
            }

        if not isinstance(user_name, str):

            return {
                "success": False,
                "message": "User name must be a string.",
            }

        user_name = user_name.strip()

        if not user_name:

            return {
                "success": False,
                "message": "User name cannot be empty.",
            }

        log_event("license.user_lookup")

        users = find_users(user_name)

        # ----------------------------------------------------
        # No user
        # ----------------------------------------------------

        if len(users) == 0:

            return {
                "success": False,
                "message": (
                    f"No user found matching '{user_name}'."
                ),
            }

        # ----------------------------------------------------
        # Multiple users
        # ----------------------------------------------------

        if len(users) > 1:

            return {
                "success": False,
                "message": (
                    f"Multiple users found matching "
                    f"'{user_name}'."
                ),
                "matches": users,
            }

        user = users[0]

        user_id = user["id"]

        user_display_name = user.get(
            "displayName",
            user_name
        )

    # ========================================================
    # ASSIGN LICENSE
    # ========================================================

    log_event("license.assignment_started")

    try:

        graph_update_license(
            user_id,
            add=[
                {
                    "skuId": sku_id,
                    "disabledPlans": [],
                }
            ],
        )

    except GraphError as e:

        log_event("license.assignment_graph_failed")

        return {
            "success": False,
            "message": (
                f"Microsoft Graph failed to assign "
                f"'{requested_license}'."
            ),
            "error_code": e.code,
            "retryable": e.retryable,
            "user_id": user_id,
        }

    except Exception:

        log_event("license.assignment_failed")

        return {
            "success": False,
            "message": (
                f"Failed to assign "
                f"'{requested_license}'."
            ),
            "error_code": "license_assignment_failed",
            "user_id": user_id,
        }

    # ========================================================
    # SUCCESS
    # ========================================================

    log_event("license.assignment_completed")

    return {
        "success": True,
        "message": (
            f"{requested_license} assigned to "
            f"{user_display_name or user_id}."
        ),
        "user_id": user_id,
        "license": requested_license,
        "sku_id": sku_id,
    }


# ============================================================
# LIST AVAILABLE LICENSES
# ============================================================

def list_available_licenses(parameters=None):
    """
    Return licenses currently available in the Microsoft 365
    tenant.
    """

    data = graph_get("/subscribedSkus")

    licenses = data.get("value", [])
    return {
        "success": True,
        "message": f"Found {len(licenses)} available tenant license(s).",
        "licenses": licenses,
    }


# ============================================================
# LIST USER LICENSES
# ============================================================

def list_user_licenses(parameters):
    """
    List licenses assigned to a user.

    Supports:

        {
            "user_id": "GRAPH-USER-ID"
        }

    or:

        {
            "user": "John Doe"
        }
    """

    if not isinstance(parameters, dict):

        return {
            "success": False,
            "message": "License parameters must be an object.",
        }

    # --------------------------------------------------------
    # Preferred exact user ID
    # --------------------------------------------------------

    user_id = parameters.get("user_id")

    user_display_name = parameters.get(
        "display_name"
    )

    # --------------------------------------------------------
    # Fallback name lookup
    # --------------------------------------------------------

    if not user_id:

        user_name = parameters.get("user")

        if not user_name:

            return {
                "success": False,
                "message": (
                    "No user_id or user name was provided."
                ),
            }

        users = find_users(user_name)

        if len(users) == 0:

            return {
                "success": False,
                "message": (
                    f"No user found matching '{user_name}'."
                ),
            }

        if len(users) > 1:

            return {
                "success": False,
                "message": (
                    f"Multiple users found matching "
                    f"'{user_name}'."
                ),
                "matches": users,
            }

        user = users[0]

        user_id = user["id"]

        user_display_name = user.get(
            "displayName",
            user_name
        )

    # --------------------------------------------------------
    # Graph request
    # --------------------------------------------------------

    licenses = graph_get(
        f"/users/{path_segment(user_id)}/licenseDetails"
    ).get(
        "value",
        []
    )

    license_names = [
        str(license.get("skuPartNumber", "Unknown license"))
        .replace("_", " ")
        .title()
        for license in licenses
        if isinstance(license, dict)
    ]
    license_count = len(license_names)
    subject = user_display_name or "The user"

    if license_count == 0:
        public_summary = f"{subject} has no assigned licenses."
    elif license_count == 1:
        public_summary = f"{subject} has 1 license: {license_names[0]}."
    else:
        public_summary = (
            f"{subject} has {license_count} licenses: "
            f"{', '.join(license_names)}."
        )

    return {
        "success": True,
        "message": public_summary,
        "user_id": user_id,
        "user": user_display_name,
        "licenses": licenses,
    }
