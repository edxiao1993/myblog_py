# Coding Agent API

The app keeps `/api/agent/*` and bearer-token login. `GET /api/agent/config`
identifies the new backend with `agent.kind: "coding_agent"`. The Coding Agent web
page and API now use the same private conversations, project directory, native Pi
prompt, run lock, managed background execution, cancellation, and replay records.
The older Pi Agent page continues to use its existing service.

## Project configuration

`CODING_PROJECT_ROOT` bounds coding project paths (default: the service account's
home). Provider credentials and Pi settings still belong to that service account.
Set destinations and assignments through the server's private environment file.

Without an explicit assignment for a username, the API provisions an isolated
`<CODING_PROJECT_ROOT>/coding-projects/<encoded-username>/default` project. It also
recognizes that user's web-created projects under the allowed root. Only owned
web conversation metadata is used; the API does not enumerate arbitrary folders.

For an authoritative per-user allowlist, set `CODING_API_PROJECTS` to a JSON object
mapping login usernames to lists of approved projects, for example:

```dotenv
CODING_API_PROJECTS='{"example-user":[{"id":"default","name":"Default project","path":"/allowed/root/project-a"},{"id":"project-b","name":"Project B","path":"/allowed/root/project-b"}]}'
```

These paths are examples. Create the assigned directories on the server first;
explicitly assigned missing directories are not recreated. Their resolved paths
must stay under `CODING_PROJECT_ROOT`. An empty list denies new coding execution
for that username. A username omitted from the object uses the implicit behavior
above. Project IDs must be stable strings of 1–120 ASCII letters, digits,
underscores, or hyphens; reserve `legacy-` IDs for the server's old workspaces.
The first available assigned project supplies default settings. API requests send
IDs, never paths. A project cannot change after session creation.

`GET /api/agent/config?working_directory_id=<id>` returns actual settings for that
project. Each model's thinking levels are queried for that model. Non-reasoning
models have no selectable levels and use JSON `null`. Config permissions apply
before creation; a saved session's `editable_settings` permits model/thinking
changes only. Configuration errors or no available default return
`agent.available: false`; saved history remains readable.

## Endpoint contract

| Method | Endpoint | Result |
| --- | --- | --- |
| GET | `/api/agent/config` | Capability, approved projects, models, defaults, permissions |
| POST | `/api/agent/sessions` | 201, owned Coding Agent session; optional complete settings |
| GET | `/api/agent/sessions?limit=30&cursor=...` | Flat owned history and next cursor |
| GET | `/api/agent/sessions/{id}` | Session and active run |
| GET | `/api/agent/sessions/{id}/messages?limit=50&before=...` | User/assistant messages and older-page cursor |
| PATCH | `/api/agent/sessions/{id}/settings` | Complete settings; same project ID required |
| POST | `/api/agent/sessions/{id}/runs` | 202, durable user message and managed run |
| GET | `/api/agent/sessions/{id}/runs/{run_id}/events` | Normalized replayable SSE; `Last-Event-ID` supported |
| POST | `/api/agent/sessions/{id}/runs/{run_id}/cancel` | Current run status; observe until terminal |

Use the app's existing HTTPS base URL, login, and token refresh. Never send provider
credentials, raw filesystem paths, or system prompts. Requests should include a
stable `client_request_id` for creation/submission retries and may include
`X-Request-ID`. Creation idempotency records retain the old storage namespace so
retries accepted before migration still resolve to the original resource.

SSE contains only `status`, `message_delta`, and exactly one `run_finished`.
Thinking/tools map to progress stages; private reasoning, tool records, prompts,
and native session paths are not exposed. The final message replaces partial
text. Accepted user messages are persisted before returning 202. Reads during a
run include the stable streaming assistant ID and partial text. Cancelled/failed
runs retain partial content. On restart, abandoned accepted runs are failed
explicitly when queried/tailed; side effects are never automatically replayed.
Events are available during the run and for at least 24 hours afterward.
`PI_TIMEOUT` defaults to 600 seconds; the mobile message limit is 20,000 characters.

Web login/CSRF routes retain raw Pi streaming for the browser. Closing either
client's stream does not cancel execution. The web page can reopen an active run
from the app without submitting a second message. Web append/steering remains
available; it is not a new mobile API operation. The browser retains its existing
32,000-character message limit.

## Compatibility

Legacy Pi sessions stay in their original storage, workspace, and saved prompt.
The server resolves an owned ID against Coding Agent first and then the legacy
store. No files are moved or deleted. Legacy contexts can be continued, and their
working directory has a server-generated read-only `legacy-...` ID. Such IDs
cannot be used to create new coding sessions. The archive API `/api/ai/sessions`
is unchanged. Terminal/web messages without API run tracking have `run_id: null`,
stable synthesized message IDs, and stable saved timestamps.

## Verification

The regression suite uses a fake Pi executable; it never calls a model provider:

```sh
source /Users/kevinzeng/mydoc/pythonProject/venv/bin/activate
python -m unittest tests.test_agent_api tests.test_pi_agent tests.test_coding_agent tests.test_i18n -q
node --test tests/coding-agent-ui.test.cjs tests/pi-agent-ui.test.cjs tests/theme.test.cjs
```

Before enabling the iOS feature against a deployment, query its real config with
an authenticated account and verify `agent.kind`. Exercise a project-backed
conversation from both clients, disconnect/reconnect, and cancel from the other
client. Check real model availability under the service account. Test fixtures
prove lifecycle behavior but do not certify provider credentials or every
project-specific extension in production.
