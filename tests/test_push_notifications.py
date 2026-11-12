"""Job push notifications: authenticated device ownership and mocked APNs delivery."""
import json
import unittest
from unittest.mock import patch

from flask import Flask
from database import db
from models.user import User
from models.push_device import PushDevice
from routes.push import bp
from services.push_notifications import send_job_finished, _provider_token
from utils.jwt import generate_access_token


class PushNotificationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, JWT_SECRET_KEY='push-test',
                               SQLALCHEMY_DATABASE_URI='sqlite://',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(bp, url_prefix='/api/push')
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        db.session.add_all([User(id=1, username='a', email='a@test', password_hash='test'),
                            User(id=2, username='b', email='b@test', password_hash='test')])
        db.session.commit()
        self.client = self.app.test_client()
        self.payload = dict(installation_id='c17b6b25-71db-439a-969f-43ace6e60fc9',
                            device_token='ab' * 32, environment='development')

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def headers(self, uid=1):
        return {'Authorization': 'Bearer ' + generate_access_token(uid)}

    def register(self, uid=1, **changes):
        return self.client.post('/api/push/devices', json={**self.payload, **changes}, headers=self.headers(uid))

    def test_requires_authentication(self):
        self.assertEqual(self.client.post('/api/push/devices', json=self.payload).status_code, 401)

    def test_validates_request_without_echoing_token(self):
        for body in [[], 'bad', {}, {**self.payload, 'environment': 'invalid'},
                     {**self.payload, 'device_token': 'not-hex'},
                     {**self.payload, 'installation_id': 'bad'}]:
            response = self.client.post('/api/push/devices', json=body, headers=self.headers())
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(self.payload['device_token'], response.get_data(as_text=True))

    def test_registration_is_idempotent_and_uses_authenticated_user(self):
        self.assertEqual(self.register(user_id=2).status_code, 200)
        self.assertEqual(self.register().status_code, 200)
        self.assertEqual(PushDevice.query.count(), 1)
        self.assertEqual(PushDevice.query.one().user_id, 1)

    def test_account_switch_and_token_rotation_update_same_installation(self):
        self.register()
        self.register(uid=2, device_token='cd' * 40)
        self.assertEqual(PushDevice.query.count(), 1)
        self.assertEqual(PushDevice.query.one().user_id, 2)
        self.assertEqual(PushDevice.query.one().device_token, 'cd' * 40)

    def test_unregister_is_owner_scoped_and_idempotent(self):
        self.register()
        self.client.delete('/api/push/devices', json=self.payload, headers=self.headers(2))
        self.assertEqual(PushDevice.query.count(), 1)
        for _ in range(2):
            self.assertEqual(self.client.delete('/api/push/devices', json=self.payload, headers=self.headers()).status_code, 204)
        self.assertEqual(PushDevice.query.count(), 0)

    def test_same_token_is_not_left_registered_to_old_installation(self):
        self.register()
        self.register(uid=2, installation_id='735df354-fdb8-4014-840b-68f638f11825')
        self.assertEqual(PushDevice.query.count(), 1)
        self.assertEqual(PushDevice.query.one().user_id, 2)

    @patch('services.push_notifications._provider_token', return_value='test-provider-token')
    @patch('services.push_notifications.httpx.Client')
    def test_sender_targets_owner_and_correct_environment(self, client_type, token):
        self.register()
        self.register(uid=2, installation_id='735df354-fdb8-4014-840b-68f638f11825', device_token='cd' * 32, environment='production')
        client = client_type.return_value.__enter__.return_value
        client.post.return_value.status_code = 200
        result = send_job_finished(1, 'job-123', title='Job finished', body='Ready 手机')
        self.assertEqual(result, {'sent': 1, 'failed': 0, 'removed': 0})
        args, kwargs = client.post.call_args
        self.assertTrue(args[0].startswith('https://api.sandbox.push.apple.com/3/device/'))
        self.assertEqual(kwargs['headers']['apns-push-type'], 'alert')
        payload = json.loads(kwargs['content'])
        self.assertEqual(payload['type'], 'job_finished')
        self.assertEqual(payload['job_id'], 'job-123')
        self.assertEqual(payload['user_id'], 1)
        self.assertEqual(payload['aps']['alert']['body'], 'Ready 手机')
        self.assertTrue(client_type.call_args.kwargs['http2'])

    @patch('services.push_notifications._provider_token', return_value='test-provider-token')
    @patch('services.push_notifications.httpx.Client')
    def test_invalid_tokens_removed_but_transient_failures_retained(self, client_type, token):
        self.register()
        response = client_type.return_value.__enter__.return_value.post.return_value
        response.status_code = 503
        self.assertEqual(send_job_finished(1, 'job')['failed'], 1)
        self.assertEqual(PushDevice.query.count(), 1)
        response.status_code = 410
        self.assertEqual(send_job_finished(1, 'job')['removed'], 1)
        self.assertEqual(PushDevice.query.count(), 0)

    @patch('services.push_notifications._provider_token', return_value='test-provider-token')
    @patch('services.push_notifications.httpx.Client')
    def test_old_rejection_cannot_remove_refreshed_registration(self, client_type, token):
        self.register()
        def response(*args, **kwargs):
            self.register(uid=2, device_token='cd' * 32)
            class Rejection:
                status_code = 410
            return Rejection()
        client_type.return_value.__enter__.return_value.post.side_effect = response
        result = send_job_finished(1, 'job')
        self.assertEqual(result['removed'], 0)
        self.assertEqual(PushDevice.query.one().user_id, 2)
        self.assertEqual(PushDevice.query.one().device_token, 'cd' * 32)

    def test_provider_token_is_es256_signed_and_reused(self):
        import tempfile
        from pathlib import Path
        import jwt
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        key = ec.generate_private_key(ec.SECP256R1())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.p8'
            path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            self.app.config.update(APNS_TEAM_ID='test-team', APNS_KEY_ID='test-key', APNS_KEY_PATH=str(path))
            token = _provider_token()
            claims = jwt.decode(token, key.public_key(), algorithms=['ES256'])
            self.assertEqual(claims['iss'], 'test-team')
            self.assertEqual(jwt.get_unverified_header(token)['kid'], 'test-key')
            self.assertEqual(_provider_token(), token)

    def test_no_devices_needs_no_apns_credentials(self):
        self.assertEqual(send_job_finished(1, 'job'), {'sent': 0, 'failed': 0, 'removed': 0})

    def test_payload_size_limit_checked_before_sending(self):
        with self.assertRaises(ValueError):
            send_job_finished(1, 'job', body='大' * 2000)


if __name__ == '__main__':
    unittest.main()
