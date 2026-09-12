/* Screen composition uses shared primitives in ui.js and existing backend actions. */
const {sectionHeader,searchField,chip}=NoemaUI;
let taskFilter='all',noteFilter='all',peopleFilter='all';
const priorityLabels={high:'Высокий',urgent:'Высокий',normal:'Обычный',medium:'Средний',low:'Низкий'};
const priorityLabel=value=>priorityLabels[String(value||'normal').toLowerCase()]||String(value||'Обычный');
const itemDate=value=>value?new Date(value).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:value.includes?.('T')?'2-digit':undefined,minute:value.includes?.('T')?'2-digit':undefined,timeZone:data.settings.timezone}):'Без даты';
const noteCategory=note=>String(note.category||'').trim();
const peopleCategory=person=>String(person.relationship||'').trim();

home=function(){
 const d=new Date(data.day+'T12:00:00'),pending=data.tasks.filter(t=>t.status==='open');
 const next=data.reminders.filter(r=>!r.acknowledged).sort((a,b)=>a.remind_at_utc.localeCompare(b.remind_at_utc));
 const current=weatherData?.current;
 const weather=current?`${icon(/ясно/i.test(current.condition)?'sun':'cloud')}<div><small>${esc(weatherData.city)}</small><p class="temperature">${Math.round(current.temperature)}°</p><p>${esc(current.condition)}</p><small>Ощущается как ${Math.round(current.feels_like)}°</small></div>`:`${icon('cloud')}<div><p>${preview?'Погода появится в Telegram':'Погода недоступна'}</p><button class="text-action" data-form="briefing">Выбрать город</button></div>`;
 const list=(items,name='notes')=>items.map(x=>`<div class="widget-line">${icon(name)}<span>${esc(x)}</span></div>`).join('');
 const snippets=pending.slice(0,3).map(t=>`<div class="widget-line">${icon('tasks')}<span>${esc(t.text)}<small>${date(t.due_date)}</small></span></div>`).join('')+(pending.length>3?`<small class="widget-more">Ещё ${pending.length-3}</small>`:'');
 const spend=monthBudget?.currency_totals?.RUB?.expense;
 const bodies={
  tasks:[snippets||empty('Нет предстоящих задач'),'tasks','tasks'],
  next_event:[next[0]?`<p class="widget-time">${new Date(next[0].remind_at_utc).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',timeZone:data.settings.timezone})}</p><p>${esc(next[0].text)}</p><small>${date(next[0].remind_at_utc)}</small>`:empty('Нет запланированных напоминаний'),'reminders','calendar'],
  notes:[list(data.notes.slice(0,3).map(n=>n.title||n.text.slice(0,65)))||empty('Нет заметок'),'notes','notes'],
  reminders:[list(next.slice(0,2).map(r=>r.text),'bell')||empty('Нет активных напоминаний'),'reminders','bell'],
  budget:[`<small>Расходы за текущий месяц · RUB</small><p class="amount">${spend===undefined?'—':money(spend)}</p><small>${spend===undefined?'Нет загруженных данных':'По учтённым операциям'}</small>`,'budget','wallet'],
  recent_saved:[list(data.files.slice(0,2).map(f=>f.original_name),'file')||empty('Нет сохранённых файлов'),'archive','file'],
  people:[list(data.people.slice(0,3).map(p=>p.name),'people')||empty('Нет сохранённых контактов'),'people','people']
 };
 const widgets=(data.settings.home_widgets||defaultWidgets).filter(type=>bodies[type]);
 return `<section class="card hero"><div class="hero-calendar"><h3>${icon('calendar')} Сегодня</h3><div class="hero-date"><span class="date-number">${d.getDate()}</span><p>${d.toLocaleDateString('ru-RU',{month:'long'})}<br>${d.toLocaleDateString('ru-RU',{weekday:'long'})}</p></div></div><div class="weather">${weather}</div><canvas class="membrane" width="420" height="420" aria-hidden="true"></canvas></section><div class="grid home-grid">${widgets.map(type=>`<div data-widget="${type}">${card(widgetNames[type],`<div class="widget-content">${bodies[type][0]}</div>`,bodies[type][1],bodies[type][2])}</div>`).join('')}</div>`;
};
const originalSettings=settings;
settings=function(){const html=originalSettings().replace('<h1>Настройки</h1><p class="subtle">Фокус, ясность и контроль.</p>','').replace('<p class="subtle">Подключение iPhone и управление AI доступны в настройках бота.</p>','').replace(/<button data-delete="delete_behavior_rule" data-id="(\d+)" aria-label="Удалить правило">×<\/button>/g,'<button class="icon" data-edit="rule" data-id="$1" aria-label="Изменить правило">'+icon('settings')+'</button><button class="icon" data-delete="delete_behavior_rule" data-id="$1" aria-label="Удалить правило">'+icon('trash')+'</button>');return `<div class="settings-stack">${html}</div>`};
tasks=function(){
 const base=data.tasks.filter(t=>taskTab==='done'?t.status!=='open':t.status==='open'&&(taskTab==='upcoming'?(t.due_date&&t.due_date.slice(0,10)>data.day):(!t.due_date||t.due_date.slice(0,10)<=data.day)));
 const priorities=[...new Set(base.map(t=>String(t.priority||'normal')).filter(Boolean))];
 const items=taskFilter==='all'?base:base.filter(t=>String(t.priority||'normal')===taskFilter);
 const filters=[chip('all','Все',taskFilter==='all','task-filter'),...priorities.map(value=>chip(value,priorityLabel(value),taskFilter===value,'task-filter'))].join('');
 return `${segmented([['today','Сегодня'],['upcoming','Предстоящие'],['done','Завершённые']],taskTab,'task-tab')}<div class="screen-toolbar"><div class="chip-row">${filters}</div><button class="secondary add-action" data-form="task">${icon('plus')}<span>Задача</span></button></div>${sectionHeader(taskTab==='done'?'Завершённые':taskTab==='upcoming'?'Предстоящие':'Сегодня',`${items.length} ${items.length===1?'задача':'задач'}`)}<div class="task-list">${items.map(t=>`<article class="card task-card"><button class="check ${t.status!=='open'?'done':''}" data-toggle="${t.id}" aria-label="Изменить статус задачи"></button><details class="body"><summary class="task-summary"><div><h2>${esc(t.text)}</h2><small><i></i>${esc(priorityLabel(t.priority))}</small></div><div class="task-meta"><span>△ ${esc(priorityLabel(t.priority))}</span><small>${icon('clock')}${itemDate(t.due_date)}</small></div>${icon('chevron')}</summary><div class="details"><div class="detail-grid"><p><small>Срок</small>${itemDate(t.due_date)}</p><p><small>Приоритет</small>${esc(priorityLabel(t.priority))}</p></div><div class="toolbar compact-actions">${iconButton('settings','Изменить задачу',`data-edit="task" data-id="${t.id}"`)}${iconButton('trash','Удалить задачу',`data-delete="delete_task" data-id="${t.id}"`)}</div></div></details></article>`).join('')||empty('В этом разделе нет задач')}</div>`;
};
reminders=function(){return `<div class="screen-toolbar"><div></div><button class="secondary add-action" data-form="reminder">${icon('plus')}<span>Напоминание</span></button></div>${sectionHeader('Активные',`${data.reminders.filter(r=>!r.acknowledged).length}`)}<div class="reminder-list">${data.reminders.map(r=>`<article class="card row reminder-row"><button class="check ${r.acknowledged?'done':''}" data-reminder="${r.id}" ${r.acknowledged?'disabled':''} aria-label="Выполнить напоминание"></button><div class="body"><p>${esc(r.text)}</p><small>${icon('clock')}${new Date(r.remind_at_utc).toLocaleString('ru-RU',{timeZone:data.settings.timezone})}</small></div>${iconButton('settings','Изменить напоминание',`data-edit="reminder" data-id="${r.id}"`)}${iconButton('trash','Удалить напоминание',`data-delete="delete_reminder" data-id="${r.id}"`)}</article>`).join('')||empty('Нет активных напоминаний')}</div>`};
people=function(){
 const categories=[...new Set(data.people.map(peopleCategory).filter(Boolean))];
 const found=data.people.filter(p=>`${p.name} ${p.notes} ${p.relationship} ${p.projects} ${p.home_city} ${p.current_location}`.toLowerCase().includes(search.toLowerCase())&&(peopleFilter==='all'||peopleCategory(p)===peopleFilter));
 return `${searchField('Поиск по людям…',search,'Поиск по людям')}<div class="screen-toolbar"><div class="chip-row">${chip('all','Все',peopleFilter==='all','people-filter')}${categories.map(value=>chip(value,value,peopleFilter===value,'people-filter')).join('')}</div><button class="secondary add-action" data-form="person">${icon('plus')}<span>Человек</span></button></div><div class="people-list">${found.map((p,index)=>{const facts=[p.relationship,p.home_city&&`из ${p.home_city}`,p.current_location&&`сейчас ${p.current_location}`,p.age&&`${p.age} лет`].filter(Boolean),projects=String(p.projects||'').split(/[,;]\s*/).filter(Boolean);return `<details class="card person-profile" ${index===0?'open':''}><summary><span class="avatar">${esc(p.name?.[0]||'•')}</span><div class="body"><h2>${esc(p.name)}</h2><small>${esc(facts.join(' · ')||'Контакт')}</small></div>${p.birthday?`<small class="person-birthday">${icon('calendar')}${esc(p.birthday)}</small>`:''}${icon('chevron')}</summary><div class="details"><div class="person-overview"><div><h3>Что важно</h3><p>${esc(p.notes||'Пока нет заметок')}</p></div>${p.birthday?`<p><small>День рождения</small>${esc(p.birthday)}</p>`:''}</div>${p.recent_interactions?.length?`<h3>Недавние упоминания</h3><div class="profile-interactions">${p.recent_interactions.map(i=>`<p class="profile-interaction">${esc(i.interaction)}<small>${esc(i.interaction_date)}${i.interaction_type?` · ${esc(i.interaction_type)}`:''}</small></p>`).join('')}</div>`:''}${projects.length?`<h3>Связано с</h3><div class="project-chips">${projects.map(project=>`<span>${icon('folder')}${esc(project)}</span>`).join('')}</div>`:''}<div class="toolbar compact-actions">${iconButton('settings','Изменить человека',`data-edit="person" data-id="${p.id}"`)}${iconButton('trash','Удалить человека',`data-delete="delete_person" data-id="${p.id}"`)}</div></div></details>`}).join('')||empty('Добавь человека — Noema будет хранить важный контекст.')}</div>`;
};

