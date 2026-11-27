"""Language preferences, translated UI, and safe language switching."""
from pathlib import Path
import json
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
                     'ai_sessions.sessions_list', 'pi_agent.page', 'coding_agent.page', 'main.view_post', 'moments.add_moment', 'auth.login', 'auth.register', 'auth.logout']
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

    def test_shared_theme_is_available_on_all_pages_in_both_languages(self):
        pages = ['index', 'post', 'create', 'moments', 'add_moment', 'login', 'register', 'ai_sessions', 'ai_session_detail', 'pi_agent', 'coding_agent']
        for language in ('en', 'zh-CN'):
            self.client.set_cookie('language', language)
            for page in pages:
                with self.subTest(language=language, page=page):
                    body = self.client.get('/preview/' + page).get_data(as_text=True)
                    self.assertEqual(body.count('id="app-theme"'), 1)
                    self.assertIn('js/theme.js', body)
                    self.assertIn('css/theme.css', body)
                    self.assertNotIn('js/coding-theme.js', body)
                    self.assertIn('aria-label="' + ('夜间模式' if language == 'zh-CN' else 'Night view') + '"', body)
                    if page == 'pi_agent':
                        self.assertIn('id="pi-mobile-theme"', body)

    def test_posts_create_button_is_icon_only_with_translated_accessible_label(self):
        for language, label in [('en', 'Create New Post'), ('zh-CN', '新建文章')]:
            self.client.set_cookie('language', language)
            body = self.client.get('/preview/index').get_data(as_text=True)
            header = body.split('<!-- Simple Header Bar -->')[1].split('<!-- Posts Feed -->')[0]
            self.assertIn('aria-label="' + label + '"', header)
            self.assertIn('title="' + label + '"', header)
            self.assertIn('<span aria-hidden="true">+</span>', header)
            self.assertNotIn('<span>' + label + '</span>', header)

    def test_coding_delete_messages_are_translated(self):
        expected = {
            'Delete conversation?': '删除对话？',
            'Delete conversation: {title}': '删除对话：{title}',
            'Delete "{title}" from history? Pi transcript files will be preserved.': '从历史记录中删除“{title}”？Pi 对话记录文件将被保留。',
            'Conversation deleted from history.': '对话已从历史记录中删除。',
            'Unable to delete this conversation.': '无法删除此对话。',
        }
        for language in ('en', 'zh-CN'):
            self.client.set_cookie('language', language)
            for page in ('coding_agent', 'pi_agent'):
                body = self.client.get('/preview/' + page).get_data(as_text=True)
                catalog = json.loads(body.split('<script id="i18n-data" type="application/json">')[1].split('</script>')[0])['messages']
                for key, chinese in expected.items():
                    self.assertEqual(catalog.get(key, key), chinese if language == 'zh-CN' else key)
                self.assertIn('id="' + ('pi' if page == 'pi_agent' else 'coding') + '-delete-dialog"', body)

    def test_coding_page_languages_and_mobile_layout(self):
        for language, label in [('en', 'Project folder'), ('zh-CN', '项目文件夹')]:
            self.client.set_cookie('language', language)
            body = self.client.get('/preview/coding_agent').get_data(as_text=True)
            self.assertIn(label, body)
            self.assertIn('@media(max-width:767px)', body)
            self.assertIn('夜间模式' if language == 'zh-CN' else 'Night view', body)
            self.assertIn('id="app-theme"', body)
            navbar = body.split('<div id="coding-app"')[0]
            self.assertIn('id="app-theme"', navbar)
            self.assertIn('☀', navbar)
            self.assertIn('☾', navbar)
            self.assertIn('aria-label="' + ('夜间模式' if language == 'zh-CN' else 'Night view') + '"', navbar)
            self.assertIn('id="coding-sidebar-toggle"', body)
            self.assertIn('aria-controls="coding-sidebar" aria-expanded="false"', body)
            self.assertIn('项目与历史记录' if language == 'zh-CN' else 'Project and history', body)
            self.assertIn('css/coding-mobile.css', body)
            self.assertNotIn('.coding-layout>aside,.coding-chat>.coding-controls{display:none}', body)
            self.assertNotIn('coding-sidebar-show', body)
            self.assertNotIn('data-sidebar-hidden', body)
            self.assertIn('grid-template-columns:240px minmax(0,1fr)', body)
            self.assertIn('id="coding-sidebar" class="card p-2"', body)
            self.assertIn('id="coding-delete-dialog" aria-labelledby="coding-delete-title" aria-describedby="coding-delete-description"', body)
            self.assertIn('删除对话？' if language == 'zh-CN' else 'Delete conversation?', body)
            self.assertIn('id="coding-delete-cancel"', body)
            self.assertIn('id="coding-delete-confirm"', body)
            self.assertIn('id="coding-activity-spinner"', body)
            self.assertIn('id="coding-activity" role="status" aria-live="polite"></span>', body)
            self.assertIn('id="coding-message" aria-label="' + ('消息' if language == 'zh-CN' else 'Message') + '"', body)
            catalog = json.loads(body.split('<script id="i18n-data" type="application/json">')[1].split('</script>')[0])['messages']
            self.assertEqual(catalog.get('Rename conversation', 'Rename conversation'), '重命名对话' if language == 'zh-CN' else 'Rename conversation')
            self.assertEqual(catalog.get('Rename conversation: {title}', 'Rename conversation: {title}'), '重命名对话：{title}' if language == 'zh-CN' else 'Rename conversation: {title}')
            self.assertEqual(catalog.get('Agent is working…', 'Agent is working…'), '助手正在处理…' if language == 'zh-CN' else 'Agent is working…')
            self.assertEqual(catalog.get('Running a tool…', 'Running a tool…'), '正在运行工具…' if language == 'zh-CN' else 'Running a tool…')
            self.assertIn('prefers-reduced-motion:reduce', body)
            self.assertIn('class="coding-conversation mb-2"', body)
            self.assertIn('width:24px;height:24px;min-height:24px', body)
            self.assertIn('id="coding-jump-latest"', body)
            jump = body.split('<button id="coding-jump-latest"')[1].split('</button>')[0]
            self.assertIn('aria-label="' + ('跳到最新消息' if language == 'zh-CN' else 'Jump to latest') + '"', jump)
            self.assertIn(' hidden>', jump)
            self.assertEqual(jump.split(' hidden>')[1], '<span aria-hidden="true">↓</span>')
            self.assertIn('aria-controls="coding-messages"', body)
            self.assertIn('跳到最新消息' if language == 'zh-CN' else 'Jump to latest', body)
            self.assertNotIn('custom-prompt', body)
            self.assertIn('<dialog id="coding-directory-dialog"', body)
            self.assertNotIn('id="coding-path"', body.split('</aside>')[0])
            self.assertNotIn('id="coding-project"', body)
            self.assertNotIn('id="coding-arguments"', body)
            self.assertNotIn('id="coding-execute"', body)
            self.assertIn('选择项目文件夹' if language == 'zh-CN' else 'Choose project folder', body)
            self.assertIn('浏览文件夹' if language == 'zh-CN' else 'Browse folders', body)


    def test_coding_mobile_css_keeps_settings_and_touch_actions_available(self):
        css = Path('static/css/coding-mobile.css').read_text()
        self.assertIn('@media(max-width:767px)', css)
        self.assertIn('#coding-sidebar:not([data-mobile-open="true"])', css)
        self.assertIn('grid-template-columns: repeat(2, minmax(0, 1fr))', css)
        self.assertIn('grid-column: 1 / -1', css)
        self.assertIn('font-size: 16px', css)
        self.assertIn('min-height: 44px', css)
        self.assertIn('max-height: 85dvh', css)
        self.assertIn('flex-wrap: wrap', css)
        self.assertIn('#coding-app .coding-history-rename', css)

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

    def test_pi_long_running_activity_messages_are_translated(self):
        expected = {
            '{action} ({seconds}s)': '{action}（{seconds}秒）',
            'Connecting to Pi…': '正在连接 Pi…',
            'Connecting to Pi, waiting for response… ({seconds}s)': '正在连接 Pi，等待响应…（{seconds}秒）',
            'Thinking deeply, analyzing the problem… ({seconds}s)': '正在深入思考，分析问题中…（{seconds}秒）',
            'Still thinking, working through complex steps… ({seconds}s)': '仍在深度思考，正在处理复杂步骤…（{seconds}秒）',
            'Still executing {tool}… ({seconds}s)': '仍在执行 {tool}…（{seconds}秒）',
            'Tool {tool} is still running, waiting for results… ({seconds}s)': '工具 {tool} 仍在运行中，等待返回结果…（{seconds}秒）',
            'Still working on your request… ({seconds}s)': '仍在处理您的请求…（{seconds}秒）',
            'Download {filename}': '下载 {filename}',
        }
        for language in ('en', 'zh-CN'):
            self.client.set_cookie('language', language)
            body = self.client.get('/preview/pi_agent').get_data(as_text=True)
            catalog = json.loads(body.split('<script id="i18n-data" type="application/json">')[1].split('</script>')[0])['messages']
            for key, chinese in expected.items():
                self.assertEqual(catalog.get(key, key), chinese if language == 'zh-CN' else key)

    def test_coding_agent_session_tip_is_translated(self):
        for language, tip in [
                ('en', 'Send a message below to start working with Pi.'),
                ('zh-CN', '在下方发送消息，即可开始与 Pi 协作。')]:
            self.client.set_cookie('language', language)
            body = self.client.get('/preview/coding_agent').get_data(as_text=True)
            self.assertIn(tip, body)
            self.assertIn('id="coding-session-tip"', body)
            self.assertIn('role="status" hidden', body)
            self.assertIn('#coding-session-tip[hidden]{display:none}', body)

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
            mobile_menu = body.split('<nav class="pi-mobile-navigation')[1].split('</nav>')[0]
            self.assertIn('<a href="/test/coding_agent.page">' + ('Coding Agent' if language == 'en' else '编程代理') + '</a>', mobile_menu)
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
        for path in [*root.joinpath('templates').glob('*.html'), root / 'static/js/pi-agent.js', root / 'static/js/coding-agent.js', root / 'static/js/moments.js']:
            for match in pattern.finditer(path.read_text()):
                key = ast.literal_eval(match[1])
                self.assertIn(key, ZH_CN, f'Missing Chinese translation: {path.name}: {key}')
        for key, value in ZH_CN.items():
            self.assertEqual(set(re.findall(r'\{(\w+)\}', key)), set(re.findall(r'\{(\w+)\}', value)), key)

    def test_moments_render_for_line_measurement_and_escape_content(self):
        from datetime import datetime
        from types import SimpleNamespace

        self.app.add_url_rule('/test/delete/<id>', 'moments.delete_moment', lambda id: '')

        @self.app.get('/moment-preview/<int:count>')
        def moment_preview(count):
            return render_template('moments.html',
                moments=[SimpleNamespace(id='test', create_time=datetime(2026, 1, 1),
                    moment=('word\n' * (count - 1)) + '<script>alert(1)</script>')],
                pagination=SimpleNamespace(pages=1))

        for language, label in [('en', 'Show more'), ('zh-CN', '展开全文')]:
            self.client.set_cookie('language', language)
            for count in (1, 5, 6, 201):
                with self.subTest(language=language, count=count):
                    body = self.client.get(f'/moment-preview/{count}').get_data(as_text=True)
                    self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', body)
                    self.assertNotIn('<script>alert(1)</script>', body)
                    self.assertIn('white-space: pre-wrap;', body)
                    self.assertIn('word-break: break-word;', body)
                    self.assertIn('js/moments.js', body)
                    self.assertIn('class="moment-markdown"', body)
                    self.assertLess(body.index('js/marked.min.js'), body.index('js/moments.js'))
                    self.assertLess(body.index('js/purify.min.js'), body.index('js/moments.js'))
                    self.assertIn('data-moment-toggle', body)
                    self.assertIn('aria-expanded="false"', body)
                    self.assertIn('aria-controls="moment-content-test" hidden', body)
                    self.assertIn('>' + label + '</button>', body)
                    self.assertNotIn('data-moment-preview', body)
                    self.assertNotIn('pre-wrap;" hidden', body)
                    self.assertEqual(body.count('word\n'), count - 1)
                    date_markup = '<time datetime="2026-01-01T00:00:00">2026-01-01 00:00</time>'
                    self.assertIn(date_markup, body)
                    self.assertIn('<div class="d-flex flex-wrap align-items-center justify-content-end gap-2 mt-2">', body)
                    self.assertLess(body.index('data-moment-content'), body.index(date_markup))
                    self.assertLess(body.index(date_markup), body.index('data-bs-target="#deleteModaltest"'))
                    self.assertIn('Posted on' if language == 'en' else '发布于', body)

    def test_moment_markdown_help_is_translated(self):
        for language, help_text in [('en', 'Markdown is supported.'), ('zh-CN', '支持 Markdown 格式。')]:
            self.client.set_cookie('language', language)
            body = self.client.get('/preview/add_moment').get_data(as_text=True)
            self.assertIn(help_text, body)
            self.assertIn('aria-describedby="moment-format-help"', body)

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
