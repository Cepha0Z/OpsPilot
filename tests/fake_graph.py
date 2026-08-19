"""Small deterministic Graph boundary used by workflow tests.

It patches only the tool-level Graph functions; production still uses
``GraphClient``.  Keeping the fixture here makes multi-step tests realistic
without any network access or tenant state.
"""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from urllib.parse import unquote
from unittest.mock import patch

from opspilot.integrations.graph.client import GraphError


class FakeGraph:
    def __init__(self):
        self.users = [
            {
                "id": "ada", "displayName": "Ada Lovelace",
                "userPrincipalName": "ada@nebulous.example", "mail": "ada@nebulous.example",
                "department": "Engineering", "jobTitle": "Engineer", "accountEnabled": True,
            },
            {
                "id": "grace", "displayName": "Grace Hopper",
                "userPrincipalName": "grace@nebulous.example", "mail": "grace@nebulous.example",
                "department": "Technology", "jobTitle": "Director", "accountEnabled": True,
            },
            {
                "id": "linus", "displayName": "Linus Torvalds",
                "userPrincipalName": "linus@nebulous.example", "mail": "linus@nebulous.example",
                "department": "IT", "jobTitle": "Systems Architect", "accountEnabled": False,
            },
            {
                "id": "katherine", "displayName": "Katherine Johnson",
                "userPrincipalName": "katherine@nebulous.example", "mail": "katherine@nebulous.example",
                "department": "IT", "jobTitle": "Systems Analyst", "accountEnabled": True,
            },
        ]
        self.licenses = {
            "ada": [{"skuPartNumber": "FLOW_FREE", "skuId": "flow"}],
            "grace": [],
            "linus": [{"skuPartNumber": "POWER_AUTOMATE_FREE", "skuId": "power"}],
            "katherine": [],
        }
        self.messages = [{
            "id": "message-1", "subject": "Project update", "isRead": False,
            "receivedDateTime": "2026-08-01T10:00:00Z",
            "from": {"emailAddress": {"address": "manager@example.com"}},
        }]
        self.drafts = []
        self.sent = []
        self.calls = []
        self.fail_paths = set()

    def _fail_if_requested(self, path):
        if path in self.fail_paths:
            raise GraphError("http_error", "The requested fake Graph operation failed.")

    def find_users(self, name):
        needle = str(name).lower()
        return [deepcopy(user) for user in self.users if user["displayName"].lower().startswith(needle)]

    def get_collection(self, path, **kwargs):
        self._fail_if_requested(path)
        self.calls.append(("GET_COLLECTION", path, kwargs))
        if path == "/users":
            filter_value = (kwargs.get("params") or {}).get("$filter", "")
            if filter_value.startswith("startswith(displayName,"):
                name = filter_value.removeprefix("startswith(displayName,").removesuffix(")")
                if name.startswith("'") and name.endswith("'"):
                    name = name[1:-1].replace("''", "'")
                return self.find_users(name)
            if filter_value.startswith("department eq "):
                department = filter_value.removeprefix("department eq ")
                if department.startswith("'") and department.endswith("'"):
                    department = department[1:-1].replace("''", "'")
                return [deepcopy(user) for user in self.users if user["department"] == department]
            return deepcopy(self.users)
        if path.endswith("/messages"):
            return deepcopy(self.messages)
        raise AssertionError(f"Unexpected collection path: {path}")

    def get(self, path, **kwargs):
        self._fail_if_requested(path)
        self.calls.append(("GET", path, kwargs))
        if path == "/subscribedSkus":
            return {"value": [{"skuPartNumber": "FLOW_FREE", "skuId": "flow"}]}
        if path.endswith("/licenseDetails"):
            user_id = unquote(path.split("/")[2])
            return {"value": deepcopy(self.licenses.get(user_id, []))}
        if path.startswith("/users/"):
            user_id = unquote(path.split("/")[2])
            for user in self.users:
                if user["id"] == user_id:
                    return deepcopy(user)
        raise AssertionError(f"Unexpected get path: {path}")

    def post(self, path, body):
        self._fail_if_requested(path)
        self.calls.append(("POST", path, deepcopy(body)))
        if path.endswith("/messages"):
            draft = {"id": f"draft-{len(self.drafts) + 1}", **deepcopy(body)}
            self.drafts.append(draft)
            return draft
        if path.endswith("/sendMail"):
            self.sent.append(deepcopy(body))
            return {}
        if path.endswith("/revokeSignInSessions"):
            return {}
        if path.endswith("/assignLicense"):
            return {}
        if path == "/users":
            return {"id": "new-user", "displayName": body["displayName"]}
        raise AssertionError(f"Unexpected post path: {path}")

    def patch(self, path, body):
        self._fail_if_requested(path)
        self.calls.append(("PATCH", path, deepcopy(body)))
        return {}

    def delete(self, path):
        self._fail_if_requested(path)
        self.calls.append(("DELETE", path, None))
        return {}

    @contextmanager
    def patched_tools(self):
        with ExitStack() as stack:
            stack.enter_context(patch("opspilot.tools.users.graph_get_collection", side_effect=self.get_collection))
            stack.enter_context(patch("opspilot.tools.users.graph_get", side_effect=self.get))
            stack.enter_context(patch("opspilot.tools.users.graph_post", side_effect=self.post))
            stack.enter_context(patch("opspilot.tools.users.graph_patch", side_effect=self.patch))
            stack.enter_context(patch("opspilot.tools.users.graph_delete", side_effect=self.delete))
            stack.enter_context(patch("opspilot.tools.licenses.find_users", side_effect=self.find_users))
            stack.enter_context(patch("opspilot.tools.licenses.graph_get", side_effect=self.get))
            stack.enter_context(patch("opspilot.tools.licenses.graph_post", side_effect=self.post))
            stack.enter_context(patch("opspilot.tools.mail.graph_get_collection", side_effect=self.get_collection))
            stack.enter_context(patch("opspilot.tools.mail.graph_get", side_effect=self.get))
            stack.enter_context(patch("opspilot.tools.mail.graph_post", side_effect=self.post))
            yield self
