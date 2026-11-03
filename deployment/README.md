# Linux deployment example

Customize `myblog_py.service` before installing it. Replace `appuser`, its home,
the project path, virtualenv path, and PATH entries with your own values.
Install dependencies and configure `.env` as described in the main README.
Set `FLASK_ENV=production` and `FLASK_DEBUG=False` for HTTPS production use.

The service account must be able to read the application and its private `.env`,
and write `data/` and `logs/`. Keep `.env` readable only by the service account.
If you enable Pi, install Node and Pi for that account, authenticate Pi with your
own provider credentials, and set `PI_EXECUTABLE` to its absolute executable path.
Set `PI_CODING_AGENT_DIR` only if you need a non-default Pi configuration folder.
Systemd does not source interactive shell or nvm startup files.

Install the customized unit as `/etc/systemd/system/myblog_py.service`, then run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now myblog_py.service
sudo systemctl status myblog_py.service --no-pager
```

Terminate HTTPS at your reverse proxy and configure proxy timeouts/buffering for
Pi streams as described in the main README. The example listens only on localhost.
Adjust filesystem restrictions and certificate access for your deployment.
Back up databases and Pi session files separately; they are never included in Git.
