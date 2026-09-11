/* Screen composition uses shared primitives in ui.js and existing backend actions. */
home=function(){
 const d=new Date(data.day+'T12:00:00'),pending=data.tasks.filter(t=>t.status==='open');
 const next=data.reminders.filter(r=>!r.acknowledged).sort((a,b)=>a.remind_at_utc.localeCompare(b.remind_at_utc));
 const current=weatherData?.current;
 const weather=current?`${icon(/ясно/i.test(current.condition)?'sun':'cloud')}<p>${esc(weatherData.city)}</p><p class="temperature">${Math.round(current.temperature)}°</p><p>${esc(current.condition)}</p><p>Ощущается как ${Math.round(current.feels_like)}°</p>`:`${icon('cloud')}<p>${preview?'Погода появится в Telegram':'Погода недоступна'}</p><button data-form="briefing">Выбрать город</button>`;
 const list=(items)=>items.map(x=>`<div class="widget-line">${icon('notes')}<span>${esc(x)}</span></div>`).join('');
 const snippets=pending.slice(0,3).map(t=>`<div class="widget-line">${icon('tasks')}<span>${esc(t.text)}<small>${date(t.due_date)}</small></span></div>`).join('');
 const spend=monthBudget?.currency_totals?.RUB?.expense;
 const bodies={
 tasks:[snippets||empty('Нет предстоящих задач'), 'tasks','tasks'],
 next_event:[next[0]?`<p class="amount">${new Date(next[0].remind_at_utc).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',timeZone:data.settings.timezone})}</p><p>${esc(next[0].text)}</p><small>${date(next[0].remind_at_utc)}</small>`:empty('Нет запланированных напоминаний'),'reminders','calendar'],
 notes:[list(data.notes.slice(0,3).map(n=>n.title||n.text.slice(0,65)))||empty('Нет заметок'),'notes','notes'],
 reminders:[list(next.slice(0,2).map(r=>r.text))||empty('Нет активных напоминаний'),'reminders','bell'],
 budget:[`<small>Расходы за текущий месяц · RUB</small><p class="amount">${spend===undefined?'—':money(spend)}</p><small>${spend===undefined?'Нет загруженных данных':'По учтённым операциям'}</small>`,'budget','wallet'],
 recent_saved:[list(data.files.slice(0,2).map(f=>f.original_name))||empty('Нет сохранённых файлов'),'archive','file'],
 people:[list(data.people.slice(0,3).map(p=>p.name))||empty('Нет сохранённых контактов'),'people','people']
 };
 const widgets=data.settings.home_widgets||defaultWidgets;
 return `<section class="card hero"><div><h3>${icon('calendar')} Сегодня</h3><div class="hero-date"><span class="date-number">${d.getDate()}</span><p>${d.toLocaleDateString('ru-RU',{day:'numeric',month:'long'}).replace(/^\d+\s/,'')}<br>${d.toLocaleDateString('ru-RU',{weekday:'long'})}</p></div></div><div class="weather">${weather}</div><canvas class="membrane" width="360" height="360" aria-hidden="true"></canvas></section><div class="grid">${widgets.map(type=>`<div data-widget="${type}">${card(widgetNames[type],`<div class="widget-content">${bodies[type][0]}</div>`,bodies[type][1],bodies[type][2])}</div>`).join('')}</div><div class="all-sections"><button data-layout>Настроить виджеты</button>${Object.entries({tasks:'Задачи',reminders:'Календарь',notes:'Заметки',archive:'Архив',people:'Люди',budget:'Бюджет'}).map(([key,title])=>`<button data-page="${key}">${title}</button>`).join('')}</div>`;
};
const originalSettings=settings;
settings=function(){return originalSettings().replace('<h1>Настройки</h1><p class="subtle">Фокус, ясность и контроль.</p>','')};
tasks=function(){
 const items=data.tasks.filter(t=>taskTab==='done'?t.status!=='open':t.status==='open'&&(taskTab==='upcoming'||!t.due_date||t.due_date.slice(0,10)<=data.day));
 return `${segmented([['today','Сегодня'],['upcoming','Предстоящие'],['done','Завершённые']],taskTab,'task-tab')}<div class="toolbar"><button class="secondary" data-form="task">Добавить задачу</button></div>${items.map(t=>`<article class="card row"><button class="check ${t.status!=='open'?'done':''}" data-toggle="${t.id}" aria-label="Изменить статус задачи"></button><details class="body"><summary><p>${esc(t.text)}</p><small>${date(t.due_date)}</small></summary><div class="details"><h3>Срок</h3><p>${date(t.due_date)}</p><p>Статус: ${t.status==='open'?'Предстоит':'Завершена'}</p><button class="secondary" data-delete="delete_task" data-id="${t.id}">Удалить задачу</button></div></details></article>`).join('')||empty('В этом разделе нет задач')}`;
};
const originalPeople=people;
people=function(){const original=data.people;data.people=original.filter(p=>`${p.name} ${p.notes} ${p.relationship}`.toLowerCase().includes(search.toLowerCase()));const html=originalPeople().replace('<h1>Люди</h1><p class="subtle">Noema помнит важное о людях.</p>','');data.people=original;return `<input id="search" class="search" aria-label="Поиск по людям" placeholder="Поиск по людям…" value="${esc(search)}">${html}`};
const originalArchive=archive;
archive=function(){let html=originalArchive().replace('<h1>Архив</h1><p class="subtle">Всё, что стоит сохранить.</p>','');if(page==='notes')html=html.replace('Найти в заметках и файлах…','Найти заметку…');return html+`<div class="toolbar"><button class="secondary" data-memory-search>Поиск по всей памяти</button></div>${archiveResults?card('Результаты поиска',archiveResults.results.map(r=>`<details class="archive-entry"><summary>${esc(r.title||r.summary||'Запись')}</summary><p>${esc(r.content||r.summary||r.text)}</p></details>`).join('')||empty('Совпадений не найдено')):''}`};
let budgetPeriod='month',budgetMonth='',budgetData=null,budgetLoading=false;
function budgetRange(){const selected=budgetMonth||data.day.slice(0,7),d=new Date(selected+'-01T12:00:00');let start=selected+'-01',end=new Date(d.getFullYear(),d.getMonth()+1,0,12).toLocaleDateString('en-CA');if(budgetPeriod==='day')start=end=data.day;if(budgetPeriod==='week'){const day=new Date(data.day+'T12:00:00');day.setDate(day.getDate()-6);start=day.toLocaleDateString('en-CA');end=data.day}if(budgetPeriod==='year'){start=selected.slice(0,4)+'-01-01';end=selected.slice(0,4)+'-12-31'}return {date_from:start,date_to:end}}
budget=function(){
 const stats=budgetData?.currency_totals||{},items=budgetData?.items||[],summary=Object.entries(stats).map(([currency,t])=>`<div><small>${esc(currency)}</small><p class="amount">${new Intl.NumberFormat('ru-RU',{style:'currency',currency}).format(t.balance)}</p><p class="subtle">Доход ${t.income.toLocaleString('ru-RU')} · Расход ${t.expense.toLocaleString('ru-RU')}</p></div>`).join('');
 const days={};items.filter(e=>e.kind!=='income'&&e.currency==='RUB').forEach(e=>{const key=e.spent_at.slice(0,10);days[key]=(days[key]||0)+Number(e.amount)});const values=Object.values(days).slice(0,31),max=Math.max(...values,1);
 return `<div class="period-picker"><label for="budget-month">Период</label><span>${icon('calendar')}</span><input type="month" id="budget-month" value="${budgetMonth||data.day.slice(0,7)}"></div>${segmented([['day','День'],['week','Неделя'],['month','Месяц'],['year','Год']],budgetPeriod,'budget-period')}<div class="toolbar compact-actions"><button class="secondary" data-form="expense">${icon('plus')}<span>Расход</span></button><button class="secondary" data-form="income">${icon('plus')}<span>Доход</span></button></div>${card('Баланс периода',budgetLoading?'<div class="skeleton" aria-label="Загрузка"></div>':summary||empty('Операций за период нет'))}${values.length?card('Расходы по дням · RUB',`<div class="bars" role="img" aria-label="Расходы по дням; суммы приведены в списке операций">${values.map(v=>`<i style="height:${Math.max(4,v/max*100)}%"></i>`).join('')}</div>`):''}${items.slice(0,100).map(e=>`<article class="card row"><div class="body"><p>${esc(e.description)}</p><small>${date(e.spent_at)} · ${esc(e.category)}</small></div><span class="mono">${e.kind==='income'?'+':'−'}${Number(e.amount).toLocaleString('ru-RU')} ${esc(e.currency)}</span>${iconButton('trash','Удалить операцию',`data-delete="delete_expense" data-id="${e.id}"`)}</article>`).join('')}${items.length>100?'<p class="subtle">Показаны первые 100 операций. Сузь период для просмотра остальных.</p>':''}`;
};
function renderDock(){
 const dock=document.querySelector('#dock');dock.hidden=['settings','chat'].includes(page);document.querySelector('#shell').classList.toggle('no-dock',dock.hidden);
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
 for(const name of ['hero','voice-area']){const canvas=content.querySelector(`.${name} canvas.membrane`);if(canvas)persistentMembranes.set(name,canvas)}
 const screens={home,archive,notes:archive,people,tasks,reminders,budget,settings,chat};
 content.innerHTML=(screens[page]||home)();
 const title={home:'Noema',archive:'Архив',notes:'Заметки',people:'Люди',tasks:'Задачи',reminders:'Календарь',budget:'Бюджет',settings:'Настройки',chat:'Чат'}[page];
 content.querySelector('h1')?.remove();
 document.querySelector('#header').innerHTML=`<div class="header-title">${page!=='home'?iconButton('back','Назад','data-back'):''}<h1>${title}</h1></div><div class="utilities">${page!=='settings'?iconButton('settings','Настройки','data-page="settings"'):''}</div>`;
 content.querySelector('.all-sections')?.remove();
 if(page==='budget'){
  const total=budgetData?.currency_totals?.RUB;
  const first=content.querySelector('.card');
  if(first){const panel=document.createElement('div');panel.className='budget-totals';panel.innerHTML=card('Расходы',`<p class="amount">${total?money(total.expense):'—'}</p><small>За выбранный период · RUB</small>`)+card('Доходы',`<p class="amount">${total?money(total.income):'—'}</p><small>За выбранный период · RUB</small>`);first.before(panel);
   const categories={};for(const e of budgetData?.items||[])if(e.kind!=='income'&&e.currency==='RUB')categories[e.category||'Прочее']=(categories[e.category||'Прочее']||0)+Number(e.amount);
   const entries=Object.entries(categories).sort((a,b)=>b[1]-a[1]),sum=entries.reduce((s,x)=>s+x[1],0);let offset=0;
   const slices=entries.map(([name,value],i)=>{const length=sum?value/sum*100:0;const slice=`<circle cx="60" cy="60" r="48" pathLength="100" fill="none" stroke="hsl(0 0% ${85-i%6*10}%)" stroke-width="10" stroke-dasharray="${Math.max(0,length-.6)} ${100-length+.6}" stroke-dashoffset="${-offset}"/>`;offset+=length;return slice}).join('');
   const chart=document.createElement('section');chart.className='card distribution';chart.innerHTML=`<h3>Распределение расходов</h3><div class="distribution-body"><svg viewBox="0 0 120 120" role="img" aria-label="Расходы по категориям в рублях"><circle cx="60" cy="60" r="48" fill="none" stroke="var(--border)" stroke-width="10"/>${slices}<text x="60" y="63" text-anchor="middle" fill="currentColor" font-size="9">${sum?esc(money(sum)):'Нет данных'}</text></svg><div>${entries.map(([name,value])=>`<p>${esc(name)} <span>${Math.round(value/sum*100)}%</span><small>${money(value)}</small></p>`).join('')||'<p class="subtle">Добавь первый расход — здесь появятся категории.</p>'}</div></div>`;panel.after(chart);
  }
 }
 document.querySelector('#shell').classList.toggle('chat-screen',page==='chat');
 document.querySelector('#shell').classList.toggle('budget-screen',page==='budget');
 document.querySelector('#shell').classList.toggle('settings',page==='settings');
 content.querySelectorAll('.delete').forEach(b=>b.innerHTML=icon('trash'));
 if(page==='chat'){
  content.querySelector('.subtle')?.remove();
  const form=content.querySelector('.composer');
  const voice=document.createElement('div');voice.className='voice-area';voice.innerHTML=`<button class="orb-button" data-voice aria-label="Голосовой запрос"><canvas class="membrane" width="300" height="300" aria-hidden="true"></canvas></button><p id="voice-status">${busy?'Обдумываю…':'Нажми, чтобы говорить'}</p>`;form.before(voice);
  form.insertAdjacentHTML('afterbegin',iconButton('mic','Записать голосовое сообщение','type="button" id="record"'));
  form.querySelector('[type=submit]').innerHTML=icon('arrow');
 }
 for(const name of ['hero','voice-area']){const canvas=content.querySelector(`.${name} canvas.membrane`),saved=persistentMembranes.get(name);if(canvas&&saved)canvas.replaceWith(saved)}
 renderDock();if(tg){page==='home'?tg.BackButton.hide():tg.BackButton.show()}
};
go=function(next){if(busy){say('Дождись завершения запроса или закончи голосовую запись.');return}if(next!==page)navHistory.push(page);page=next;search='';filter=next==='notes'?'notes':'all';archiveResults=null;say('');render();window.scrollTo({top:0});if(next==='budget')loadBudget()};
function back(){if(busy)return;if(document.querySelector('#layout-editor').open){document.querySelector('#layout-editor').close();return}if(document.querySelector('#editor').open){document.querySelector('#editor').close();return}page=navHistory.pop()||'home';search='';filter=page==='notes'?'notes':'all';render()}
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
 if(b.hasAttribute('data-keyboard')){go('chat');document.querySelector('#chat-input')?.focus()}
 if(b.hasAttribute('data-voice'))await recordVoice(b);
 if(b.hasAttribute('data-layout'))openLayout();
 if(b.hasAttribute('data-close-layout'))document.querySelector('#layout-editor').close();
 if(b.hasAttribute('data-reset-layout')){layoutDraft=[...defaultWidgets];drawLayout()}
 if(b.hasAttribute('data-save-layout')){if(preview){data.settings.home_widgets=[...layoutDraft];render()}else await mutate('home_layout',{widgets:layoutDraft});if(preview||!notice.textContent)document.querySelector('#layout-editor').close()}
 if(b.dataset.move!==undefined){const i=Number(b.dataset.move),j=i+Number(b.dataset.step);if(j>=0&&j<layoutDraft.length){[layoutDraft[i],layoutDraft[j]]=[layoutDraft[j],layoutDraft[i]];drawLayout()}}
 if(b.dataset.hide!==undefined){layoutDraft.splice(Number(b.dataset.hide),1);drawLayout()}
 if(b.dataset.addWidget){layoutDraft.push(b.dataset.addWidget);drawLayout()}
 if(b.dataset.taskTab){taskTab=b.dataset.taskTab;render()}
 if(b.dataset.budgetPeriod){budgetPeriod=b.dataset.budgetPeriod;loadBudget()}
 if(b.hasAttribute('data-memory-search')){if(!search.trim()){say('Введи запрос в строку поиска.');return}try{archiveResults=await api('archive_search',{query:search});render()}catch(err){say(err.message)}}
});
document.addEventListener('change',e=>{if(e.target.id==='budget-month'){budgetMonth=e.target.value;loadBudget()}});
async function loadBudget(){if(preview){render();return}budgetLoading=true;if(page==='budget')render();try{const result=await api('budget',page==='budget'?budgetRange():{});if(page==='budget')budgetData=result;else monthBudget=result}catch(e){say(e.message)}finally{budgetLoading=false;if(['home','budget'].includes(page))render()}}
async function init(){try{await designReady;tg?.setHeaderColor('#0A0A0A');tg?.setBackgroundColor('#0A0A0A');tg?.expand();tg?.BackButton.onClick(back);render();if(preview)say('Предпросмотр · данные и сохранение доступны при открытии через Telegram.');else{await load();loadBudget();api('weather').then(result=>{weatherData=result;if(page==='home')render()}).catch(()=>{})}}catch(e){say(e.message)}finally{tg?.ready();document.querySelector('#splash').classList.add('hidden')}}
init();
