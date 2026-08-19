import os
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

DEFAULT_DOMAIN = "nebulousdesign.com"
DEFAULT_SENDER = "studio1@nebulousdesign.com"

FORCE_PASSWORD_CHANGE = True
# The preferred name is explicit; TEMPORARY_PASSWORD keeps existing local
# deployments working while they move to the namespaced setting. Never add a
# default: creating an account with an unknown credential is unsafe.
NEW_USER_TEMPORARY_PASSWORD = (
    os.getenv("OPSPILOT_NEW_USER_TEMPORARY_PASSWORD")
    or os.getenv("NEBULOUS_NEW_USER_TEMPORARY_PASSWORD")  # Backward compatibility.
    or os.getenv("TEMPORARY_PASSWORD")
)

GRAPH_CONNECT_TIMEOUT_SECONDS = float(os.getenv("GRAPH_CONNECT_TIMEOUT_SECONDS", "5"))
GRAPH_READ_TIMEOUT_SECONDS = float(os.getenv("GRAPH_READ_TIMEOUT_SECONDS", "20"))
GRAPH_MAX_GET_RETRIES = int(os.getenv("GRAPH_MAX_GET_RETRIES", "2"))
GRAPH_MAX_COLLECTION_ITEMS = int(os.getenv("GRAPH_MAX_COLLECTION_ITEMS", "1000"))

# Synchronous workflow engine limits.  These bound local resource use without
# introducing a separate worker service.
WORKFLOW_DEADLINE_SECONDS = int(os.getenv("OPSPILOT_WORKFLOW_DEADLINE_SECONDS", os.getenv("NEBULOUS_WORKFLOW_DEADLINE_SECONDS", "300")))
WORKFLOW_MAX_ACTIVE_EXECUTIONS = int(os.getenv("OPSPILOT_WORKFLOW_MAX_ACTIVE", os.getenv("NEBULOUS_WORKFLOW_MAX_ACTIVE", "4")))
WORKFLOW_MAX_PARALLEL_READS = int(os.getenv("OPSPILOT_WORKFLOW_MAX_PARALLEL_READS", os.getenv("NEBULOUS_WORKFLOW_MAX_PARALLEL_READS", "4")))
