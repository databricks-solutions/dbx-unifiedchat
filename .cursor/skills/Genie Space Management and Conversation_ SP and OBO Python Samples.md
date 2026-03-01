### Goal

Minimal, working Python examples to 1\) create a Genie space (“room”), 2\) read it back, 3\) update it, and 4\) start a conversation — using both Service Principal (M2M OAuth) and On‑Behalf‑Of (OBO) user authorization in a Databricks App. The samples use the Databricks SDK’s unified authentication plus direct REST calls for Genie endpoints.

---

### Prerequisites

- A Databricks workspace URL and a SQL warehouse you can use with Genie spaces. You’ll need the warehouse ID when preparing a space; ensure the identity you use has at least Can Use on that warehouse and appropriate Unity Catalog permissions on underlying tables.  
- Permissions:  
  - For Service Principal (SP) flow: the app’s SP (or your SP) must have Genie space **Can Run**, SQL warehouse **Can Use**, and downstream **Unity Catalog** privileges on referenced tables/functions, etc.  
  - For OBO: add required scopes to your Databricks App (for example, Genie \+ sql), and optionally pre‑consent as admin to avoid user prompts. Your code will forward the user token from the request headers via `X‑Forwarded‑Access‑Token`.  
- Python 3.9+ and `databricks-sdk` installed:

```shell
pip install --upgrade databricks-sdk
```

---

### Constants and helpers

```py
import json
import time
from databricks.sdk import WorkspaceClient

# -------- Adjust for your workspace --------
WORKSPACE_HOST = "https://<your-workspace-host>"  # e.g., https://dbc-12345.cloud.databricks.com
PARENT_PATH = "/Workspace/Users/yang.yang@databricks.com"  # where the space object will live
# Optional: if your serialized space references tables in UC, ensure the identity has the right ACLs.

# Minimal, well-structured serialized_space (unescaped JSON).
# Add your tables, instructions, examples, and benchmarks as needed.
SER_SPACE_OBJ = {
    "version": 1,
    "config": {
        "sample_questions": [
            {"id": "q1", "question": ["What is the row count of my_table?"]}
        ]
    },
    "data_sources": {
        "tables": [
            {"identifier": "main.default.my_table"}
        ]
    },
    # Add instructions, example SQLs, benchmarks, joins, etc. as needed for a quality space
}

def escape_serialized_space(obj: dict) -> str:
    """Genie space APIs expect serialized_space as a JSON-escaped string."""
    return json.dumps(obj, separators=(",", ":"))  # compact form

def create_space(wc: WorkspaceClient, description: str) -> dict:
    payload = {
        "description": description,
        "parent_path": PARENT_PATH,
        "serialized_space": escape_serialized_space(SER_SPACE_OBJ),
    }
    # POST /api/2.0/genie/spaces
    return wc.api_client.do("POST", "/api/2.0/genie/spaces", body=payload)  # returns {"space_id": "...", ...}

def get_space(wc: WorkspaceClient, space_id: str, include_serialized_space: bool = True) -> dict:
    # GET /api/2.0/genie/spaces/{space_id}?include_serialized_space=true
    path = f"/api/2.0/genie/spaces/{space_id}"
    query = {"include_serialized_space": "true"} if include_serialized_space else {}
    return wc.api_client.do("GET", path, query=query)

def update_space(wc: WorkspaceClient, space_id: str, new_ser_space_obj: dict, new_description: str | None = None) -> dict:
    # Update Genie Space API: send new serialized_space (and any updatable fields).
    # Depending on your cloud/preview version, this may be PATCH or PUT; PATCH shown here.
    body = {"serialized_space": escape_serialized_space(new_ser_space_obj)}
    if new_description is not None:
        body["description"] = new_description
    return wc.api_client.do("PATCH", f"/api/2.0/genie/spaces/{space_id}", body=body)

def start_conversation(wc: WorkspaceClient, space_id: str, question: str) -> dict:
    # POST /api/2.0/genie/spaces/{space_id}/start-conversation
    return wc.api_client.do(
        "POST",
        f"/api/2.0/genie/spaces/{space_id}/start-conversation",
        body={"content": question},
    )

def get_message(wc: WorkspaceClient, space_id: str, conversation_id: str, message_id: str) -> dict:
    # GET /api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}
    return wc.api_client.do(
        "GET",
        f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}",
    )

def poll_until_done(wc: WorkspaceClient, space_id: str, conversation_id: str, message_id: str, timeout_s: int = 600, poll_s: float = 2.0) -> dict:
    # Poll status until COMPLETED/FAILED/CANCELLED, respecting best practices to limit polling.
    start = time.time()
    terminal = {"COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}
    while time.time() - start < timeout_s:
        msg = get_message(wc, space_id, conversation_id, message_id)
        status = msg.get("status") or msg.get("message", {}).get("status")
        if status in terminal:
            return msg
        time.sleep(poll_s)
    raise TimeoutError("Timed out waiting for Genie message completion")
```

---

### Flow A: Service Principal (M2M OAuth, “app authorization”)