function noteCard(note,pinned=false){return `<article class="card note-card"><div class="card-head"><h2>${esc(note.title||'Без названия')}</h2>${pinned?icon('pin'):icon('notes')}</div><p>${esc(note.text)}</p><div class="note-meta"><span>${esc(noteCategory(note)||'Заметка')}</span><small>${date(note.created_at)}</small><span class="note-actions">${iconButton('settings','Изменить заметку',`data-edit="note" data-id="${note.id}"`)}${iconButton('trash','Удалить заметку',`data-delete="delete_note" data-id="${note.id}"`)}</span></div></article>`}
notes=function(){
 const categories=[...new Set(data.notes.map(noteCategory).filter(Boolean))];
 const matches=data.notes.filter(n=>`${n.title} ${n.text}`.toLowerCase().includes(search.toLowerCase())&&(noteFilter==='all'||noteCategory(n)===noteFilter));
 const pinned=matches.filter(n=>n.pinned||n.is_pinned),recent=matches.filter(n=>!(n.pinned||n.is_pinned));
 const controls=[chip('all','Все',noteFilter==='all','note-filter'),...categories.map(value=>chip(value,value,noteFilter===value,'note-filter'))].join('');
 return `${searchField('Поиск заметок…',search,'Поиск по заметкам')}<div class="screen-toolbar"><div class="chip-row">${controls}</div><button class="secondary add-action" data-form="note">${icon('plus')}<span>Заметка</span></button></div>${pinned.length?`${sectionHeader('Закреплённые',`${pinned.length}`)}<div class="pinned-notes">${pinned.map(n=>noteCard(n,true)).join('')}</div>`:''}${sectionHeader('Недавние',`${recent.length}`)}<div class="recent-notes">${recent.map(n=>noteCard(n)).join('')||empty('Здесь пока тихо. Добавь первую запись.')}</div><button class="secondary memory-search" data-memory-search>${icon('search')}<span>Поиск по всей памяти</span></button>${archiveResults?card('Результаты поиска',archiveResults.results.map(r=>`<details class="archive-entry"><summary>${esc(r.title||r.summary||'Запись')}</summary><p>${esc(r.content||r.summary||r.text)}</p></details>`).join('')||empty('Совпадений не найдено')):''}`;
};

function resourceKind(item){if(item.type==='notes')return 'ideas';const mime=String(item.mime_type||''),kind=String(item.kind||'');if(mime.startsWith('image/'))return 'screenshots';if(/url|link|web/i.test(kind)||/https?:\/\//i.test(item.summary||''))return 'links';return 'documents'}
const resourceLabels={links:'Ссылки',screenshots:'Скриншоты',documents:'Документы',ideas:'Идеи'};
const resourceIcons={links:'link',screenshots:'image',documents:'file',ideas:'notes'};
archive=function(){
 const sources=[...data.notes.map(n=>({...n,type:'notes',name:n.title||n.text,label:'Заметка'})),...data.files.map(f=>({...f,type:'files',name:f.original_name,text:f.summary,label:f.mime_type?.startsWith('image/')?'Изображение':'Файл'}))].map(item=>({...item,resourceKind:resourceKind(item)}));
 const kinds=[...new Set(sources.map(item=>item.resourceKind))];
 const items=sources.filter(item=>(filter==='all'||filter===item.resourceKind)&&`${item.name} ${item.text}`.toLowerCase().includes(search.toLowerCase()));
 return `${searchField('Найти в архиве…',search,'Поиск по архиву')}<div class="screen-toolbar"><div class="chip-row">${chip('all','Все',filter==='all')}${kinds.map(kind=>chip(kind,resourceLabels[kind],filter===kind)).join('')}</div><button class="secondary add-action" data-form="note">${icon('plus')}<span>Заметка</span></button></div><div class="resource-list">${items.map(item=>`<article class="card resource-card"><div class="resource-thumb ${item.resourceKind}">${icon(resourceIcons[item.resourceKind])}</div><div class="resource-body"><h2>${esc(item.name?.slice(0,120)||'Без названия')}</h2><p>${esc(item.text||'Сохранено в Noema')}</p><small>${esc(item.label)} · ${date(item.created_at)}</small></div>${item.type==='notes'?`<div class="resource-actions">${iconButton('settings','Изменить заметку',`data-edit="note" data-id="${item.id}"`)}${iconButton('trash','Удалить заметку',`data-delete="delete_note" data-id="${item.id}"`)}</div>`:''}</article>`).join('')||empty('Архив пока пуст.')}</div><button class="secondary memory-search" data-memory-search>${icon('search')}<span>Поиск по всей памяти</span></button>${archiveResults?card('Результаты поиска',archiveResults.results.map(r=>`<details class="archive-entry"><summary>${esc(r.title||r.summary||'Запись')}</summary><p>${esc(r.content||r.summary||r.text)}</p></details>`).join('')||empty('Совпадений не найдено')):''}`;
};
let budgetPeriod='month',budgetMonth='',budgetData=null,budgetLoading=false;
function budgetRange(){const selected=budgetMonth||data.day.slice(0,7),d=new Date(selected+'-01T12:00:00');let start=selected+'-01',end=new Date(d.getFullYear(),d.getMonth()+1,0,12).toLocaleDateString('en-CA');if(budgetPeriod==='day')start=end=data.day;if(budgetPeriod==='week'){const day=new Date(data.day+'T12:00:00');day.setDate(day.getDate()-6);start=day.toLocaleDateString('en-CA');end=data.day}if(budgetPeriod==='year'){start=selected.slice(0,4)+'-01-01';end=selected.slice(0,4)+'-12-31'}return {date_from:start,date_to:end}}
function distributionChart(entries,sum){let offset=0;const patterns=['dots','diag','cross','dash','solid'];const slices=entries.map(([name,value],i)=>{const length=sum?value/sum*100:0,slice=`<circle cx="60" cy="60" r="48" pathLength="100" fill="none" stroke="url(#budget-${patterns[i%patterns.length]})" stroke-width="10" stroke-dasharray="${Math.max(0,length-.6)} ${100-length+.6}" stroke-dashoffset="${-offset}"/>`;offset+=length;return slice}).join('');return `<svg viewBox="0 0 120 120" role="img" aria-label="Расходы по категориям"><defs><pattern id="budget-dots" width="4" height="4" patternUnits="userSpaceOnUse"><rect width="4" height="4" fill="#e4e4e2"/><circle cx="1" cy="1" r=".7" fill="#303033"/></pattern><pattern id="budget-diag" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="5" fill="#b8b8b6"/><path d="M0 1h5" stroke="#252527" stroke-width="1"/></pattern><pattern id="budget-cross" width="5" height="5" patternUnits="userSpaceOnUse"><rect width="5" height="5" fill="#888"/><path d="M0 0 5 5M5 0 0 5" stroke="#eee" stroke-width=".7"/></pattern><pattern id="budget-dash" width="5" height="5" patternUnits="userSpaceOnUse"><rect width="5" height="5" fill="#555"/><path d="M0 1h5M0 4h5" stroke="#eee" stroke-width="1"/></pattern><pattern id="budget-solid" width="4" height="4" patternUnits="userSpaceOnUse"><rect width="4" height="4" fill="#efefed"/></pattern></defs><circle cx="60" cy="60" r="48" fill="none" stroke="var(--border)" stroke-width="10"/>${slices}<text x="60" y="61" text-anchor="middle" fill="currentColor" font-size="8">${sum?esc(money(sum)):'Нет данных'}</text></svg>`}
budget=function(){
 const stats=budgetData?.currency_totals||{},items=budgetData?.items||[],currency=stats.RUB?'RUB':Object.keys(stats)[0]||'RUB',total=stats[currency];
 const categories={};items.filter(e=>e.kind!=='income'&&e.currency===currency).forEach(e=>categories[e.category||'Прочее']=(categories[e.category||'Прочее']||0)+Number(e.amount));
 const categoryEntries=Object.entries(categories).sort((a,b)=>b[1]-a[1]),categorySum=categoryEntries.reduce((sum,item)=>sum+item[1],0);
 const days={};items.filter(e=>e.kind!=='income'&&e.currency===currency).forEach(e=>{const key=e.spent_at.slice(0,10);days[key]=(days[key]||0)+Number(e.amount)});const values=Object.entries(days).sort(([a],[b])=>a.localeCompare(b)).slice(-8),max=Math.max(...values.map(([,value])=>value),1);
 const monthLabel=new Date((budgetMonth||data.day.slice(0,7))+'-01T12:00:00').toLocaleDateString('ru-RU',{month:'long',year:'numeric'});
 return `<div class="budget-controls"><label class="period-picker" for="budget-month">${icon('calendar')}<span>${esc(monthLabel)}</span><input type="month" id="budget-month" value="${budgetMonth||data.day.slice(0,7)}"></label>${segmented([['day','День'],['week','Неделя'],['month','Месяц'],['year','Год']],budgetPeriod,'budget-period')}</div><div class="screen-toolbar budget-actions"><div></div><div><button class="secondary add-action" data-form="expense">${icon('plus')}<span>Расход</span></button><button class="secondary add-action" data-form="income">${icon('plus')}<span>Доход</span></button></div></div><div class="budget-totals"><section class="card"><h3>Расходы</h3>${budgetLoading?'<div class="skeleton"></div>':`<p class="amount">${total?new Intl.NumberFormat('ru-RU',{style:'currency',currency,maximumFractionDigits:0}).format(total.expense):'—'}</p><small>За выбранный период · ${esc(currency)}</small>`}<span class="orb-fragment"></span></section><section class="card"><h3>Доходы</h3>${budgetLoading?'<div class="skeleton"></div>':`<p class="amount">${total?new Intl.NumberFormat('ru-RU',{style:'currency',currency,maximumFractionDigits:0}).format(total.income):'—'}</p><small>За выбранный период · ${esc(currency)}</small>`}<span class="orb-fragment"></span></section></div><div class="budget-dashboard"><section class="card distribution"><h3>Распределение расходов</h3><div class="distribution-body">${distributionChart(categoryEntries,categorySum)}<div>${categoryEntries.map(([name,value],i)=>`<p><i class="pattern-chip ${['dots','diag','cross','dash','solid'][i%5]}"></i>${esc(name)} <span>${Math.round(value/categorySum*100)}%</span><small>${money(value)}</small></p>`).join('')||'<p class="subtle">После первого расхода здесь появятся категории.</p>'}</div></div></section><section class="card trend"><h3>Расходы по дням · ${esc(currency)}</h3>${values.length?`<div class="bars" role="img" aria-label="Расходы по дням">${values.map(([day,value])=>`<div class="bar-group" title="${esc(day)}: ${money(value)}"><i style="height:${Math.max(8,value/max*100)}%"></i><small>${day.slice(8)}</small></div>`).join('')}</div><small>Динамика выбранного периода</small>`:empty('Данных для графика пока нет.')}</section></div>${Object.keys(stats).length?`<section class="card finance-strip"><div><h3>Валюта</h3><div class="currency-list">${Object.keys(stats).map(code=>`<span>${icon('globe')}${esc(code)}</span>`).join('')}</div></div><div><h3>Баланс периода</h3><p class="amount">${total?new Intl.NumberFormat('ru-RU',{style:'currency',currency,maximumFractionDigits:0}).format(total.balance):'—'}</p></div></section>`:''}<section class="card operations"><div class="card-head"><h3>Последние операции</h3><span>${items.length}</span></div>${items.slice(0,100).map(e=>`<article class="operation-row"><span class="operation-icon">${icon(e.kind==='income'?'wallet':'card')}</span><div class="body"><p>${esc(e.description||'Операция')}</p><small>${esc(e.category||'Без категории')}</small></div><span class="mono">${e.kind==='income'?'+':'−'}${Number(e.amount).toLocaleString('ru-RU')} ${esc(e.currency)}</span><small>${date(e.spent_at)}</small>${iconButton('settings','Изменить операцию',`data-edit="expense" data-id="${e.id}"`)}${iconButton('trash','Удалить операцию',`data-delete="delete_expense" data-id="${e.id}"`)}</article>`).join('')||empty('Операций за период нет.')}</section>${items.length>100?'<p class="subtle">Показаны первые 100 операций. Сузь период для просмотра остальных.</p>':''}`;
};
function renderDock(){
 const dock=document.querySelector('#dock');dock.hidden=page==='chat'||page==='settings';document.querySelector('#shell').classList.toggle('no-dock',dock.hidden);
 if(dock.hidden)return;
 const task=data.tasks.find(t=>t.status==='open'&&t.due_date&&t.due_date.slice(0,10)<data.day)||data.tasks.find(t=>t.status==='open');
 const reminder=data.reminders.filter(r=>!r.acknowledged).sort((a,b)=>a.remind_at_utc.localeCompare(b.remind_at_utc))[0];
 const focus=task?.text||reminder?.text||'Планы на сегодня';
 if(!dock.querySelector('canvas'))dock.innerHTML=`<button class="focus-pill"><small>Фокус дня</small><span></span></button><button class="orb-button" data-voice aria-label="Нажми для голосового запроса"><canvas class="membrane" width="260" height="260" aria-hidden="true"></canvas></button>${iconButton('keyboard','Открыть текстовый чат','data-keyboard')}`;
 dock.querySelector('.focus-pill').dataset.page=task?'tasks':reminder?'reminders':'tasks';
 dock.querySelector('.focus-pill span').textContent=focus;
 dock.querySelector('[data-keyboard]').classList.add('keyboard-button');
}
const persistentMembranes=new Map();
render=function(){
 for(const name of ['hero','voice-area','chat-voice-orb']){const canvas=content.querySelector(`.${name} canvas.membrane`);if(canvas)persistentMembranes.set(name,canvas)}
 const screens={home,archive,notes,people,tasks,reminders,budget,settings,chat};
 content.innerHTML=(screens[page]||home)();
 const title={home:'Noema',archive:'Архив',notes:'Заметки',people:'Люди',tasks:'Задачи',reminders:'Календарь',budget:'Бюджет',settings:'Настройки',chat:'Чат'}[page];
 content.querySelector('h1')?.remove();
 const utilities=page==='settings'?'':`${page!=='chat'?iconButton('bell','Напоминания','data-page="reminders"'):''}${iconButton('settings','Настройки','data-page="settings"')}`;
 document.querySelector('#header').innerHTML=`<div class="header-title"><h1>${title}</h1></div><div class="utilities">${utilities}</div>`;
 document.querySelector('#shell').dataset.page=page;
 content.querySelector('.all-sections')?.remove();
 document.querySelector('#shell').classList.toggle('chat-screen',page==='chat');
 document.querySelector('#shell').classList.toggle('budget-screen',page==='budget');
 document.querySelector('#shell').classList.toggle('settings',page==='settings');
 document.querySelector('#shell').classList.toggle('subpage',page!=='home');
 content.querySelectorAll('.delete').forEach(b=>b.innerHTML=icon('trash'));
 if(page==='chat'){
  content.querySelector('.subtle')?.remove();
 }
 for(const name of ['hero','voice-area','chat-voice-orb']){const canvas=content.querySelector(`.${name} canvas.membrane`),saved=persistentMembranes.get(name);if(canvas&&saved)canvas.replaceWith(saved)}
 renderDock();window.NoemaVoiceController?.sync?.();if(page==='chat')requestAnimationFrame(()=>{const thread=content.querySelector('.chat');if(thread&&thread.scrollHeight-thread.scrollTop-thread.clientHeight<72)thread.scrollTop=thread.scrollHeight});if(tg){page==='home'?tg.BackButton.hide():tg.BackButton.show()}
};
go=function(next){if(next!==page)navHistory.push(page);page=next;search='';filter='all';taskFilter='all';noteFilter='all';peopleFilter='all';archiveResults=null;say('');render();window.scrollTo({top:0});if(next==='budget')loadBudget()};
function back(){if(document.querySelector('#layout-editor').open){document.querySelector('#layout-editor').close();return}if(document.querySelector('#editor').open){document.querySelector('#editor').close();return}page=navHistory.pop()||'home';search='';filter='all';taskFilter='all';noteFilter='all';peopleFilter='all';render()}
function openLayout(){layoutDraft=[...(data.settings.home_widgets||defaultWidgets)];drawLayout();document.querySelector('#layout-editor').showModal()}
function drawLayout(){document.querySelector('#layout-items').innerHTML=layoutDraft.map((type,i)=>`<div class="layout-row" draggable="true" data-index="${i}"><select aria-label="Виджет ${i+1}" data-replace="${i}">${Object.entries(widgetNames).filter(([k])=>k===type||!layoutDraft.includes(k)).map(([k,v])=>`<option value="${k}" ${k===type?'selected':''}>${v}</option>`).join('')}</select><button data-move="${i}" data-step="-1" aria-label="Выше" ${i===0?'disabled':''}>↑</button><button data-move="${i}" data-step="1" aria-label="Ниже" ${i===layoutDraft.length-1?'disabled':''}>↓</button><button data-hide="${i}" aria-label="Скрыть ${widgetNames[type]}">×</button></div>`).join('')+`<div class="toolbar">${Object.entries(widgetNames).filter(([k])=>!layoutDraft.includes(k)).map(([k,v])=>`<button class="secondary" data-add-widget="${k}">+ ${v}</button>`).join('')}</div>`}
let held=false,dragIndex=null;
document.addEventListener('click',e=>{if(held&&e.target.closest('.grid')){held=false;e.stopImmediatePropagation();e.preventDefault()}},true);
document.addEventListener('dragstart',e=>{const row=e.target.closest('.layout-row');if(row){dragIndex=Number(row.dataset.index);e.dataTransfer.setData('text/plain',String(dragIndex));row.classList.add('dragging')}});
document.addEventListener('dragover',e=>{if(e.target.closest('.layout-row'))e.preventDefault()});
document.addEventListener('drop',e=>{const row=e.target.closest('.layout-row');if(row&&dragIndex!==null){e.preventDefault();const [item]=layoutDraft.splice(dragIndex,1);layoutDraft.splice(Number(row.dataset.index),0,item);dragIndex=null;drawLayout()}});
document.addEventListener('change',e=>{if(e.target.dataset.replace!==undefined){layoutDraft[Number(e.target.dataset.replace)]=e.target.value;drawLayout()}});
document.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[role=button][data-page]')){e.preventDefault();e.target.click()}});
document.addEventListener('click',async e=>{
 const b=e.target.closest('button,[role=button]');if(!b)return;
 if(b.hasAttribute('data-back'))back();
 if(b.hasAttribute('data-keyboard'))go('chat');
 if(b.hasAttribute('data-voice'))await recordVoice(b);
 if(b.hasAttribute('data-layout'))openLayout();
 if(b.hasAttribute('data-close-layout'))document.querySelector('#layout-editor').close();
 if(b.hasAttribute('data-reset-layout')){layoutDraft=[...defaultWidgets];drawLayout()}
 if(b.hasAttribute('data-save-layout')){if(preview){data.settings.home_widgets=[...layoutDraft];render()}else await mutate('home_layout',{widgets:layoutDraft});if(preview||!notice.textContent)document.querySelector('#layout-editor').close()}
 if(b.dataset.move!==undefined){const i=Number(b.dataset.move),j=i+Number(b.dataset.step);if(j>=0&&j<layoutDraft.length){[layoutDraft[i],layoutDraft[j]]=[layoutDraft[j],layoutDraft[i]];drawLayout()}}
 if(b.dataset.hide!==undefined){layoutDraft.splice(Number(b.dataset.hide),1);drawLayout()}
 if(b.dataset.addWidget){layoutDraft.push(b.dataset.addWidget);drawLayout()}
 if(b.dataset.taskTab){taskTab=b.dataset.taskTab;taskFilter='all';render()}
 if(b.dataset.taskFilter){taskFilter=b.dataset.taskFilter;render()}
 if(b.dataset.noteFilter){noteFilter=b.dataset.noteFilter;render()}
 if(b.dataset.peopleFilter){peopleFilter=b.dataset.peopleFilter;render()}
 if(b.dataset.budgetPeriod){budgetPeriod=b.dataset.budgetPeriod;loadBudget()}
 if(b.hasAttribute('data-memory-search')){if(!search.trim()){say('Введи запрос в строку поиска.');return}try{archiveResults=await api('archive_search',{query:search});render()}catch(err){say(err.message)}}
});
document.addEventListener('change',e=>{if(e.target.id==='budget-month'){budgetMonth=e.target.value;loadBudget()}});
async function loadBudget(){if(preview){render();return}budgetLoading=true;if(page==='budget')render();try{const result=await api('budget',page==='budget'?budgetRange():{});if(page==='budget')budgetData=result;else monthBudget=result}catch(e){say(e.message)}finally{budgetLoading=false;if(['home','budget'].includes(page))render()}}
function applyTelegramSafeArea(){
 if(!tg)return;
 const safe=tg.safeAreaInset||{},contentSafe=tg.contentSafeAreaInset||{},root=document.documentElement;
 for(const side of ['top','right','bottom','left']){
  const values=[safe[side],contentSafe[side]].map(Number).filter(Number.isFinite);
  if(values.length)root.style.setProperty(`--app-safe-${side}`,`${Math.max(0,...values)}px`)
 }
}
function prepareTelegramViewport(){
 if(!tg)return;
 tg.ready();
 tg.expand();
 applyTelegramSafeArea();
 tg.onEvent?.('safeAreaChanged',applyTelegramSafeArea);
 tg.onEvent?.('contentSafeAreaChanged',applyTelegramSafeArea);
 try{
  if(typeof tg.requestFullscreen==='function'&&(!tg.isVersionAtLeast||tg.isVersionAtLeast('8.0')))tg.requestFullscreen()
 }catch{}
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshStateOnResume()});
window.addEventListener('focus',refreshStateOnResume);
async function init(){prepareTelegramViewport();try{await designReady;tg?.setHeaderColor('#0A0A0A');tg?.setBackgroundColor('#0A0A0A');tg?.BackButton.onClick(back);render();if(preview)say('Предпросмотр · данные и сохранение доступны при открытии через Telegram.');else{await load();loadBudget();api('weather').then(result=>{weatherData=result;if(page==='home')render()}).catch(()=>{})}}catch(e){say(e.message)}finally{document.querySelector('#splash').classList.add('hidden')}}
init();
