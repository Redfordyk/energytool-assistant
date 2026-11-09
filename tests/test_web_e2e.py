"""Browser smoke test; uses an isolated database and no external model."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest


@pytest.mark.skipif(os.getenv('RUN_E2E') != '1', reason='Set RUN_E2E=1 and install Playwright browser')
def test_review_and_save_lead_in_browser(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(DATABASE_URL='sqlite:///' + (tmp_path / 'web.db').as_posix(),
               DATA_DIR=str(tmp_path / 'data'), KNOWLEDGE_DIR=str(tmp_path / 'knowledge'))
    for name in ('LLM_API_KEY', 'OPENAI_API_KEY', 'LLM_BASE_URL', 'BASE_URL', 'OPENAI_BASE_URL'):
        env.pop(name, None)
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=root, env=env, check=True, capture_output=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
                              cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            browser = playwright.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
            page = browser.new_page()
            page.goto(url)
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
            page.locator('#name').fill('Тестовый Клиент')
            page.locator('#phone').fill('8 999 123-45-67')
            page.locator('#company').fill('Тестовая компания')
            page.get_by_role('button', name='Проверить заявку').click()
            page.locator('#preview').wait_for(state='visible')
            assert '+79991234567' in page.locator('#preview-text').inner_text()
            assert 'Тестовая компания' in page.locator('#preview-text').inner_text()
            page.locator('#consent').check()
            page.get_by_role('button', name='Сохранить заявку').click()
            page.get_by_role('status').get_by_text('Заявка сохранена.').wait_for()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
