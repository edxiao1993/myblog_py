# MyBlog API Documentation (for iOS App)

Complete API specification for integrating native clients (iOS / mobile apps) with the MyBlog backend server.

---

## 1. Overview & Architecture

### Base URL
- **Local Development:** `http://127.0.0.1:5000` (or `https://127.0.0.1:8443` with self-signed SSL)
- **Production Server:** `https://YOUR_SERVER_IP:8443` (or your production domain name)

### Authentication Mechanism
Authentication uses **dual-token JWT (JSON Web Tokens)**:
- **`access_token`**: Short-lived JWT (1 hour). Sent with every protected request in the HTTP header:
  ```http
  Authorization: Bearer <access_token>
  ```
- **`refresh_token`**: Long-lived opaque string (30 days). Stored in the database and secured in the iOS Keychain. Used to silently obtain a new `access_token` when the old one expires without prompting the user for credentials.

### Standard Response Codes
| Status Code | Meaning | Description |
| :--- | :--- | :--- |
| `200 OK` | Success | The request succeeded. |
| `201 Created` | Created | A new resource (e.g. user) was created. |
| `400 Bad Request` | Client Error | Missing required fields or invalid parameters. |
| `401 Unauthorized` | Auth Error | Missing, expired, or invalid token / wrong credentials. |
| `409 Conflict` | Conflict | Username or email already registered. |
| `500 Internal Error` | Server Error | Unhandled server error. |

---

## 2. Authentication Flow Diagram (iOS)

```
[ User Action ]            [ iOS App ]                             [ Backend Server ]
       │                        │                                          │
       ├─ Launch App ───────────► Check Keychain for refresh_token         │
       │                        │                                          │
       │                        ├─ Found? POST /api/token/refresh ─────────► Returns new access_token
       │                        │                                          │
       │                        └─ Not found? Show Login / Signup Screen   │
       │                                                                   │
       ├─ Submit Register/Login ───────────────────────────────────────────► POST /api/register or /api/login
       │                                                                   │ Returns {access_token, refresh_token}
       │                        ◄──────────────────────────────────────────┤
       │                        Save tokens to Keychain                    │
       │                                                                   │
       ├─ Browse Moments ───────► GET /api/moments ────────────────────────► Returns JSON data
       │                        (Header: Authorization: Bearer <token>)    │
```

---

## 3. Authentication Endpoints

### 3.1 Register Account
Creates a new user account and immediately returns access & refresh tokens.

- **URL:** `/api/register`
- **Method:** `POST`
- **Auth Required:** No
- **Headers:** `Content-Type: application/json`

#### Request Body
```json
{
  "username": "ios_demo",
  "email": "demo@example.com",
  "password": "StrongPassword123"
}
```

#### Success Response (`201 Created`)
```json
{
  "access_token": "<ACCESS_TOKEN>",
  "refresh_token": "<REFRESH_TOKEN>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {
    "id": 2,
    "username": "ios_demo",
    "email": "demo@example.com"
  }
}
```

#### Error Responses
- `400 Bad Request`: `{"error": "username, email, and password are required"}`
- `409 Conflict`: `{"error": "Username already exists"}` or `{"error": "Email already registered"}`

---

### 3.2 Login
Authenticates existing credentials and issues a fresh pair of tokens.

- **URL:** `/api/login`
- **Method:** `POST`
- **Auth Required:** No
- **Headers:** `Content-Type: application/json`

#### Request Body
```json
{
  "username": "ios_demo",
  "password": "StrongPassword123"
}
```

#### Success Response (`200 OK`)
```json
{
  "access_token": "<ACCESS_TOKEN>",
  "refresh_token": "<REFRESH_TOKEN>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {
    "id": 2,
    "username": "ios_demo",
    "email": "demo@example.com"
  }
}
```

#### Error Responses
- `400 Bad Request`: `{"error": "username and password are required"}`
- `401 Unauthorized`: `{"error": "Invalid username or password"}`

---

### 3.3 Refresh Access Token
Exchanges a valid 30-day refresh token for a fresh 1-hour access token when receiving an HTTP 401.

- **URL:** `/api/token/refresh`
- **Method:** `POST`
- **Auth Required:** No (Uses `refresh_token` in body)
- **Headers:** `Content-Type: application/json`

