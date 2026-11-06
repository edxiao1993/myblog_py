# myblog_py — Python Web & API Backend

A modern Flask-based web application and mobile API backend with MySQL relational database, dual-token JWT authentication, and DuckDB analytical storage for AI conversation sessions.

---

## 🌟 Key Features

### 1. Web Application (Flask-Login + Jinja2 + Bootstrap)
- **Blog Posts**: Create, view, and manage articles with timestamp tracking.
- **Secret Moments**: Private microblog entries with pagination.
- **User Authentication**: User registration (`/register`), login (`/login`), and session management with flash alerts.

### 2. Mobile API Backend (iOS / Native Clients)
- **Dual-Token JWT Auth**:
  - `access_token`: Short-lived (1 hour) for secure API requests (`Authorization: Bearer <token>`).
  - `refresh_token`: Long-lived (30 days) stored in DB for silent auto-refresh without re-login.
  - Revocation & Logout endpoint (`/api/logout`).
- **RESTful Endpoints**:
  - User registration & login (`/api/register`, `/api/login`).
  - Secret moments management (`GET /api/moments`, `POST /api/moments`, `DELETE /api/moments/<id>`).

### 3. AI Session Archive (DuckDB Integration)
- **Zero-Setup Analytics**: Stores complete AI conversations including system prompts, tool schemas, chain-of-thought reasoning, and tool calls.
- **Dual Engine**:
  - **Dev Mode**: Uses fast in-memory tables (`:memory:`) to avoid cluttering local disk.
  - **Production**: Persists into a columnar DuckDB file (`data/ai_sessions.duckdb`) for analytical querying and dataset export.
- Database operations use transactions and a shared `<DUCKDB_PATH>.lock` file. Workers on the same host serialize access and close their connection before releasing the lock. All processes accessing the file must follow this protocol; do not delete the lock file while the app is running. Keep the database on local storage with reliable file locking.
- Message retries with the same `message_id` and payload return the existing acknowledgment; conflicting reuse returns HTTP 409. Clients must preserve IDs across retries to avoid duplicates.

---

## 🛠️ Tech Stack & Prerequisites

- **Operating system**: macOS or Linux (file locking uses `fcntl`). On Windows, use WSL.
- **Python**: 3.10+ (tested on Python 3.12)
- **Web Framework**: Flask 2.3+, Werkzeug, Flask-Login, Flask-SQLAlchemy
- **Databases**:
  - **MySQL 8.0+**: Primary relational DB (Users, Posts, Moments, Refresh Tokens).
  - **DuckDB 1.2+**: Embedded analytical database (AI Sessions & Tool Calls).
- **Authentication**: PyJWT (HS256) + PBKDF2-SHA256 password hashing.

---

## 🚀 Setup & Local Development

### 1. Clone & Activate Virtual Environment

```bash
git clone https://github.com/edxiao1993/myblog_py.git
cd myblog_py

# Activate existing virtualenv or create one
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables (`.env`)

Copy `.env.example` to `.env` (`cp .env.example .env`), then edit it.
Generate separate random values for `SECRET_KEY`, `JWT_SECRET_KEY`, and
`TOKEN_SECRET_KEY`; never use the example placeholders in a running deployment.
Your friend must configure their own database and keys. Do not share your `.env`.

Example settings:

```ini
# Database Configuration (MySQL)
DATABASE_URL=mysql+pymysql://username:password@localhost:3306/myblog_py

# Secret Keys (Generate with: python3 -c "import secrets; print(secrets.token_hex(32))")
SECRET_KEY=your-secure-flask-secret-key
JWT_SECRET_KEY=your-secure-jwt-secret-key

# Debug Mode (Set to False for production; True enables in-memory DuckDB)
FLASK_DEBUG=True

# DuckDB Storage Path (Used when FLASK_DEBUG=False)
DUCKDB_PATH=data/ai_sessions.duckdb
```

### 4. Initialize Database

For MySQL or MariaDB, create the database first and grant your application user
access to it. Set `DATABASE_URL` in `.env` to that database, for example
`mysql+pymysql://username:password@DB_HOST:3306/myblog_py?charset=utf8mb4`.
URL-encode special characters in the username or password.

