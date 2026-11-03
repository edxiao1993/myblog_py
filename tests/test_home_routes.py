"""Pi home, Posts navigation, and removal of About without touching real data."""
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit

from flask import Flask, url_for
from flask_login import LoginManager
from database import db
from models.post import Post
from models.user import User
from routes import register_blueprints
from utils.i18n import init_i18n


class HomeRoutesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.app = Flask(__name__, template_folder=str(Path('templates').resolve()))
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://',
            SQLALCHEMY_TRACK_MODIFICATIONS=False, PI_DATA_DIR=str(root / 'pi'),
            PI_WORKSPACE=str(root / 'workspace'), PI_SYSTEM_PROMPT_FILE=str(root / 'prompt.txt'))
        (root / 'prompt.txt').write_text('Test instructions')
        db.init_app(self.app)
        init_i18n(self.app)
        login = LoginManager(self.app)
        login.login_view = 'auth.login'
        login.user_loader(lambda uid: db.session.get(User, int(uid)))
        register_blueprints(self.app)
        with self.app.app_context():
            db.create_all()
            user = User(id=1, username='owner', email='owner@example.test')
            user.set_password('test-password')
            db.session.add(user)
            db.session.add(Post(id=1, title='Existing personal post', content='Original post content'))
            db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = '1'
            session['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.directory.cleanup()

    def test_pi_is_home_and_legacy_agent_url_still_works(self):
        with self.app.test_request_context():
            self.assertEqual(url_for('pi_agent.page'), '/')
            self.assertEqual(url_for('main.index'), '/posts')
        for path in ['/', '/pi-agent', '/?conversation=' + 'a' * 32]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'id="pi-app"', response.data)
            self.assertNotIn(b'Existing personal post', response.data)

    def test_posts_and_existing_post_links(self):
        response = self.client.get('/posts')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Existing personal post', response.data)
        self.assertIn(b'href="/post/1"', response.data)
        response = self.client.get('/post/1')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Back to Posts', response.data)
        self.assertIn(b'href="/posts"', response.data)

    def test_about_is_removed_and_navigation_is_bilingual(self):
        self.assertEqual(self.client.get('/about').status_code, 404)
        for locale, label in [('en', 'Posts'), ('zh-CN', '文章')]:
            self.client.set_cookie('language', locale, domain='localhost')
            body = self.client.get('/').get_data(as_text=True)
            self.assertIn('href="/posts"', body)
            self.assertIn(label, body)
            self.assertNotIn('href="/about"', body)

    def test_login_and_create_redirect_to_correct_pages(self):
        with self.client.session_transaction() as session:
            session.clear()
        for path in ['/', '/posts']:
            self.assertEqual(urlsplit(self.client.get(path).location).path, '/login')
        response = self.client.post('/login', data={'username': 'owner', 'password': 'test-password'})
        self.assertEqual(response.location, '/')
        response = self.client.post('/create', data={'title': 'New personal post', 'content': 'Content'})
        self.assertEqual(response.location, '/posts')


if __name__ == '__main__':
    unittest.main()