Use the app’s dedicated Service Principal (or another SP) with client credentials. The Databricks SDK’s unified authentication will mint tokens automatically when `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, and `DATABRICKS_CLIENT_SECRET` are present. Grant the SP appropriate permissions on the space, warehouse, and UC objects.

```py
# -------- Environment ----------
# export DATABRICKS_HOST="https://<your-workspace-host>"
# export DATABRICKS_CLIENT_ID="<sp-client-id>"
# export DATABRICKS_CLIENT_SECRET="<sp-client-secret>"

# -------- Initialize client --------
wc = WorkspaceClient(host=WORKSPACE_HOST)  # unified auth picks up SP OAuth env vars

# 1) Create a new space
created = create_space(wc, description="Sales analytics demo space (SP auth)")
space_id = created["space_id"]
print("Created space:", space_id)

# 2) Read it back (including serialized_space)
current = get_space(wc, space_id, include_serialized_space=True)
print("Current space title/desc:", current.get("title"), current.get("description"))

# 3) Update the space (e.g., add another sample question)
new_ser = SER_SPACE_OBJ.copy()
new_ser = json.loads(json.dumps(new_ser))  # deep copy
new_ser["config"]["sample_questions"].append({"id": "q2", "question": ["Show top 10 values in my_table by count"]})
updated = update_space(wc, space_id, new_ser_space_obj=new_ser, new_description="Updated demo space with extra sample question")
print("Updated space:", updated.get("space_id"))

# 4) Start a conversation and poll for completion
start = start_conversation(wc, space_id, question="What is the row count of my_table?")
conv = start.get("conversation") or start
msg = start.get("message") or start
conversation_id = conv["id"] if isinstance(conv, dict) else conv.get("conversation_id")
message_id = msg["id"] if isinstance(msg, dict) else msg.get("message_id")

final_msg = poll_until_done(wc, space_id, conversation_id, message_id)
print("Final status:", final_msg.get("status") or final_msg.get("message", {}).get("status"))
# If SQL/result attachments are present, retrieve them via the attachments/query-result endpoints as needed.
```

APIs used: `POST /api/2.0/genie/spaces`, `GET /api/2.0/genie/spaces/{space_id}?include_serialized_space=true`, `POST /api/2.0/genie/spaces/{space_id}/start-conversation`, and message polling endpoints.

---

### Flow B: On‑Behalf‑Of (OBO, “user authorization”) in a Databricks App

Use the user’s identity by forwarding the `X‑Forwarded‑Access‑Token` header provided by Databricks Apps. Ensure your app has the necessary OAuth scopes (e.g., Genie \+ `sql`) and ideally pre‑consent them to avoid first‑use prompts. Calls will execute with the user’s permissions and must be authorized for the space, warehouse, and underlying UC objects the space touches.

```py
from streamlit.web.server.websocket_headers import _get_websocket_headers
from databricks.sdk import WorkspaceClient

# In your Databricks App request handler (e.g., Streamlit):
headers = _get_websocket_headers()
obo_token = headers.get("X-Forwarded-Access-Token")  # forwarded user token header name for OBO

# Initialize SDK as the user (token behaves like a PAT for Databricks APIs)
wc_user = WorkspaceClient(token=obo_token, auth_type="pat", host=WORKSPACE_HOST)

# 1) (Optional) Create or update a space as the user (user must have rights).
# If you only want users to converse (not manage), skip creation/update and just start conversations.

created_u = create_space(wc_user, description="User-owned space via OBO")
space_id_u = created_u["space_id"]
print("Created space (OBO):", space_id_u)

# 2) Start a conversation in that space as the user
start_u = start_conversation(wc_user, space_id_u, question="Give me top 5 values in my_table")
conv_u = start_u.get("conversation") or start_u
msg_u = start_u.get("message") or start_u
conversation_id_u = conv_u["id"] if isinstance(conv_u, dict) else conv_u.get("conversation_id")
message_id_u = msg_u["id"] if isinstance(msg_u, dict) else msg_u.get("message_id")

final_msg_u = poll_until_done(wc_user, space_id_u, conversation_id_u, message_id_u)
print("User-run final status:", final_msg_u.get("status") or final_msg_u.get("message", {}).get("status"))
```

Key requirements:

- Add OBO scopes to the app (for example, Genie \+ `sql`), so the user token is down‑scoped appropriately and your app can call the Genie and SQL APIs on the user’s behalf.  
- Read the forwarded token from `X‑Forwarded‑Access‑Token` and initialize the SDK with `token=...` and `auth_type="pat"` for downstream Databricks API calls as the user.

---

### Notes and best practices

- The Genie “room” is the public API concept of a **Genie space**; management is separate from conversations. Use the Management APIs to create/export/update spaces and the Conversation APIs to chat and fetch results.  
- Poll message status every 1–5 seconds and time out long‑running polls (\~10 minutes) to avoid unbounded waits.  
- Ensure the identity (SP or user) has both the space‑level permission and all downstream resource permissions (warehouse, UC tables/functions, vector indexes, etc.).  
- The Databricks SDK uses unified auth and can auto‑mint OAuth tokens for SPs (M2M) when `DATABRICKS_CLIENT_ID/SECRET` are set; you can also explicitly pass credentials. For Azure, you may use Azure‑specific fields if preferred.

*Written with Glean Assistant*  