#### Request Body
```json
{
  "refresh_token": "<REFRESH_TOKEN>"
}
```

#### Success Response (`200 OK`)
```json
{
  "access_token": "<ACCESS_TOKEN>",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

#### Error Responses
- `400 Bad Request`: `{"error": "refresh_token is required"}`
- `401 Unauthorized`: `{"error": "Invalid or expired refresh token"}` *(Action on iOS: Redirect to Login)*

---

### 3.4 Logout
Revokes the refresh token in the backend database.

- **URL:** `/api/logout`
- **Method:** `POST`
- **Auth Required:** No (Uses `refresh_token` in body)
- **Headers:** `Content-Type: application/json`

#### Request Body
```json
{
  "refresh_token": "<REFRESH_TOKEN>"
}
```

#### Success Response (`200 OK`)
```json
{
  "message": "Logged out"
}
```

---

## 4. Protected Data Endpoints

### 4.1 Get Secret Moments List
Retrieves paginated secret moments ordered by creation date descending.

- **URL:** `/api/moments` or `/api/mm/<page>`
- **Method:** `GET`
- **Auth Required:** Yes (`Authorization: Bearer <access_token>`)
- **Query Parameters:**
  - `page` (optional, default `1`): Current page index
  - `per_page` (optional, default `10`, max `50`): Number of moments per page

#### Example Request
```http
GET /api/moments?page=1&per_page=10 HTTP/1.1
Host: YOUR_SERVER_IP:8443
Authorization: Bearer <ACCESS_TOKEN>
```

#### Success Response (`200 OK`)
```json
{
  "moments": [
    {
      "id": "c1f7a627d31248a39a896f6e729424c5",
      "moment": "Had an awesome day developing the iOS app!",
      "create_time": "2026-09-23 18:30:12"
    }
  ],
  "page": 1,
  "per_page": 10,
  "pages": 1,
  "total": 1,
  "has_next": false,
  "has_prev": false
}
```

#### Error Responses
- `401 Unauthorized`: `{"error": "Invalid or expired token"}` or `{"error": "Missing Authorization header"}`

---

### 4.2 Create a Secret Moment
Publishes a new secret moment to the database.

- **URL:** `/api/moments`
- **Method:** `POST`
- **Auth Required:** Yes (`Authorization: Bearer <access_token>`)
- **Headers:** `Content-Type: application/json`

#### Request Body
```json
{
  "moment": "Had an awesome day developing the iOS app today!"
}
```

#### Success Response (`201 Created`)
```json
{
  "message": "Secret moment created successfully",
  "moment": {
    "id": "c1f7a627d31248a39a896f6e729424c5",
    "moment": "Had an awesome day developing the iOS app today!",
    "create_time": "2026-09-23 19:15:00"
  }
}
```

#### Error Responses
- `400 Bad Request`: `{"error": "moment content is required"}`
- `401 Unauthorized`: `{"error": "Invalid or expired token"}`

---

### 4.3 Delete a Secret Moment
Deletes an existing moment by its unique ID.

- **URL:** `/api/moments/<moment_id>`
- **Method:** `DELETE`
- **Auth Required:** Yes (`Authorization: Bearer <access_token>`)

#### Success Response (`200 OK`)
```json
{
  "message": "Secret moment deleted successfully"
}
```

#### Error Responses
- `401 Unauthorized`: `{"error": "Invalid or expired token"}`
- `404 Not Found`: `{"error": "Secret moment not found"}`

---

### 4.4 Protected Profile Example
Example endpoint to verify token validity and read user profile data.

- **URL:** `/api/protected-example`
- **Method:** `GET`
- **Auth Required:** Yes (`Authorization: Bearer <access_token>`)

#### Success Response (`200 OK`)
```json
{
  "message": "This is protected data",
  "user": "ios_demo",
  "email": "demo@example.com"
}
```

---

## 5. AI Conversation Sessions (DuckDB)

These endpoints save, list, retrieve, search, and delete AI conversation sessions using a **create-once, append-incrementally** pattern.
- **Current development behavior:** Uses in-memory DuckDB (`:memory:`) when debug is enabled and the server is not configured as production. These records disappear when the server process exits.
- **Current production behavior:** Uses a persistent file whenever production is enabled or debug is disabled. `DUCKDB_PATH` overrides the default `data/ai_sessions.duckdb` path.
- This section distinguishes the current app/server implementation from proposed reliability improvements below.
- Message batches, session deletion, and Pi archive snapshots are transactional: failures roll back the entire operation.
- A supplied `message_id` supports retries: identical role, content, type, token count, and supplied timestamp return the existing acknowledgment; conflicting reuse returns HTTP 409. Mixed batches acknowledge existing messages and insert new ones. Without IDs, retries can still duplicate messages.
- Session creation with the same `session_id`, owner, and metadata returns the existing session. Conflicting reuse returns HTTP 409. `client_session_id` and `client_message_id` remain unimplemented.
- Invalid payloads, roles, timestamps, and negative or non-integer pagination parameters return HTTP 400. Timestamps with offsets are stored in UTC.
- All application database access is serialized across same-host processes using `<DUCKDB_PATH>.lock`; connections close before the lock is released. External database clients must follow the same protocol. Use local storage supporting reliable file locks; never delete an active lock file.


### Architecture: Incremental Save Flow

```
Turn 0 — Chat starts
  iOS App → POST /api/ai/sessions               ← Create session (metadata only)
          ← { session_id: "abc-123" }

  iOS App → POST /api/ai/sessions/abc-123/messages  ← Send initial messages
          Body: {messages: [system_prompt, COMPACTED_MEMORY (if available)]}

