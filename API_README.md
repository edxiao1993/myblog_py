# API README

This project serves two clients:

- Web browser users, authenticated with Flask session cookies
- Native/mobile clients, authenticated with JWT bearer tokens

The website routes and the API routes share the same `User` table and the same `secret_moment` data, but they use different auth mechanisms.

## Auth Strategy

Use these auth modes as follows:

- Website: existing browser login form and Flask session cookie
- iOS app: JSON login endpoint that returns a JWT

Do not make the iOS app depend on the browser login page or session cookies.

## Base URL

Examples below assume:

```text
http://127.0.0.1:5000
```

If deployed, replace this with your HTTPS domain.

## Recommended Endpoints For iOS

### 1. Login

Request:

```http
POST /api/login
Content-Type: application/json
```

Body:

```json
{
  "username": "demo",
  "password": "your_password"
}
```

Success response:

```json
{
  "token": "<jwt-token>",
  "token_type": "Bearer",
  "expires_in": "24 hours",
  "user": {
    "id": 1,
    "username": "demo",
    "email": "demo@example.com"
  }
}
```

Failure responses:

```json
{
  "error": "username and password are required"
}
```

```json
{
  "error": "Invalid username or password"
}
```

### 2. Fetch Secret Moments

Request:

```http
GET /api/moments?page=1&per_page=10
Authorization: Bearer <jwt-token>
```

Success response:

```json
{
  "moments": [
    {
      "id": "f0e2e7d4c52d4ec8a9d4c8f4d82c1a33",
      "moment": "A secret message",
      "create_time": "2026-04-21 10:30:00"
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

Notes:

- `per_page` is capped at `50`
- results are ordered by `create_time` descending

### 3. Refresh Token

Request:

```http
POST /api/token/refresh
Authorization: Bearer <jwt-token>
```

Success response:

```json
{
  "token": "<new-jwt-token>",
  "expires_in": "24 hours"
}
```

Failure response:

```json
{
  "error": "Authentication required"
}
```

## cURL Examples

### Login

```bash
curl -X POST http://127.0.0.1:5000/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"your_password"}'
```

### Fetch Moments

```bash
curl "http://127.0.0.1:5000/api/moments?page=1&per_page=10" \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

### Refresh Token

```bash
curl -X POST http://127.0.0.1:5000/api/token/refresh \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

## Swift Example

This example uses `URLSession` directly.

```swift
import Foundation

struct LoginResponse: Decodable {
    let token: String
    let tokenType: String
    let expiresIn: String
    let user: APIUser

    enum CodingKeys: String, CodingKey {
        case token
        case tokenType = "token_type"
        case expiresIn = "expires_in"
        case user
    }
}

struct APIUser: Decodable {
    let id: Int
    let username: String
    let email: String
}

struct MomentsResponse: Decodable {
    let moments: [Moment]
    let page: Int
    let perPage: Int
    let pages: Int
    let total: Int
    let hasNext: Bool
    let hasPrev: Bool

    enum CodingKeys: String, CodingKey {
        case moments
        case page
        case perPage = "per_page"
        case pages
        case total
        case hasNext = "has_next"
        case hasPrev = "has_prev"
    }
}

struct Moment: Decodable, Identifiable {
    let id: String
    let moment: String
    let createTime: String

    enum CodingKeys: String, CodingKey {
        case id
        case moment
        case createTime = "create_time"
    }
}

final class APIClient {
    private let baseURL = URL(string: "http://127.0.0.1:5000")!
    private let decoder = JSONDecoder()

    func login(username: String, password: String) async throws -> LoginResponse {
        let url = baseURL.appending(path: "api/login")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "username": username,
            "password": password
        ])

        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        return try decoder.decode(LoginResponse.self, from: data)
    }

    func fetchMoments(token: String, page: Int = 1, perPage: Int = 10) async throws -> MomentsResponse {
        var components = URLComponents(url: baseURL.appending(path: "api/moments"), resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "page", value: String(page)),
            URLQueryItem(name: "per_page", value: String(perPage))
        ]

        var request = URLRequest(url: components.url!)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        return try decoder.decode(MomentsResponse.self, from: data)
    }

    func refreshToken(token: String) async throws -> String {
        let url = baseURL.appending(path: "api/token/refresh")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)

        let payload = try JSONDecoder().decode(TokenRefreshResponse.self, from: data)
        return payload.token
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let httpResponse = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "Unknown server error"
            throw NSError(
                domain: "APIClient",
                code: httpResponse.statusCode,
                userInfo: [NSLocalizedDescriptionKey: message]
            )
        }
    }
}

private struct TokenRefreshResponse: Decodable {
    let token: String
    let expiresIn: String

    enum CodingKeys: String, CodingKey {
        case token
        case expiresIn = "expires_in"
    }
}
```

## Storage Guidance For iOS

- Store the JWT in Keychain, not in `UserDefaults`
- Clear the token on logout
- On `401`, redirect the user back to login or try refresh logic first

## Production Notes

- Use HTTPS only
- Set a strong `SECRET_KEY`
- Set a strong `JWT_SECRET_KEY`
- Do not use default credentials from local development

## Legacy Token Endpoints

This repo still contains older token endpoints such as:

- `POST /api/setup-token`
- `POST /api/token/generate`
- `POST /api/verify-token`
- `GET /api/protected-example`

They are based on a custom `X-Token` design. In the current codebase, that path is not the recommended mobile auth flow. Use JWT with `POST /api/login` and `Authorization: Bearer <ACCESS_TOKEN>` for iOS.
