"""Secret Moments must remain private through both web and JWT API routes."""
from datetime import datetime
from pathlib import Path
import unittest

from utils.i18n import init_i18n
from flask import Flask, g
from flask_login import LoginManager
from sqlalchemy import inspect, text

from database import db
from models.secret_moment import SecretMoment
from models.user import User
from routes.moments import bp as moments_bp
from routes.app_api import bp as api_bp
from utils.jwt import generate_access_token


class MomentPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(Path('templates').resolve()))
        self.app.config.update(TESTING=True, SECRET_KEY='test', JWT_SECRET_KEY='test',
                               SQLALCHEMY_DATABASE_URI='sqlite://',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        init_i18n(self.app)
        login = LoginManager(self.app)
        login.user_loader(lambda uid: db.session.get(User, int(uid)))
        self.app.register_blueprint(moments_bp, url_prefix='/moments')
        self.app.register_blueprint(api_bp, url_prefix='/api')
        for endpoint in ['main.index', 'ai_sessions.sessions_list',
                         'pi_agent.page', 'auth.logout']:
            self.app.add_url_rule('/test/' + endpoint, endpoint, lambda: '')
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.assertIn('user_id', {column['name'] for column in inspect(db.engine).get_columns('secret_moment')})
        db.session.add_all([
            User(id=1, username='alice', email='alice@test', password_hash='test'),
            User(id=2, username='bob', email='bob@test', password_hash='test'),
        ])
        for owner in [1, 2]:
            for index in range(12):
                db.session.execute(text(
                    'INSERT INTO secret_moment (id, moment, create_time, user_id) VALUES (:id, :moment, :created, :owner)'),
                    {'id': f'{owner}-{index}', 'moment': f'private-user-{owner}-{index}',
                     'created': datetime(2026, 10, 27, 0, index).isoformat(' '), 'owner': owner})
        db.session.add(SecretMoment(id='legacy', moment='unassigned-legacy-secret'))
        db.session.commit()
        self.client = self.app.test_client()
        self.login(1)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def login(self, uid):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(uid)
            session['_fresh'] = True
        g.pop('_login_user', None)  # Each simulated login starts a fresh request identity.

    def headers(self, uid):
        return {'Authorization': 'Bearer ' + generate_access_token(uid)}

    def test_web_lists_only_current_user_on_every_page(self):
        for owner in [1, 2]:
            self.login(owner)
            for path in ['/moments/', '/moments/page/2']:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn(f'private-user-{owner}-', body)
                self.assertNotIn(f'private-user-{3 - owner}-', body)
                self.assertNotIn('unassigned-legacy-secret', body)

    def test_api_filters_rows_and_pagination_totals(self):
        for owner in [1, 2]:
            result = self.client.get('/api/moments?per_page=50', headers=self.headers(owner))
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json['total'], 12)
            self.assertTrue(all(row['moment'].startswith(f'private-user-{owner}-') for row in result.json['moments']))
            result = self.client.get('/api/mm/2?per_page=10', headers=self.headers(owner))
            self.assertEqual(len(result.json['moments']), 2)
            self.assertEqual(result.json['pages'], 2)

    def test_creation_uses_authenticated_owner_not_supplied_owner(self):
        result = self.client.post('/moments/add_moment', data={'moment': 'created via web', 'user_id': 2})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(SecretMoment.query.filter_by(moment='created via web').one().user_id, 1)
        result = self.client.post('/api/moments', json={'moment': 'created via api', 'user_id': 1}, headers=self.headers(2))
        self.assertEqual(result.status_code, 201)
        self.assertEqual(db.session.get(SecretMoment, result.json['moment']['id']).user_id, 2)

    def test_web_delete_checks_owner_and_hides_legacy(self):
        for moment_id in ['2-0', 'legacy', 'missing']:
            self.assertEqual(self.client.post(f'/moments/delete_moment/{moment_id}').status_code, 404)
        self.assertIsNotNone(db.session.get(SecretMoment, '2-0'))
        self.assertEqual(self.client.post('/moments/delete_moment/1-0').status_code, 302)
        self.assertIsNone(db.session.get(SecretMoment, '1-0'))

    def test_api_delete_checks_owner_and_hides_legacy(self):
        for moment_id in ['1-0', 'legacy', 'missing']:
            self.assertEqual(self.client.delete(f'/api/moments/{moment_id}', headers=self.headers(2)).status_code, 404)
        self.assertIsNotNone(db.session.get(SecretMoment, '1-0'))
        self.assertEqual(self.client.delete('/api/moments/2-0', headers=self.headers(2)).status_code, 200)
        self.assertIsNone(db.session.get(SecretMoment, '2-0'))

    def test_login_and_token_required(self):
        with self.client.session_transaction() as session:
            session.clear()
        g.pop('_login_user', None)
        self.assertEqual(self.client.get('/moments/').status_code, 401)
        self.assertEqual(self.client.get('/api/moments').status_code, 401)


if __name__ == '__main__':
    unittest.main()
