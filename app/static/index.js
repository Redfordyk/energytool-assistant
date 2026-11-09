let session = JSON.parse(sessionStorage.getItem('session') || 'null');
let pending = JSON.parse(sessionStorage.getItem('pendingMessage') || 'null');
let requestKey = crypto.randomUUID();
let confirmation = null;
let busy = false;
let readyError = null;

const messages = document.querySelector('#messages');
const status = document.querySelector('#status');
const preview = document.querySelector('#preview');
const leadForm = document.querySelector('#lead');
const questionInput = document.querySelector('#question');

function clearPreview() {
  confirmation = null;
  preview.hidden = true;
  document.querySelector('#consent').checked = false;
}

function saveSession() {
  sessionStorage.setItem('session', JSON.stringify(session));
}

function savePending(value) {
  pending = value;
  if (value) sessionStorage.setItem('pendingMessage', JSON.stringify(value));
  else sessionStorage.removeItem('pendingMessage');
}

async function start(resetForm = true) {
  const response = await fetch('/api/sessions', {method: 'POST'});
  if (!response.ok) throw Error('Не удалось создать диалог');
  session = await response.json();
  saveSession();
  savePending(null);
  messages.replaceChildren();
  questionInput.value = '';
  requestKey = crypto.randomUUID();
  clearPreview();
  if (resetForm) leadForm.reset();
  readyError = null;
  status.textContent = '';
}

function add(role, text) {
  const p = document.createElement('p');
  p.className = role;
  p.textContent = (role === 'user' ? 'Вы: ' : 'Консультант: ') + text;
  messages.append(p);
}

async function get(path) {
  const response = await fetch('/api/sessions/' + session.id + path,
    {headers: {Authorization: 'Bearer ' + session.token}});
  if (!response.ok) throw Error('Не удалось загрузить диалог');
  return response.json();
}

async function hydrate() {
  const state = await get('');
  const rows = [];
  let after = 0;
  while (true) {
    const page = await get('/messages?limit=100&after_id=' + after);
    rows.push(...page);
    if (page.length < 100) break;
    after = page[page.length - 1].id;
  }
  session.version = state.version;
  session.closed = state.closed;
  saveSession();
  messages.replaceChildren();
  rows.forEach(row => add(row.role, row.content));
  if (state.closed) status.textContent = 'Диалог закрыт. Начните новый.';
}

async function api(path, body, key = null) {
  const headers = {'Content-Type': 'application/json', Authorization: 'Bearer ' + session.token};
  if (key) headers['Idempotency-Key'] = key;
  const response = await fetch('/api/sessions/' + session.id + path,
    {method: 'POST', headers, body: JSON.stringify(body)});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = Error(typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить запрос.');
    error.status = response.status;
    throw error;
  }
  return response.json();
}

let ready = session ? hydrate().catch(error => { readyError = error; status.textContent = error.message; }) : Promise.resolve();
if (pending && session && pending.sessionId === session.id) questionInput.value = pending.text;
else if (pending) savePending(null);

async function run(action, allowUnready = false) {
  if (busy) return;
  busy = true;
  try { await ready; if (readyError && !allowUnready) throw readyError; await action(); }
  catch (error) { status.textContent = error.message || 'Произошла ошибка'; }
  finally { busy = false; }
}

document.querySelector('#new').onclick = () => run(() => start(), true);
document.querySelector('#chat').onsubmit = event => {
  event.preventDefault();
  run(async () => {
    const text = questionInput.value.trim();
    if (!text) return;
    if (!session) { await start(false); questionInput.value = text; }
    if (session.closed) throw Error('Диалог закрыт. Начните новый.');
    if (pending && pending.text !== text) throw Error('Сначала повторите отправку предыдущего сообщения или начните новый диалог.');
    if (!pending) savePending({sessionId: session.id, text, key: crypto.randomUUID(), version: session.version});
    let result;
    try {
      result = await api('/messages', {text: pending.text, session_version: pending.version}, pending.key);
    } catch (error) {
      if (error.status === 409) {
        savePending(null);
        await hydrate();
      }
      throw error;
    }
    savePending(null);
    session.version = result.version;
    saveSession();
    questionInput.value = '';
    status.textContent = '';
    clearPreview();
    try { await hydrate(); status.textContent = result.trace_id ? 'Ответ готов. Трасса: ' + result.trace_id : ''; }
    catch (_) { add('user', text); add('assistant', result.answer); status.textContent = result.trace_id ? 'Историю не удалось обновить. Трасса: ' + result.trace_id : 'Ответ получен; историю не удалось обновить.'; }
  });
};

leadForm.addEventListener('input', clearPreview);
leadForm.onsubmit = event => {
  event.preventDefault();
  run(async () => {
    const fields = {name: document.querySelector('#name').value, phone: document.querySelector('#phone').value,
      company: document.querySelector('#company').value};
    if (!session) await start(false);
    const draft = await api('/lead-drafts', fields, requestKey);
    confirmation = draft.confirmation_token;
    document.querySelector('#preview-text').textContent = 'ФИО: ' + draft.name + '\nТелефон: ' + draft.phone +
      '\nКомпания: ' + draft.company + '\nКонтекст задачи:\n' + draft.summary;
    preview.hidden = false;
    status.textContent = '';
  });
};

document.querySelector('#cancel').onclick = () => run(async () => {
  const response = await fetch('/api/sessions/' + session.id + '/lead-drafts',
    {method: 'DELETE', headers: {Authorization: 'Bearer ' + session.token, 'Idempotency-Key': requestKey}});
  if (!response.ok) throw Error('Не удалось отменить заявку');
  clearPreview();
  status.textContent = 'Заявка отменена.';
});

document.querySelector('#confirm').onclick = () => run(async () => {
  if (!confirmation || !document.querySelector('#consent').checked) throw Error('Подтвердите сохранение показанных данных');
  await api('/leads', {confirmation_token: confirmation, consent: true}, requestKey);
  status.textContent = 'Заявка сохранена.';
  clearPreview();
  leadForm.reset();
  requestKey = crypto.randomUUID();
});
