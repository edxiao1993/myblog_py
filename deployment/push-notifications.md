# APNs notifications for scheduled jobs

## Configure the server

Install the updated requirements in the web service's existing virtual environment.
The additions are `httpx[http2]` (HTTP/2) and the cryptography extra for the existing
PyJWT dependency (ES256 signatures).

Set these variables in the server's private environment configuration, not in Git:

```dotenv
APNS_TEAM_ID=YOUR_APPLE_TEAM_ID
APNS_KEY_ID=YOUR_APNS_KEY_ID
APNS_KEY_PATH=/etc/myblog/AuthKey_YOUR_APNS_KEY_ID.p8
APNS_TOPIC=com.kevinchien.BlueNest
```

The `.p8` must be an Apple APNs signing key, not your HTTPS certificate. Install it
outside the repository, restrict it to the service account, and keep it out of
backups or uploads that are not intended to carry credentials. The HTTP client
validates Apple's TLS certificate; no custom trust bypass is used for APNs.

Restart the web service after installing dependencies. Its existing `db.create_all`
initialization creates the new `push_device` table; no existing table is altered.
If production permissions do not allow creating tables, have your database admin
create it through the normal schema deployment process first.

## Register the phone

Install the updated iOS app, sign in to Remote Account and choose **Enable Job
Notifications**. Registration is authenticated using the existing mobile JWT.
`POST /api/push/devices` accepts `installation_id` (UUID), `device_token` (hex) and
`environment` (`development` or `production`). `DELETE` on the same path removes
only the current user's specified installation. Registering the same installation
updates its token and owner, so it supports token changes and account switching.

Do not add an unauthenticated endpoint that sends notifications to arbitrary users.
The server stores the tokens and sends them only to Apple, never to another user.

## Call after your scheduled job succeeds

From a job already running in a Flask app context:

```python
from services.push_notifications import send_job_finished

# First finish the job and commit its result to your database.
delivery = send_job_finished(
    user_id=job_owner_id,
    job_id=completed_job_id,
    title="Daily briefing ready",
    body="Your scheduled research has finished. Open BlueNest to view the summary.",
)
```

If your scheduler runs in a separate process, create/use the project's Flask app
and wrap the call in `with app.app_context():`. Use the actual job owner's database
ID. Do not hard-code an administrator ID for every notification. This feature does
not create a new scheduler or change the existing Pi memory scheduler.

The function returns `{"sent": n, "failed": n, "removed": n}`. These count APNs
acceptances, failures and invalid registrations removed after HTTP 410, provided the original registration has not been refreshed while
the request was in flight. Failed
registrations are retained for a later job-level retry. Check the returned counts;
an accepted request does not guarantee delivery or display. Keep notification
errors separate from the success of the already-completed job.

Missing APNs credentials or oversized payloads raise a configuration/validation
error. Keep summaries short: the entire APNs payload must fit in 4096 UTF-8 bytes.
Use generic summaries on the lock screen; retrieve sensitive full results through
an authenticated result API when you add one. Logout deregistration is best effort
when the phone is offline. Device tokens and provider JWTs are not logged.

Debug app registrations select Apple's sandbox; Release registrations select
production. The server reuses its ES256 provider JWT for up to 50 minutes, and
keeps the private key exclusively on the server. Notification taps open Today and
show the title, body and job ID; no result endpoint or arbitrary URL is assumed.

## First end-to-end check

1. Enable APNs for the app's bundle ID in the Apple Developer account and use an
   APNs-enabled signing profile to install the app on a real iPhone.
2. Confirm **Job notifications are enabled** in Remote Account.
3. Call `send_job_finished` for that user's ID and a unique test job ID from the
   existing job runner/app context. Check that `sent` is at least 1.
4. Verify receipt foregrounded, backgrounded and terminated; tap the notification
   and confirm Today shows the correct summary. Check denial and account switching.

References:
- https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns
- https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns
