const token = document.querySelector('#token');
const status = document.querySelector('#status');
const versions = document.querySelector('#versions');
const leadQueue = document.querySelector('#lead-queue');
const leadsStatus = document.querySelector('#leads-status');
const moreLeadsButton = document.querySelector('#load-more-leads');
const leadPageSize = 25;
let leadCursor = 0;

async function call(path, method = 'GET') {
  const response = await fetch('/api/admin/knowledge' + path, {
    method,
    headers: {Authorization: 'Bearer ' + token.value},
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || 'Ошибка запроса');
  }
  return response.json();
}

async function refresh() {
  const rows = await call('');
  versions.replaceChildren();
  if (!rows.length) versions.textContent = 'Версий пока нет.';
  for (const row of rows) {
    const item = document.createElement('article');
    const title = document.createElement('p');
    const hash = document.createElement('code');
    hash.textContent = row.sha256;
    title.append(hash, document.createTextNode(' — ' + row.status));
    item.append(title);
    for (const action of ['approve', 'activate', 'revoke']) {
      const button = document.createElement('button');
      button.textContent = {approve: 'Утвердить', activate: 'Активировать', revoke: 'Отозвать'}[action];
      button.onclick = async () => {
        button.disabled = true;
        try {
          await call('/' + row.sha256 + '/' + action, 'POST');
          status.textContent = 'Действие выполнено';
          await refresh();
        } catch (error) {
          status.textContent = error.message;
        } finally {
          button.disabled = false;
        }
      };
      item.append(button);
    }
    versions.append(item);
  }
}

function addLeadField(container, label, value) {
  const field = document.createElement('p');
  const name = document.createElement('strong');
  name.textContent = label + ': ';
  field.append(name, document.createTextNode(value || '—'));
  container.append(field);
}

function renderLead(lead) {
  const item = document.createElement('article');
  item.className = 'lead-inbox-item';
  const heading = document.createElement('h3');
  heading.textContent = 'Заявка №' + lead.id;
  const confirmation = document.createElement('p');
  confirmation.className = 'lead-inbox-date';
  const date = new Date(lead.confirmed_at);
  confirmation.textContent = Number.isNaN(date.getTime())
    ? 'Подтверждена: ' + lead.confirmed_at
    : 'Подтверждена: ' + date.toLocaleString('ru-RU');
  item.append(heading, confirmation);
  addLeadField(item, 'ФИО', lead.name);
  addLeadField(item, 'Телефон', lead.phone);
  addLeadField(item, 'Компания', lead.company);
  addLeadField(item, 'Контекст', lead.summary);
  addLeadField(item, 'Ревизия БЗ', lead.knowledge_revision || 'не указана');
  leadQueue.append(item);
}

async function loadLeads(reset = false) {
  const credential = token.value;
  if (!credential) {
    leadsStatus.textContent = 'Введите токен администратора.';
    return;
  }
  if (reset) {
    leadCursor = 0;
    leadQueue.replaceChildren();
    moreLeadsButton.hidden = true;
  }
  leadsStatus.textContent = 'Загружаю заявки…';
  const params = new URLSearchParams({limit: String(leadPageSize)});
  if (leadCursor) params.set('before_id', String(leadCursor));
  try {
    const response = await fetch('/api/admin/leads?' + params, {
      headers: {Authorization: 'Bearer ' + credential},
    });
    const data = await response.json().catch(() => ({}));
    if (token.value !== credential) return;
    if (!response.ok) throw new Error(data.detail || 'Не удалось загрузить заявки');
    if (reset && !data.length) {
      leadsStatus.textContent = 'Подтверждённых заявок пока нет.';
      return;
    }
    for (const lead of data) renderLead(lead);
    if (data.length) leadCursor = data[data.length - 1].id;
    moreLeadsButton.hidden = data.length < leadPageSize;
    leadsStatus.textContent = `Загружено: ${data.length}.`;
  } catch (error) {
    if (token.value === credential) leadsStatus.textContent = error.message;
  }
}

document.querySelector('#load').onclick = () => refresh().catch(error => status.textContent = error.message);
document.querySelector('#register').onclick = async () => {
  try {
    await call('/register', 'POST');
    status.textContent = 'Архив зарегистрирован';
    await refresh();
  } catch (error) {
    status.textContent = error.message;
  }
};
document.querySelector('#load-leads').onclick = () => loadLeads(true);
moreLeadsButton.onclick = () => loadLeads();
token.addEventListener('input', () => {
  leadQueue.replaceChildren();
  leadsStatus.textContent = '';
  leadCursor = 0;
  moreLeadsButton.hidden = true;
});

const traceInput = document.querySelector('#trace-id');
const traceOutput = document.querySelector('#trace-output');
document.querySelector('#load-trace').onclick = async () => {
  traceOutput.textContent = '';
  try {
    if (!traceInput.value.trim()) throw new Error('Введите идентификатор трассы');
    const response = await fetch('/api/admin/traces/' + encodeURIComponent(traceInput.value.trim()), {
      headers: {Authorization: 'Bearer ' + token.value},
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Не удалось загрузить трассу');
    traceOutput.textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    traceOutput.textContent = error.message;
  }
};
