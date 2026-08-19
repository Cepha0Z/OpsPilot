from tools.users import create_user
from tools.mail import send_email
from config.defaults import DEFAULT_SENDER


def onboard_employee(employee):

    steps = []

    # Create Microsoft 365 account
    user = create_user(employee)
    steps.append("Microsoft 365 account created")

    # Build welcome email
    body = f"""
Hello {employee["first_name"]},

Welcome to Nebulous Design!

Your Microsoft 365 account has been created.

Email:
{user["company_email"]}

Temporary Password:
{user["temporary_password"]}

Please sign in and change your password immediately.

Regards,
Nebulous Design
"""

    # Send welcome email
    send_email(
        sender=DEFAULT_SENDER,
        recipient=user["personal_email"],
        subject="Welcome to Nebulous Design",
        body=body
    )

    steps.append("Welcome email sent")

    return {
        "success": True,
        "steps": steps,
        "employee": user
    }