Run the database setup script to create all relational tables:

```bash
python init_db.py
```

To also create an initial user, set `DEFAULT_ADMIN_USERNAME` (default `admin`),
`DEFAULT_ADMIN_EMAIL`, and `DEFAULT_ADMIN_PASSWORD` in your
environment or `.env` before running the script. Without it, only tables are
created. Rerunning the script preserves existing users and passwords.

This script creates missing tables; it does not migrate existing columns. For an
older database with a 120-character password hash column, run
`ALTER TABLE user MODIFY COLUMN password_hash VARCHAR(255) NOT NULL;`
before creating users with newer Werkzeug versions.

For an existing Secret Moments table, apply
`migrations/20261027_secret_moment_ownership.sql` once before deploying the updated
application. Copy the SQL file to the server and run it against the configured
database, for example:

```sh
mysql -u YOUR_DB_USER -p YOUR_DB_NAME < migrations/20261027_secret_moment_ownership.sql
```

New databases receive the owner column through normal table creation. Both the
web page and API now restrict listing and deletion to the authenticated owner,
and assign ownership on creation. Old moments are preserved with no owner and
remain hidden until explicitly assigned; their authors cannot be recovered from
the previous schema. `create_all()` does not migrate an existing table.

### 5. Run the Local Server

```bash
python app.py
```

