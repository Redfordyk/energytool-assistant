"""Browser smoke test; uses an isolated database and no external model."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest


@pytest.mark.skipif(
    os.getenv('RUN_E2E') != '1', reason='Set RUN_E2E=1 and install Playwright browser'
)
def test_review_and_save_lead_in_browser(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(
        DATABASE_URL='sqlite:///' + (tmp_path / 'web.db').as_posix(),
        DATA_DIR=str(tmp_path / 'data'),
        KNOWLEDGE_DIR=str(tmp_path / 'knowledge'),
        ADMIN_TOKEN='e2e-admin-secret',
        REVIEWER_TOKEN='e2e-reviewer-secret',
    )
    for name in ('LLM_API_KEY', 'OPENAI_API_KEY', 'LLM_BASE_URL', 'BASE_URL', 'OPENAI_BASE_URL'):
        env.pop(name, None)
    subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
    )
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen(
        [
            sys.executable,
            '-m',
            'uvicorn',
            'app.main:app',
            '--host',
            '127.0.0.1',
            '--port',
            str(port),
        ],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            if server.poll() is not None:
                raise RuntimeError('Web server terminated during startup')
            try:
                with urlopen(url, timeout=1):
                    break
            except (URLError, TimeoutError):
                time.sleep(0.1)
        else:
            raise RuntimeError('Web server did not start')
        with sync_playwright() as playwright:
            executable = os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
            browser = playwright.chromium.launch(
                headless=True, **({'executable_path': executable} if executable else {})
            )
            page = browser.new_page()
            page.goto(url)
            expect(page.locator('link[href="/assets/tokens.css"]')).to_have_count(1)
            assert (
                page.locator('body').evaluate(
                    '(element) => getComputedStyle(element).getPropertyValue("--color-brand").trim()'
                )
                == '#e30611'
            )
            expect(
                page.get_by_role('heading', name='Интеллектуальная система энергоконсалтинга')
            ).to_be_visible()
            expect(page.get_by_role('heading', name='Заявка специалисту')).to_be_visible()
            expect(page.locator('.topic-chip')).to_have_count(3)
            assert page.locator('.welcome-message .message-bubble').evaluate(
                '(element) => element.getBoundingClientRect().height < 170'
            )
            page.set_viewport_size({'width': 375, 'height': 812})
            assert page.locator('body').evaluate(
                '(element) => element.scrollWidth <= window.innerWidth'
            )
            page.set_viewport_size({'width': 1280, 'height': 900})

            xss_session = page.request.post(url + '/api/sessions').json()
            xss_page = browser.new_page()
            xss_page.add_init_script(
                'sessionStorage.setItem("session", JSON.stringify('
                + json.dumps(xss_session)
                + '));'
            )
            malicious_answer = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>'

            def inject_malicious_history(route):
                if route.request.method == 'GET' and '/messages?' in route.request.url:
                    route.fulfill(
                        status=200,
                        content_type='application/json',
                        body=json.dumps(
                            [{'id': 1, 'role': 'assistant', 'content': malicious_answer}]
                        ),
                    )
                else:
                    route.continue_()

            xss_page.route('**/api/sessions/*/messages*', inject_malicious_history)
            xss_page.goto(url)
            expect(xss_page.locator('.message-assistant p')).to_contain_text(malicious_answer)
            expect(xss_page.locator('#messages img, #messages script')).to_have_count(0)
            assert xss_page.evaluate('window.__xss || null') is None
            xss_page.close()

            def lose_first_response(route):
                if route.request.method == 'POST':
                    assert route.fetch().status == 200
                    route.abort('failed')
                else:
                    route.continue_()

            pattern = '**/api/sessions/*/messages'
            page.route(pattern, lose_first_response)
            page.locator('#question').fill('Здравствуйте')
            page.get_by_role('button', name='Отправить').click()
            expect(page.locator('#status')).not_to_be_empty()
            page.unroute(pattern, lose_first_response)
            page.get_by_role('button', name='Отправить').click()
            expect(page.locator('#messages p')).to_have_count(2)
            expect(page.locator('#status')).to_contain_text('Трасса: ')
            trace_id = page.locator('#status').inner_text().split('Трасса: ', 1)[1]
            page.goto(url + '/admin')
            expect(page.get_by_role('heading', name='Рабочая панель')).to_be_visible()
            expect(page.locator('link[href="/assets/tokens.css"]')).to_have_count(1)
            assert (
                page.locator('.admin-card').first.evaluate(
                    '(element) => getComputedStyle(element).backgroundColor'
                )
                == 'rgb(255, 255, 255)'
            )
            page.set_viewport_size({'width': 375, 'height': 812})
            assert page.locator('body').evaluate(
                '(element) => element.scrollWidth <= window.innerWidth'
            )
            page.set_viewport_size({'width': 1280, 'height': 900})
            page.locator('#token').fill('e2e-reviewer-secret')
            page.locator('#trace-id').fill(trace_id)
            page.get_by_role('button', name='Открыть трассу').click()
            expect(page.locator('#trace-output')).to_contain_text('"intent": "greeting"')
            assert 'Здравствуйте' not in page.locator('#trace-output').inner_text()
            page.goto(url)
            page.locator('#name').fill('Тестовый Клиент')
            page.locator('#phone').fill('8 999 123-45-67')
            page.locator('#company').fill('Тестовая <img src=x onerror="window.__xss=1">')
            page.get_by_role('button', name='Проверить заявку').click()
            page.locator('#preview').wait_for(state='visible')
            assert '+79991234567' in page.locator('#preview-text').inner_text()
            assert (
                '<img src=x onerror="window.__xss=1">' in page.locator('#preview-text').inner_text()
            )
            page.locator('#consent').check()
            page.locator('#confirm').click()
            page.get_by_role('status').get_by_text('Заявка сохранена.').wait_for()
            page.goto(url + '/admin')
            page.locator('#token').fill('e2e-reviewer-secret')
            page.get_by_role('button', name='Загрузить заявки').click()
            expect(page.locator('#leads-status')).to_contain_text('Недостаточно прав')
            expect(page.locator('.lead-inbox-item')).to_have_count(0)
            page.locator('#token').fill('e2e-admin-secret')
            page.get_by_role('button', name='Загрузить заявки').click()
            expect(page.locator('.lead-inbox-item')).to_contain_text('Тестовый Клиент')
            expect(page.locator('.lead-inbox-item')).to_contain_text('+79991234567')
            expect(page.locator('#lead-queue img, #lead-queue script')).to_have_count(0)
            assert page.evaluate('window.__xss || null') is None
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
