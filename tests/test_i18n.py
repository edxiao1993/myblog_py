"""Language preferences, translated UI, and safe language switching."""
from pathlib import Path
import unittest

from flask import Flask, render_template
from flask_login import LoginManager, UserMixin, login_user


class LanguageTests(unittest.TestCase):
    def setUp(self):
        from utils.i18n import init_i18n
        self.app = Flask(__name__, template_folder=str(Path('templates').resolve()))
        self.app.config.update(TESTING=True, SECRET_KEY='test')
        login = LoginManager(self.app)
        class TestUser(UserMixin):
            id = '1'
            username = '<script>alert(1)</script>'
        self.user = TestUser()
        login.user_loader(lambda uid: self.user)
        init_i18n(self.app)
        endpoints = ['main.index', 'main.create_post', 'moments.moments',
                     'ai_sessions.sessions_list', 'pi_agent.page', 'main.view_post', 'moments.add_moment', 'auth.login', 'auth.register', 'auth.logout']
        for endpoint in endpoints:
            self.app.add_url_rule('/test/' + endpoint, endpoint, lambda: '')
        @self.app.get('/preview/<name>')
        def preview(name):
            from datetime import datetime
            from types import SimpleNamespace
            now = datetime(2026, 10, 27)
            return render_template(name + '.html', posts=[], sessions=[], moments=[],
                post=SimpleNamespace(title='Home', content='User content stays unchanged', created_at=now, updated_at=now),
                pagination=SimpleNamespace(pages=1),
                session={'title': 'Saved title', 'messages': [], 'model_name': 'test/model'},
                prompt='Original instructions', csrf='test', presets={}, upload_limit_mb=200)
        @self.app.get('/signed-in')
        def signed_in():
            login_user(self.user)
            return render_template('index.html', posts=[])
        self.client = self.app.test_client()

    def switch(self, language, next_page='/preview/login'):
        self.client.get('/preview/login')
        with self.client.session_transaction() as session:
            token = session['language_csrf']
        return self.client.post('/language', data={'language': language, 'csrf_token': token, 'next': next_page})

    def test_general_prompt_option_is_default_and_translated(self):
        for language, label in [('en', 'General assistant'), ('zh-CN', '通用助手')]:
            self.client.set_cookie('language', language)
            body = self.client.get('/preview/pi_agent').get_data(as_text=True)
            self.assertIn('<option value="general" selected>' + label + '</option>', body)
            self.assertIn('Create a custom prompt' if language == 'en' else '创建自定义提示词', body)
            self.assertIn('Save prompt' if language == 'en' else '保存提示词', body)

    def test_pi_activity_toggle_is_translated(self):
        for language, label in [('en', 'Display tool execution and thinking cards in conversation'),
                                ('zh-CN', '在对话中显示工具执行和思考卡片')]:
            self.client.set_cookie('language', language)
            body = self.client.get('/preview/pi_agent').get_data(as_text=True)
            self.assertIn(label, body)
            self.assertIn('pi-wrapper hide-tools pi-desktop-sidebar-hidden', body)

    def test_pi_mobile_greeting_and_navigation_follow_browser_language(self):
        for language, greeting, placeholder, settings in [
                ('en', 'What’s the plan for today?', 'Message Pi…', 'Settings'),
                ('zh-CN', '今天有什么计划？', '给 Pi 发消息…', '设置')]:
            self.client.set_cookie('language', language)
            body = self.client.get('/preview/pi_agent').get_data(as_text=True)
            self.assertIn(greeting, body)
            self.assertIn(placeholder, body)
            self.assertIn(settings, body)
            self.assertIn('Jump to latest' if language == 'en' else '跳到最新消息', body)
            self.assertIn('id="pi-language-choice"', body)
            self.assertIn('name="csrf_token"', body)
            self.assertIn('name="next" value="/preview/pi_agent?"', body)
            self.assertIn('More chat options' if language == 'en' else '更多对话选项', body)

    def test_default_english_and_language_picker(self):
        body = self.client.get('/preview/login').get_data(as_text=True)
        self.assertIn('<html lang="en">', body)
        self.assertIn('English', body)
        self.assertIn('简体中文', body)
        self.assertIn('Username', body)

    def test_chinese_persists_and_can_switch_back(self):
        response = self.switch('zh-CN', '/preview/login?next=%2F')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/preview/login?next=%2F')
        self.assertIn('Max-Age=', response.headers['Set-Cookie'])
        for name, text in [('login', '用户名'), ('register', '注册'), ('index', '文章'),
                           ('moments', '私密动态'), ('ai_sessions', 'AI 会话'), ('create', '新建文章'),
                           ('post', '返回文章列表'), ('add_moment', '你的私密动态'),
                           ('ai_session_detail', '返回会话列表'), ('pi_agent', '思考强度')]:
            body = self.client.get('/preview/' + name).get_data(as_text=True)
            self.assertIn('<html lang="zh-CN">', body)
            self.assertIn(text, body)
        self.switch('en')
        self.assertIn('Username', self.client.get('/preview/login').get_data(as_text=True))

    def test_preference_is_per_browser_and_invalid_cookie_falls_back(self):
        self.switch('zh-CN')
        other = self.app.test_client()
        self.assertIn('<html lang="en">', other.get('/preview/login').get_data(as_text=True))
        other.set_cookie('language', 'invalid', domain='localhost')
        self.assertIn('<html lang="en">', other.get('/preview/login').get_data(as_text=True))

    def test_switch_rejects_csrf_and_unsupported_language(self):
        self.assertEqual(self.client.post('/language', data={'language': 'zh-CN'}).status_code, 403)
        self.assertEqual(self.switch('invalid').status_code, 400)

    def test_switch_never_redirects_off_site(self):
        for target in ['https://example.com', '//example.com', '/\\example.com', '/\r\nLocation: evil', '//[']:
            result = self.switch('zh-CN', target)
            self.assertEqual(result.headers['Location'], '/')

    def test_catalog_covers_ui_keys_and_named_placeholders(self):
        import ast
        import re
        from utils.i18n import ZH_CN
        root = Path(__file__).resolve().parents[1]
        literal = r"(?:'(?:\\.|[^'\\])*')|(?:\"(?:\\.|[^\"\\])*\")"
        pattern = re.compile(r"(?<![\w])(?:_|t|status)\(\s*(" + literal + r")")
        for path in [*root.joinpath('templates').glob('*.html'), root / 'static/js/pi-agent.js']:
            for match in pattern.finditer(path.read_text()):
                key = ast.literal_eval(match[1])
                self.assertIn(key, ZH_CN, f'Missing Chinese translation: {path.name}: {key}')
        for key, value in ZH_CN.items():
            self.assertEqual(set(re.findall(r'\{(\w+)\}', key)), set(re.findall(r'\{(\w+)\}', value)), key)

    def test_interface_language_does_not_translate_user_content(self):
        self.switch('zh-CN')
        body = self.client.get('/preview/post').get_data(as_text=True)
        self.assertIn('<h1>Home</h1>', body)
        self.assertIn('User content stays unchanged', body)

    def test_dynamic_values_stay_escaped_and_missing_keys_fall_back(self):
        from utils.i18n import translate
        self.switch('zh-CN')
        body = self.client.get('/signed-in').get_data(as_text=True)
        self.assertIn('&lt;script&gt;', body)
        self.assertNotIn('<script>alert(1)</script>', body)
        with self.app.test_request_context(headers={'Cookie': 'language=zh-CN'}):
            self.assertEqual(translate('Uncatalogued label'), 'Uncatalogued label')


if __name__ == '__main__':
    unittest.main()