Turn 1 — AI finishes its response
  iOS App → POST /api/ai/sessions/abc-123/messages
          Body: {messages: [{role: "user", ...}, {role: "assistant", ...}]}

Turn N — Each subsequent turn appends only the new messages
```


### Current iOS Session Lifecycle and Upload Policy

The AI provider produces the conversation; MyBlog archives completed turns separately. The app does not wait for a remote upload to finish before allowing the user to chat.

1. Opening a new normal chat creates a local session UUID and stages an upload record, even when the session has no completed turns yet.
2. While signed in, the upload worker creates the remote session, saves the returned `session_id`, and appends the initial context once.
3. After each streamed reply completes, the worker appends only the unacknowledged user/assistant pair. Partial streaming fragments and failed or unanswered requests are not archived as completed turns.
4. Confirming an app action appends a separate system message such as `APP_ACTION_STATE for turn <UUID>: Task added.` This does not overwrite the original assistant message.
5. Reopening normal chat, entering the background, or returning to the foreground triggers synchronization. Launch and successful login/register also retry pending work.
6. Closing the normal chat page or backgrounding the app does not end the local session. On a later app launch, the previous nonempty open session is closed locally; opening chat creates a new session. There is currently no remote close-session endpoint or uploaded `closed_at` field.
7. Temporary conversations are excluded from automatic archival.

The initial archive includes the generated normal-chat system prompt and persistent compacted memory when available. It excludes old raw conversations, saved tasks, Thoughts, and Today context. The server still recognizes `TODAY_CONTEXT` for other clients, but normal iOS chat does not currently send it.

The app captures initial context and model/provider metadata once for the archive. It does not record every later prompt change, current-session compaction summary, native tool schema, intermediate tool call, or raw web-search result. Therefore this is a conversation archive, not an exact replay of every AI-provider request.

### Current Local Upload Ledger

The iOS app stores one JSON file per local session under `ChatSessionSync` in its application storage directory:

| Field | Purpose |
| :--- | :--- |
| `transcript` | Local session UUID, timestamps, completed turns, initial context, initial model/provider |
| `ownerUserID` | Account permitted to upload this record |
| `remoteSessionID` | Server session ID returned by creation |
| `initialContextUploaded` | Initial-message acknowledgment |
| `uploadedTurnIDs` | Completed turns already acknowledged by the server |
| `uploadedActionResults` | Action-result events already acknowledged |
| `lastError` | Last synchronization failure for local inspection |

The worker processes creation, initial context, completed turns, then action-result events in order. Acknowledgments are saved only after a successful HTTP response. Files remain after success to preserve the local-to-remote mapping and acknowledgment ledger across launches.

Only the matching signed-in account can upload an owned record. A record staged while signed out remains unassigned until it is staged again while signed in; login alone does not claim every unassigned record. Failed uploads remain queued, and local chatting continues.

There is currently no periodic retry timer, network-restoration listener, upload-status UI, or exactly-once delivery guarantee. Local SwiftData history remains available independently of the upload ledger.

### Current DuckDB Tables

The actual schema lives in `services/duckdb_service.py`:

| Table | Columns |
| :--- | :--- |
| `ai_sessions` | `session_id VARCHAR PRIMARY KEY`, `user_id INTEGER NOT NULL`, `title`, `model_name`, `stream BOOLEAN`, `response_format JSON`, `config JSON`, `created_at TIMESTAMP`, `updated_at TIMESTAMP` |
| `ai_messages` | `message_id VARCHAR PRIMARY KEY`, `session_id VARCHAR NOT NULL`, `seq INTEGER NOT NULL`, `role`, `message_type`, `content TEXT`, `response_text TEXT`, `web_search JSON`, `sources JSON`, `suggestion JSON`, `action JSON`, `token_count INTEGER`, `created_at TIMESTAMP` |

Indexes exist on `ai_sessions.user_id` and `ai_messages.session_id`. Each appended message receives the next per-session `seq`, starting at zero. Session retrieval orders messages by `seq ASC`.

The current schema has no foreign-key constraint linking the tables, no unique `(session_id, seq)` constraint, and no unique client-session/client-message identity constraints. Ownership is checked in the service layer. Message batches and session deletion are protected by a process-local lock, but do not currently have an explicit transaction spanning all statements.

Server timestamps are generated in UTC and stored in `TIMESTAMP` columns without timezone information. The current iOS append payload sends only `role` and `content`, so server message timestamps represent upload time rather than the original local message time.

### Message Type Auto-Classification

Messages are automatically classified on the server — the iOS app just sends raw `{role, content}`:

| `role` | Auto-detected `message_type` | Trigger |
| :--- | :--- | :--- |
| `system` | `system_prompt` | Default for system messages |
| `system` | `compacted_memory` | First 50 content characters contain "COMPACTED_MEMORY" |
| `system` | `search_results` | First 50 content characters contain "WEB_SEARCH_RESULTS" |
| `user` | `user_message` | Default for user messages |
| `user` | `context_injection` | First 50 content characters contain "TODAY_CONTEXT" |
| `assistant` | `assistant_response` | All assistant messages |

For `assistant` messages, the server extracts `response_text`, `web_search`, `suggestion`, `action`, and `sources` from the JSON content string, while retaining the complete string in `content`. Plain-text assistant content is also supported as `response_text`. Empty source/action fields are stored as SQL NULL.

`APP_ACTION_STATE` messages currently use the default `system_prompt` classification. `action_result` can remain inside assistant JSON content, but has no dedicated extracted database column.

### 5.1 Create AI Session
- **URL:** `/api/ai/sessions`
- **Method:** `POST`
- **Auth Required:** Yes (`Authorization: Bearer <access_token>`)
- **Headers:** `Content-Type: application/json`

#### Request Body
```json
{
  "title": "BlueNest Conversation",
  "model_name": "deepseek-v4-flash",
  "stream": true,
  "config": {
    "client_session_id": "<local-session-UUID>",
    "provider": "deepseek",
    "source": "BlueNest-iOS"
  }
}
```


The example matches the current iOS request. `model_name` and `provider` reflect the selected AI provider at session creation. The current app uses the generic title `BlueNest Conversation`.

`response_format` is optional metadata. Normal chat receives Markdown with native function tools, so the app omits this field; the server stores `{}` when omitted. The assistant archive JSON envelope is constructed by the app after receiving the answer and does not imply an AI-provider `json_object` response mode.

The server also accepts an optional top-level `session_id`, but the current app does not supply it. Otherwise, a new UUID is generated for every create request. `config.client_session_id` is currently stored as metadata only and does **not** deduplicate retries.

#### Success Response (`201 Created`)
```json
{
  "message": "AI session created",
  "session": {
    "session_id": "7f12f940...",
    "user_id": 1,
    "title": "BlueNest Conversation",
    "model_name": "deepseek-v4-flash",
    "stream": true,
    "response_format": {},
    "config": {
      "client_session_id": "<local-session-UUID>",
      "provider": "deepseek",
      "source": "BlueNest-iOS"
    },
    "created_at": "2026-10-01 08:40:00",
    "updated_at": "2026-10-01 08:40:00"
  }
}
```

### 5.2 Append Messages to Session
- **URL:** `/api/ai/sessions/<session_id>/messages`
- **Method:** `POST`
- **Auth Required:** Yes (`Authorization: Bearer <access_token>`)
- **Headers:** `Content-Type: application/json`

#### Request Body
```json
{
  "messages": [
    {"role": "user", "content": "Tell me about MCP"},
    {"role": "assistant", "content": "{\"response_text\":\"MCP is the Model Context Protocol...\",\"web_search\":null,\"sources\":[],\"suggestion\":null,\"action\":null}"}
  ]
}
```


The app waits for the complete AI response before sending this pair. It serializes the Markdown reply into the assistant `content` JSON string locally. The envelope also supports `action_result`.

Current iOS uploads contain final text, source titles/URLs, and any pending task/Thought proposal. `web_search` and `suggestion` are currently always `null`; full native tool calls, raw search results, token counts, and hidden reasoning are not uploaded.

The server accepts optional `message_id`, `message_type`, `token_count`, and `created_at` on individual messages. The current app sends only `role` and `content`. Supplying an existing `message_id` currently causes a primary-key conflict rather than a successful duplicate acknowledgment.

#### Success Response (`201 Created`)
```json
{
  "message": "2 message(s) appended",
  "messages": [
    {
      "message_id": "a1b2c3...",
      "seq": 3,
      "role": "user",
      "message_type": "user_message",
      "content": "Tell me about MCP",
      "response_text": null,
      "created_at": "2026-10-01 08:41:00"
    },
    {
      "message_id": "d4e5f6...",
      "seq": 4,
      "role": "assistant",
      "message_type": "assistant_response",
      "content": "{...}",
      "response_text": "MCP is the Model Context Protocol...",
      "created_at": "2026-10-01 08:41:00"
    }
  ]
}
```

### 5.3 List AI Sessions
- **URL:** `/api/ai/sessions?limit=50&offset=0`
- **Method:** `GET`
- **Auth Required:** Yes (`Authorization: Bearer <access_token>`)

#### Success Response (`200 OK`)
```json
{
  "count": 1,
  "sessions": [
    {
      "session_id": "7f12f940...",
      "title": "Chat about MCP",
      "model_name": "deepseek-v4-flash",
      "created_at": "2026-10-01 08:40:00",
      "updated_at": "2026-10-01 08:41:00",
      "message_count": 5
    }
  ]
}
```

### 5.4 Get Full Session Details
- **URL:** `/api/ai/sessions/<session_id>`
- **Method:** `GET`
- **Auth Required:** Yes (`Authorization: Bearer <access_token>`)

### 5.5 Search Across Sessions
- **URL:** `/api/ai/sessions/search?q=MCP&type=user_message,assistant_response&limit=20`
- **Method:** `GET`
- **Auth Required:** Yes (`Authorization: Bearer <access_token>`)
- **Query Params:**
  - `q` (required): Search query
  - `type` (optional): Comma-separated message types to filter
  - `limit` (optional, default 20): Max results

#### Success Response (`200 OK`)
```json
{
  "query": "MCP",
  "count": 2,
  "results": [
    {
      "message_id": "a1b2c3...",
      "session_id": "7f12f940...",
      "session_title": "Chat about MCP",
      "model_name": "deepseek-v4-flash",
      "seq": 3,
      "role": "user",
      "message_type": "user_message",
      "content": "Tell me about MCP",
      "response_text": null,
      "created_at": "2026-10-01 08:41:00"
    }
  ]
}
```

### 5.6 Delete AI Session
- **URL:** `/api/ai/sessions/<session_id>`
- **Method:** `DELETE`
- **Auth Required:** Yes (`Authorization: Bearer <access_token>`)


### 5.7 Session Reliability Improvement Plan (Not Yet Implemented)

The following changes are proposed contracts and migrations, not guarantees of the current endpoints.

#### A. Idempotent Session Creation

- Promote a stable `client_session_id` to a validated create-request field and a stored column.
- Enforce uniqueness on `(user_id, client_session_id)`.
- Return the existing session for a repeated identical create request instead of creating another one. Reject inconsistent reuse with `409 Conflict`.
- Preserve the current server `session_id` as the identifier used by append/detail/delete URLs.
- Migrate existing rows explicitly; do not assume a client ID exists for legacy sessions.

Proposed request addition:

```json
{
  "client_session_id": "<stable-local-session-UUID>",
  "title": "BlueNest Conversation",
  "model_name": "deepseek-v4-flash",
  "stream": true,
  "config": {"provider": "deepseek", "source": "BlueNest-iOS"}
}
```

#### B. Idempotent Message Appends

- Give every archived message a stable `client_message_id`, persisted before networking. Use separate IDs for each user message, assistant message, initial-context message, and action event.
- Enforce uniqueness on `(session_id, client_message_id)` and `(session_id, seq)`.
- A duplicate with identical role/content should return its existing acknowledgment. Different content under the same identity should return `409 Conflict`.
- Retries must preserve the original IDs and payload. A batch containing already-saved and new messages inserts only the new messages.
- Return IDs and sequence numbers for every acknowledged message; the iOS client validates acknowledgments before updating its local ledger.

Proposed append shape:

```json
{
  "messages": [
    {
      "client_message_id": "<stable-user-message-UUID>",
      "role": "user",
      "content": "Tell me about MCP",
      "created_at": "2026-10-10T08:41:00Z"
    },
    {
      "client_message_id": "<stable-assistant-message-UUID>",
      "role": "assistant",
      "content": "{\"response_text\":\"MCP is...\",\"sources\":[],\"action\":null}"
    }
  ]
}
```

#### C. Atomic Database Operations

- Validate the whole batch before writing any message.
- Use an explicit transaction for identity checks, sequence allocation, all message inserts, and the session `updated_at` change; roll back the entire batch on failure.
- Delete messages and their session in one transaction after verifying account ownership.
- The current backend serializes transactions across same-host workers using a shared file lock and closes connections between operations. A dedicated writer service is an alternative for larger deployments.
- Return structured client errors for invalid roles/content, missing sessions, ownership failures, and identity conflicts.

#### D. Client Integration and Recovery

- Keep the existing incremental queue and account ownership checks.
- Send stable client IDs only after the backend implements their deduplication semantics. Adding ignored fields alone does not prevent duplicates.
- Preserve local data and report sync failures; reconcile rather than silently recreating a remote session that returns `404`.
- Define whether original message timestamps are stored separately from server receipt timestamps, using a consistent UTC contract.
- Keep completed-turn uploads immediate; lifecycle hooks provide extra retry opportunities rather than the only save mechanism.

Until A-C are implemented, a successful server commit followed by a lost network response can produce duplicate sessions/messages on retry, and a failed batch can leave partially inserted messages. This plan retains the current incremental endpoints; it does not switch to whole-session snapshot replacement.

---

## 6. iOS Swift Integration Guide

### Swift Networking Helper Example (URLSession)

```swift
import Foundation

