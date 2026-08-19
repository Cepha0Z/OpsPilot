WRITE_TOOLS = {

    "create_user": False,

    "assign_license": False,

    "send_email": False,

    "disable_user": True,

    "enable_user": False,

    "reset_password": True,

    "delete_user": True,


}


def requires_approval(tool):

    return WRITE_TOOLS.get(tool, False)
