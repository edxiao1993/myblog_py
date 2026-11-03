# Token-Based Authentication Guide

## Overview

This application uses HMAC-SHA256 token authentication for API access. The token is generated using:
- Username (stored in token)
- Timestamp (stored in token)
- API Secret (stored in database, never transmitted)
- Server Secret Key (environment variable)

**The token cannot be decrypted** - it uses one-way HMAC hashing.

## How It Works

1. **Client gets timestamp**: Call `/api/timestamp` to get current server time
2. **Client generates token**: `HMAC-SHA256(username:timestamp, api_secret + server_secret)`
3. **Client makes API calls**: Include token in `X-Token` header only
4. **Server verifies**: Extracts username from token, looks up API secret from database, regenerates and compares

## Setup (One-Time)

### Step 1: Login and get API secret
```bash
# First, login through the normal web interface to get a session cookie
# Then call the setup endpoint:
POST /api/setup-token
Headers: Cookie: <session_cookie>
Response: {
  "api_secret": "your_unique_secret_key",
  "message": "Save this secret securely..."
}
```

⚠️ **Save the `api_secret`** - it's only shown once!

### Step 2: Add server secret (admin)
In your `.env` file:
```
TOKEN_SECRET_KEY=your-server-secret-key
```

## API Endpoints

### Get Timestamp (Public)
```
GET /api/timestamp
Response: {"timestamp": 1234567890}
```

### Setup Token Auth (Requires Login)
```
POST /api/setup-token
Requires: Session authentication
Response: {"api_secret": "abc123...", "message": "..."}
```

### Generate Token (Requires Login)
```
POST /api/token/generate
Requires: Session authentication
Response: {"token": "generated_token", "timestamp": 1234567890, "expires_in": "5 minutes"}
```

### Verify Token (Public)
```
POST /api/verify-token
Content-Type: application/json
Body: {"token": "your_token"}
Response: {"valid": true, "username": "user"} or {"valid": false, "error": "..."}
```

### Protected API Example
```
GET /api/protected-example
Headers:
  X-Token: <your_generated_token>
Response: {"message": "This is protected data", "user": "username", "email": "..."}
```

## Client Implementation

### Python Example
```python
import requests
import hmac
import hashlib
import base64

API_BASE = "http://localhost:5000"
USERNAME = "your_username"
API_SECRET = "your_api_secret_from_setup"  # From /api/setup-token
SERVER_SECRET = "your_server_secret"  # From TOKEN_SECRET_KEY env var

# Step 1: Get timestamp
resp = requests.get(f"{API_BASE}/api/timestamp")
timestamp = resp.json()['timestamp']

# Step 2: Generate token
message = f"{USERNAME}:{timestamp}"
key = f"{API_SECRET}:{SERVER_SECRET}".encode('utf-8')
signature = hmac.new(key, message.encode('utf-8'), hashlib.sha256).hexdigest()
token_data = f"{USERNAME}:{timestamp}:{signature}"
token = base64.b64encode(token_data.encode('utf-8')).decode('utf-8')

# Step 3: Make authenticated API call (no password needed!)
headers = {'X-Token': token}
resp = requests.get(f"{API_BASE}/api/protected-example", headers=headers)
print(resp.json())
```

### JavaScript/Node.js Example
```javascript
const crypto = require('crypto');
const axios = require('axios');

const API_BASE = 'http://localhost:5000';
const USERNAME = 'your_username';
const API_SECRET = 'your_api_secret_from_setup';
const SERVER_SECRET = 'your_server_secret';

async function makeAuthenticatedRequest() {
    // Step 1: Get timestamp
    const timestampResp = await axios.get(`${API_BASE}/api/timestamp`);
    const timestamp = timestampResp.data.timestamp;
    
    // Step 2: Generate token
    const message = `${USERNAME}:${timestamp}`;
    const key = `${API_SECRET}:${SERVER_SECRET}`;
    const signature = crypto
        .createHmac('sha256', key)
        .update(message)
        .digest('hex');
    
    const tokenData = `${USERNAME}:${timestamp}:${signature}`;
    const token = Buffer.from(tokenData).toString('base64');
    
    // Step 3: Make authenticated request (no password in request!)
    const resp = await axios.get(`${API_BASE}/api/protected-example`, {
        headers: {'X-Token': token}
    });
    
    console.log(resp.data);
}
```

### cURL Example
```bash
# Get timestamp
TIMESTAMP=$(curl -s http://localhost:5000/api/timestamp | jq -r '.timestamp')

# Generate token (using Python one-liner for HMAC)
TOKEN=$(python3 -c "
import hmac, hashlib, base64
msg = '${USERNAME}:${TIMESTAMP}'
key = '${API_SECRET}:${SERVER_SECRET}'.encode()
sig = hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()
print(base64.b64encode(f'{USERNAME}:{TIMESTAMP}:{sig}'.encode()).decode())
")

# Make authenticated request
curl -X GET http://localhost:5000/api/protected-example \
  -H "X-Token: ${TOKEN}"
```

## Token Expiration

- Tokens expire after **5 minutes** (300 seconds)
- Get a new timestamp and regenerate the token when expired
- No need to re-authenticate - just get a new timestamp

## Security Features

✅ **Password never transmitted** - API secret stored in DB  
✅ **Token cannot be decrypted** - HMAC-SHA256 one-way hash  
✅ **Short-lived tokens** - 5 minute expiration  
✅ **Server secret required** - Even with token, attacker needs server secret  
✅ **Per-user API secrets** - Each user has unique secret  

## Security Notes

⚠️ **Important:**

1. **Always use HTTPS** in production
2. **Save API secret securely** - it's only shown once during setup
3. **Set strong TOKEN_SECRET_KEY** in environment variables
4. **Rotate API secrets** if compromised (call `/api/setup-token` again)

## Using @token_required Decorator

```python
from flask import Blueprint, request, jsonify
from utils.token import token_required

bp = Blueprint('my_api', __name__)

@bp.route('/api/my-endpoint', methods=['GET'])
@token_required
def my_endpoint():
    user = request.current_user
    return jsonify({'data': 'protected', 'user': user.username})
```

## Database Migration

After updating the User model, run:
```bash
flask db migrate -m "Add api_secret to User"
flask db upgrade
```

For existing users, generate API secrets:
```python
from models.user import User
from database import db

with app.app_context():
    users = User.query.all()
    for user in users:
        if not user.api_secret:
            user.generate_api_secret()
    db.session.commit()
```