class APIManager {
    static let shared = APIManager()
    let baseURL = "https://YOUR_SERVER_IP:8443"

    // 1. Send Login Request
    func login(username: String, password: String) async throws -> (accessToken: String, refreshToken: String) {
        let url = URL(string: "\(baseURL)/api/login")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = ["username": username, "password": password]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            throw NSError(domain: "AuthError", code: 401)
        }

        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        let access = json["access_token"] as! String
        let refresh = json["refresh_token"] as! String
        return (access, refresh)
    }

    // 2. Fetch Protected Moments
    func fetchMoments(accessToken: String, page: Int = 1) async throws -> [String: Any] {
        let url = URL(string: "\(baseURL)/api/moments?page=\(page)&per_page=10")!
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            throw NSError(domain: "APIError", code: (response as? HTTPURLResponse)?.statusCode ?? 500)
        }

        return try JSONSerialization.jsonObject(with: data) as! [String: Any]
    }
}
```

### Self-Signed Certificate Trust in iOS Development
If connecting to your self-signed certificate server (`https://YOUR_SERVER_IP:8443`):
1. Install and enable full root trust for `cert.pem` on your device or simulator (**Settings > General > About > Certificate Trust Settings**).
2. Alternatively, implement `URLSessionDelegate` with `urlSession(_:didReceive:completionHandler:)` for development bypass.
