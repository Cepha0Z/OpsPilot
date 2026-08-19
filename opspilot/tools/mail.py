from ..integrations.graph.client import graph_get, graph_get_collection, graph_post, path_segment


DEFAULT_MAILBOX = "studio1@nebulousdesign.com"
MESSAGE_FIELDS = "id,subject,from,receivedDateTime,isRead"


def _mailbox(parameters):
    return path_segment(parameters.get("mailbox", DEFAULT_MAILBOX))


def _message_summary(mail):
    sender = (mail.get("from") or {}).get("emailAddress") or {}
    return {
        "id": mail.get("id"),
        "subject": mail.get("subject", ""),
        "from": sender.get("address", ""),
        "received": mail.get("receivedDateTime", ""),
        "is_read": bool(mail.get("isRead", False)),
    }


def _list_messages(parameters, extra_params=None):
    mailbox = _mailbox(parameters)
    count = parameters.get("count", 10)
    if not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer.")
    params = {
        "$top": count,
        "$orderby": "receivedDateTime desc",
        "$select": MESSAGE_FIELDS,
    }
    if extra_params:
        params.update(extra_params)
    messages = graph_get_collection(f"/users/{mailbox}/messages", params=params, limit=count)
    return mailbox, [_message_summary(mail) for mail in messages]


def list_recent_emails(parameters):
    mailbox, emails = _list_messages(parameters)
    return {
        "success": True,
        "message": f"Found {len(emails)} recent email(s).",
        "mailbox": mailbox,
        "emails": emails,
    }


def list_unread_emails(parameters):
    mailbox, emails = _list_messages(parameters, {"$filter": "isRead eq false"})
    return {
        "success": True,
        "message": f"Found {len(emails)} unread email(s).",
        "mailbox": mailbox,
        "emails": emails,
    }


def get_email(parameters):
    mailbox = _mailbox(parameters)
    email = graph_get(
        f"/users/{mailbox}/messages/{path_segment(parameters['email_id'])}",
        params={"$select": "id,subject,from,toRecipients,receivedDateTime,body,isRead"},
    )
    sender = (email.get("from") or {}).get("emailAddress") or {}
    body = email.get("body") or {}
    return {
        "success": True,
        "message": f"Retrieved email: {email.get('subject', '(No subject)')}",
        "email": {
            "id": email.get("id"),
            "subject": email.get("subject", ""),
            "from": sender.get("address", ""),
            "received": email.get("receivedDateTime", ""),
            "body": body.get("content", ""),
            "is_read": bool(email.get("isRead", False)),
        },
    }


def search_emails(parameters):
    mailbox = _mailbox(parameters)
    query = parameters["query"]
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string.")
    count = parameters.get("count", 10)
    messages = graph_get_collection(
        f"/users/{mailbox}/messages",
        params={"$search": f'"{query}"', "$top": count, "$select": MESSAGE_FIELDS},
        limit=count,
        headers={"ConsistencyLevel": "eventual"},
    )
    emails = [_message_summary(mail) for mail in messages]
    return {
        "success": True,
        "message": f"Found {len(emails)} email(s) matching the search.",
        "emails": emails,
    }


def _message(recipient, subject, body):
    return {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": recipient}}],
    }


def reply_email(parameters):
    mailbox = _mailbox(parameters)
    graph_post(
        f"/users/{mailbox}/messages/{path_segment(parameters['email_id'])}/reply",
        {"message": {"body": {"contentType": "Text", "content": parameters["body"]}}},
    )
    return {"success": True, "message": "Reply sent successfully."}


def draft_email(parameters):
    draft = graph_post(
        f"/users/{_mailbox(parameters)}/messages",
        _message(parameters["recipient"], parameters["subject"], parameters["body"]),
    )
    return {
        "success": True,
        "message": f"Email draft created for {parameters['recipient']}.",
        "draft_id": draft["id"],
    }


def send_email(parameters):
    mailbox = path_segment(parameters.get("mailbox", DEFAULT_MAILBOX))
    graph_post(
        f"/users/{mailbox}/sendMail",
        {"message": _message(parameters["recipient"], parameters["subject"], parameters["body"]), "saveToSentItems": True},
    )
    return {"success": True, "message": f"Email sent to {parameters['recipient']}."}