- **Web Browser**: Open [http://127.0.0.1:5000](http://127.0.0.1:5000)
- **Register Account**: [http://127.0.0.1:5000/register](http://127.0.0.1:5000/register)

---

## 📡 API Overview (For iOS & Mobile Clients)

For complete request/response schemas and Swift integration code, refer to [API_DOCUMENTATION.md](API_DOCUMENTATION.md).

| Method | Endpoint | Auth Required | Description |
| :--- | :--- | :---: | :--- |
| `POST` | `/api/register` | No | Register new account and receive access + refresh tokens |
| `POST` | `/api/login` | No | Authenticate user and receive access + refresh tokens |
| `POST` | `/api/token/refresh` | No | Exchange valid `refresh_token` for new `access_token` |
| `POST` | `/api/logout` | No | Revoke `refresh_token` in database |
| `GET` | `/api/moments` | **Bearer** | Retrieve paginated secret moments |
| `POST` | `/api/moments` | **Bearer** | Create a new secret moment |
| `DELETE`| `/api/moments/<id>` | **Bearer** | Delete a moment by ID |
| `POST` | `/api/ai/sessions` | **Bearer** | Save or update full AI session with system prompt & tools |
| `GET` | `/api/ai/sessions` | **Bearer** | List AI conversation sessions for current user |
| `GET` | `/api/ai/sessions/<id>` | **Bearer** | Retrieve complete AI session and messages |
| `DELETE`| `/api/ai/sessions/<id>` | **Bearer** | Delete an AI conversation session |

---

## 🚢 Production Deployment

### 1. Production Gunicorn Command
Run behind a WSGI server:

```bash
gunicorn -w 4 -b 127.0.0.1:8000 app:app
```

With SSL certificate directly:
```bash
gunicorn -w 4 -b 0.0.0.0:8443 --certfile=tempCA/cert.pem --keyfile=tempCA/key.pem app:app
```

### 2. Nginx Reverse Proxy with HTTPS

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 📁 Project Structure

```
myblog_py/
├── app.py                      # Application factory & entry point
├── database.py                 # SQLAlchemy and Flask-Migrate setup
├── init_db.py                  # Database initialization script
├── requirements.txt            # Project dependencies
├── .env                        # Environment configuration
├── API_DOCUMENTATION.md        # Comprehensive iOS API guide
├── models/                     # SQLAlchemy Models
│   ├── user.py                 # User model (PBKDF2 passwords, api_secret)
│   ├── refresh_token.py        # Long-lived JWT refresh token model
│   ├── post.py                 # Blog post model
│   └── secret_moment.py        # Secret moment model
├── routes/                     # Blueprint Handlers
│   ├── auth.py                 # Web registration & login routes
│   ├── main.py                 # Posts listing, detail, and creation routes
│   ├── moments.py              # Web secret moments routes
│   ├── api_auth.py             # Mobile JWT auth APIs (register, login, refresh)
│   ├── app_api.py              # Mobile data APIs (moments CRUD)
│   └── ai_api.py               # AI session APIs (DuckDB)
├── services/                   # Business Services
│   └── duckdb_service.py       # DuckDB connection & session archive service
├── templates/                  # Jinja2 HTML Templates
└── static/                     # CSS, JS, and vendor assets
```

## Pi Agent web page

Pi Agent is the home page at `/`; `/pi-agent` remains available for existing links.
The article listing is at `/posts` (Posts / 文章), and the About page has been
removed. Login defaults to Pi Agent. Every logged-in user can use Pi; there is no username allowlist. The page supports streaming responses, tool activity, Stop,
resumable conversations, model/thinking selectors, prompt presets, file uploads
and downloads, and explicit archive snapshots in AI Sessions. Markdown
output is sanitized. Pi executes its configured built-in and extension tools;
extension dialogs are cancelled because this client has no interactive dialog UI.

While Pi is working, send more text in the same input to append instructions using
Pi's native steering queue. The response continues in the existing stream and
appended messages are saved in conversation history. Uploads and settings remain
locked during a run. If Pi has already finished, the draft is kept for resending.

Set the `PI_*` variables shown in `.env.example`. New conversations default to
General assistant (`config/pi-general-prompt.txt`) for everyday work and chatting.
The Coding assistant preset uses `config/pi-system-prompt.txt`. Preset instructions
are appended to Pi's native system instructions.
The Office assistant preset is `config/pi-office-prompt.txt`; Pi default omits the
application preset. All presets receive the conversation's upload/output paths.
Use Settings → Create a custom prompt to save your own name and instructions.
Custom prompts are private to each login and stored under that user's Pi data
directory in `prompts/`. Select one before the first message; conversations save
a snapshot of its instructions. No database migration is needed.
Presets are saved with each conversation and locked after its first message;
model and thinking settings can change before later messages. The server queries
Pi RPC for configured models and supported thinking levels; no API credentials
are sent to the browser. Office file generation uses the tools/libraries installed
on the Pi server; selecting a preset does not install additional tools.

The history sidebar lists your conversations, supports title search, and resumes
Pi's original native session when you select a conversation and send another
message. Model, thinking level, prompt, uploads, and outputs are restored. A
`?conversation=<id>` URL reopens the selected conversation after refresh. Old
conversations remain compatible and use the Coding assistant preset by default.

Uploads and generated deliverables are grouped under
`PI_WORKSPACE/<login>/conversations/<id>/uploads/` and `outputs/`. The agent is told
to save downloadable results to outputs. Download routes require login and
conversation ownership, reject traversal/symlinks, and force attachment downloads.
Uploads use unique sanitized filenames and preserve originals. Up to 20 files
can be attached to a message; `PI_MAX_UPLOAD_BYTES` limits each multipart request
(default 200 MiB including form overhead). Set Nginx `client_max_body_size 200m;`
(or a matching larger limit if you increase the app limit). For existing server
configurations, update `PI_MAX_UPLOAD_BYTES=209715200` in `.env` and restart the
web service; an existing override continues to take precedence over the default.
The limit applies to the combined request, including multipart overhead, rather
than each file separately. Large uploads also need sufficient temporary disk
space and proxy timeouts for the connection speed. This is storage
organization and HTTP access control, not OS-level sandboxing of agent tools.

The Flask backend and Pi are intended to run together on your remote server under the same
Linux service account. The browser calls Flask's `/api/pi/` routes; Flask launches the
server's Pi executable and inherits that account's Pi configuration. Development
tests use a fake RPC subprocess and do not verify remote provider credentials.
If Flask and Pi run on different hosts, this subprocess integration requires a
separate remote transport; it does not open an RPC network port by itself.
`PI_EXECUTABLE` may be an absolute path when systemd's PATH does not include Pi.
The installed Pi version must support RPC's `agent_settled` lifecycle event
(verified locally with 0.84.3). Verify `pi --version` and `pi --help` on the server.

Run the application as a dedicated unprivileged service account. Authenticate Pi
under that account, or set `PI_CODING_AGENT_DIR` to its existing configuration.
Credentials stay server-side. Tools have the service account's OS permissions:
`PI_WORKSPACE` is a working directory, not a security sandbox. Use OS/container
isolation if stronger filesystem/network limits are needed.
Use a strong Flask SECRET_KEY in production.

Create the workspace and data directories writable by the service account. Native
Pi sessions and web transcripts live under `PI_DATA_DIR`, partitioned by user and
conversation: `PI_DATA_DIR/users/<login>/<conversation-id>/`. Usernames containing
path separators are percent-encoded. Older numeric user-ID session directories
are migrated automatically when that user accesses Pi. Each user runs Pi in
`PI_WORKSPACE/<login>/`, so files created with relative paths go to that login's
workspace. These folders separate organization and history; Pi retains the service
account's OS permissions.

When no conversation override is selected and `PI_PROVIDER` and `PI_MODEL` are
empty or unset, Pi uses its own default configuration, API credentials, and installed
extensions under the service account. No separate API setup is required in the
web page. Each turn starts a Pi subprocess and resumes its native session;
process state in extensions does not survive between turns. Per-session file locks
prevent concurrent turns across Gunicorn workers on the same host. Stop is
cross-worker; after a short graceful abort window the process group is terminated.
Timeout or browser disconnection also cleans up the subprocess. Keep these data
folders private and include them in backups. Multi-host deployments need shared
storage with reliable file locking or a dedicated agent service.

Use threaded Gunicorn so Stop can run while a stream is open, for example:

```sh
gunicorn -w 1 --threads 8 --timeout 660 -b 127.0.0.1:8000 app:app
```

Reserve enough threads for ordinary requests and Stop alongside active streams.
For Nginx, disable buffering and allow long reads for the Pi API:

```nginx
location /api/pi/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 660s;
}
```

Keep the prompt in a `.txt` file: the existing deploy script excludes Markdown.
Deploy application changes normally; no relational database migration is required.

For Linux/systemd setup, adapt `deployment/myblog_py.service` using the
instructions in `deployment/README.md`. Use your own service account, virtualenv,
Pi executable, and provider login. No server address or account credentials are
included in the project.

`deploy.sh` is machine-specific, ignored by Git, and absent from fresh clones.
Keep your deployment script and `.deploy.env` local. Use `.deploy.env.example`
as a starting point for private deployment settings, adapting your own script
as needed. Any `AGENTS.local.md` machine instructions also remain local.
Do not commit server credentials, certificates, databases, or user data.


## Interface language

Use the navigation bar's language selector to choose English or Simplified
Chinese. The page switches immediately when selected. The choice is remembered in this browser for one year
and is available before login. English is the default. No database migration or
additional Python dependency is needed for language selection.

Templates, flash messages, and Pi page controls/status messages share
`translations/zh-CN.json` through `utils/i18n.py` and `static/js/i18n.js`. User posts,
saved messages, filenames, model IDs, and saved system prompts keep their original
contents. Language support requirements for future changes are in `AGENTS.md`.


## Pi diagnostics and log retention

Application logs live in `logs/` (override with the server's `LOG_DIR` environment
variable). `agent.log` records request IDs, HTTP status, subprocess lifecycle,
command acceptance, tool start/end, retry/compaction, cancellation, and one progress
summary per minute during long tasks. `agent_error.log` contains errors, including
upstream HTTP codes when identifiable, exception types, and code frame locations.
Prompts, chat replies, tool arguments/results, upload names/contents, credentials,
raw stderr, and raw exception bodies are not written to these diagnostic logs.

Every Pi HTTP response has an `X-Request-ID` header for correlation. A streaming
response's initial 200 means the connection opened, not that the task finished;
look for `run.completed` and `stream.closed`. A proxy-generated 502 may never
reach Flask: correlate its timestamp with `gunicorn_error.log`, the systemd
journal, and the reverse proxy's error log. Abrupt worker kills cannot reliably
write a final application event.

All five managed files (`myblog.log`, `agent.log`, `agent_error.log`,
`gunicorn_access.log`, `gunicorn_error.log`) rotate before exceeding 20 MiB
(20,971,520 bytes), and on the first write after a date change. Example archives:
`agent.2026-10-29.001.log`, `agent.2026-10-29.002.log`. A process-safe file lock
protects writes and rotation across Gunicorn workers. A single oversized record
is truncated at 64 KiB. Retention is based on last-write time: startup and hourly
background cleanup remove managed files older than 30 days, including idle files
and backups from the previous numeric rotation scheme.

Gunicorn automatically loads `gunicorn.conf.py` from the project directory when this is
its working directory. It uses the same rotating handler, and access logs contain
only method, status, duration, PID and request ID. An explicit different Gunicorn
config must import/merge these logging settings. Proxy logs and the systemd
journal are managed separately by their own services.

### Pi long-term memory (nightly summaries)

The web application runs a background memory job every night at 03:00 in the
server's timezone. No cron service, additional package, or separate process needs
to be installed. It uses the web service account's existing Pi credentials.
Set `PI_MEMORY_ENABLED=False` to disable the scheduled job, or change
`PI_MEMORY_HOUR` (0–23) to choose a different hour.

The job reads completed user and assistant text from each user's `messages.jsonl`
files. Thinking blocks, tool output, attachments and native `session.jsonl` files
are not sent to the summary model. Busy conversations are skipped until the next
run. Legacy numeric user directories are picked up after the existing login-time
migration to `users/<login>/`.

Each user gets a private `PI_DATA_DIR/users/<login>/memory.json` containing a
summary and byte-offset checkpoints. The first job summarizes existing chats;
later jobs combine only new text with the previous summary. Large inputs are
processed in batches. Summary and checkpoints are saved together by atomic rename
only after all batches succeed. Failed jobs preserve the old state and retry next
time; overlapping jobs skip users already being processed. Back up memory files
with Pi data, and keep that directory out of Git and deployment uploads.

Each new Pi run (including resumed conversations) loads the latest summary into a
separate prompt section as potentially outdated background context. Saved prompt
presets and conversation transcripts are unchanged. Missing or corrupt memory does
not block chatting; corrupt state is not overwritten by the job. The model is
instructed to retain durable facts and preferences, omit secrets, and give current
user instructions priority. Summaries are model-generated and can be inaccurate.
Removing `memory.json` clears the injected summary, but a later job will rebuild
it from existing chats. It does not erase information in Pi's native chat history.

For a manual run, activate your deployment virtualenv first:

```sh
cd /path/to/application
. /path/to/virtualenv/bin/activate
python summarize_pi_memory.py
# Or process one login only:
python summarize_pi_memory.py --user alice
```

Gunicorn starts the scheduler in each worker using `gunicorn.conf.py`, including
with `--preload` and before any requests arrive. A shared file lock and persisted
run date in `PI_DATA_DIR` allow only one pass per day across workers on the same
host. Keep this data directory on storage supporting reliable local file locks.
An explicit different Gunicorn config must also import the `post_worker_init` and
`worker_exit` hooks. Flask's direct development server starts its scheduler in the
serving process; other WSGI setups start it on the first request.

After a restart later than the scheduled hour, a missed job for today runs on
startup. There is no separate replay per missed day: transcript checkpoints cover
all unprocessed text. Completed passes with per-user failures retry those users
on the next day. If a worker dies mid-pass, another worker can resume without
losing saved user summaries. Graceful worker shutdown cancels active summary
subprocesses. The scheduler is disabled in Flask tests.

`PI_MEMORY_MODEL=provider/model` optionally selects a summary model; otherwise it
uses `PI_PROVIDER`/`PI_MODEL` or Pi defaults. `PI_TIMEOUT` limits each batch. Logs
contain counts and error types, not conversation content. The optional manual
command loads the project's `.env`, needs no database connection, and exits
nonzero if any user fails while continuing to process other users. Do not also
install a cron job for the same task; the application now handles the schedule.
