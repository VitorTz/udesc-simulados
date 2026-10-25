'use strict';

const state = {
  auth: null, csrf: null, institution: 'UDESC',
  view: 'dashboard', editions: [], dashboard: null, subjects: [],
  selectedExam: null, exam: null, examIndex: 0, noteCache: {},
  filters: {search: '', subject: '', edition: '', offset: 0},
  builder: {editions: [], subjects: [], count: 20, shuffle: true, exclude_annulled: true},
};
const viewTitles = {dashboard:'Visão geral',builder:'Novo simulado',questions:'Banco de questões',history:'Meu desempenho',notes:'Anotações',editions:'Provas e gabaritos',exam:'Simulado',results:'Resultado'};
const main = document.getElementById('main');
const modalRoot = document.getElementById('modal-root');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtDate = s => s ? new Date(s).toLocaleDateString('pt-BR',{day:'2-digit',month:'short',year:'numeric'}) : '—';
const pct = (a,b) => b ? Math.round(100*a/b) : 0;

async function request(path, options={}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = {...(options.body instanceof FormData ? {} : (options.body ? {'Content-Type':'application/json'} : {})), ...(options.headers || {})};
  if (!['GET','HEAD','OPTIONS'].includes(method) && state.csrf && !['/auth/login','/auth/register'].includes(path)) {
    headers['X-CSRF-Token'] = state.csrf;
  }
  if (['/dashboard','/editions','/questions','/notes','/attempts'].some(p=>path===p||path.startsWith(p+'?'))) {
    path+=(path.includes('?')?'&':'?')+'institution='+state.institution;
  }
  const response = await fetch('/api'+path, {...options, credentials:'same-origin', headers});
  if (!response.ok) {
    const data = await response.json().catch(()=>({}));
    if (response.status===401 && !path.startsWith('/auth/')) showAuth('login', 'Sua sessão expirou. Entre novamente.');
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Falha na requisição (' + response.status + ')');
  }
  return response.json();
}

function showAuth(mode='login', message='') {
  state.auth = null; state.csrf = null;
  document.getElementById('app-layout').hidden = true;
  const root = document.getElementById('auth-root');
  root.hidden = false;
  const register = mode === 'register';
  root.innerHTML = `<section class="auth-card" aria-label="${register?'Criar conta':'Entrar'}">
    <div class="auth-logo">U<span>·</span></div>
    <p class="eyebrow">VESTIBULARES UDESC + UFSC</p>
    <h1>${register?'Crie sua conta':'Acesse seus simulados'}</h1>
    <p class="auth-description">${register?'Comece a estudar com seu próprio banco de questões e histórico privado.':'Entre para consultar provas, simulados e anotações.'}</p>
    <div class="auth-tabs"><button type="button" id="tab-login" class="${!register?'active':''}">Entrar</button><button type="button" id="tab-register" class="${register?'active':''}">Criar conta</button></div>
    <form id="auth-form">
      <label class="input-label" for="auth-email">E-mail</label>
      <input id="auth-email" class="input" type="email" autocomplete="email" required maxlength="254" placeholder="voce@exemplo.com">
      <label class="input-label" for="auth-password" style="margin-top:18px">Senha</label>
      <input id="auth-password" class="input" type="password" autocomplete="${register?'new-password':'current-password'}" required minlength="${register?12:1}" maxlength="128" placeholder="${register?'Mínimo de 12 caracteres':'Sua senha'}">
      ${register?'<p class="hint">Use pelo menos 12 caracteres. Não é necessária verificação de e-mail.</p>':''}
      <p id="auth-error" class="auth-error" role="alert">${esc(message)}</p>
      <button class="btn btn-primary btn-block" type="submit" id="auth-submit">${register?'Criar conta':'Entrar'}</button>
    </form>
    <p class="auth-foot">Seus dados ficam no NeonDB; PDFs e imagens são armazenados no Cloudflare R2.</p>
  </section>`;
  document.getElementById('tab-login').onclick = () => showAuth('login');
  document.getElementById('tab-register').onclick = () => showAuth('register');
  document.getElementById('auth-form').onsubmit = async event => {
    event.preventDefault();
    const button = document.getElementById('auth-submit');
    button.disabled = true; button.textContent = register?'Criando conta...':'Entrando...';
    const payload = {email:document.getElementById('auth-email').value.trim(), password:document.getElementById('auth-password').value};
    try {
      const result = await request(register?'/auth/register':'/auth/login',{method:'POST',body:JSON.stringify(payload)});
      startApp(result);
    } catch(err) {
      document.getElementById('auth-error').textContent = err.message;
      button.disabled = false; button.textContent = register?'Criar conta':'Entrar';
    }
  };
}

function startApp(result) {
  state.auth = result.user;
  state.csrf = result.csrf_token;
  state.editions=[]; state.dashboard=null; state.subjects=[]; state.exam=null;
  state.noteCache={}; state.selectedExam=null;
  document.getElementById('user-email').textContent = result.user.email;
  document.getElementById('auth-root').hidden=true;
  document.getElementById('app-layout').hidden=false;
  navigate('dashboard');
}

async function bootstrapAuth() {
  try {startApp(await request('/auth/me'))}
  catch {showAuth('login')}
}

async function logout() {
  try {await request('/auth/logout',{method:'POST'}); showAuth('login');}
  catch(err) { fail(err); }
}

function toast(message, isError=false){
  const item = document.createElement('div'); item.className='toast';
  if(isError) item.style.background='#803b36'; item.textContent=message;
  document.getElementById('toast-root').appendChild(item);
  setTimeout(()=>item.remove(),4800);
}
function fail(err){console.error(err);toast(err.message || 'Erro inesperado',true)}
function iconEmpty(title,detail,button=''){
 return `<div class="empty"><div class="empty-symbol">▤</div><strong>${esc(title)}</strong><p>${esc(detail)}</p>${button}</div>`;
}
function header(eyebrow,title,lead,action=''){
 return `<div class="page-header"><div><p class="eyebrow">${esc(eyebrow)}</p><h1>${esc(title)}</h1><p class="lead">${esc(lead)}</p></div>${action}</div>`;
}
function setActive(){
 document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x.dataset.view===state.view));
 document.getElementById('crumb').textContent=viewTitles[state.view] || 'Estudos';
 document.querySelector('.sidebar').classList.remove('open');
}
async function refreshMetadata(){
 const [editions, dashboard] = await Promise.all([request('/editions'),request('/dashboard')]);
 state.editions=editions;state.dashboard=dashboard;state.subjects=dashboard.subjects;
}
async function navigate(view, opts={}){
 state.view=view;setActive();main.innerHTML='<div class="loading">Carregando...</div>';
 try {
   if(['dashboard','builder','editions'].includes(view) || !state.dashboard) await refreshMetadata();
   if(view==='dashboard')renderDashboard();
   else if(view==='builder')renderBuilder();
   else if(view==='editions')renderEditions();
   else if(view==='questions')await renderQuestions();
   else if(view==='notes')await renderNotes();
   else if(view==='history')await renderHistory();
   else if(view==='exam')await renderExam();
   else if(view==='results')await renderResults(opts.result);
 }catch(e){fail(e);main.innerHTML=iconEmpty('Não foi possível carregar',e.message,'<button class="btn" onclick="navigate(\'dashboard\')">Voltar</button>')}
 window.scrollTo({top:0,behavior:'instant'});
}
window.navigate=navigate;
window.switchBank=async value=>{
  state.institution=value; state.editions=[]; state.subjects=[]; state.dashboard=null; state.exam=null;
  state.filters={search:'',subject:'',edition:'',offset:0};
  document.getElementById('bank-select').value=value; await navigate('dashboard');
};

function renderDashboard(){
 const {totals,subjects,history}=state.dashboard;
 const cards=[['Edições importadas',totals.editions,'▱','Provas de diferentes anos'],['Banco de questões',totals.questions,'☷','Questões disponíveis'],['Gabaritos vinculados',totals.answered_keys,'✓','Prontas para correção'],['Simulados concluídos',totals.completed,'◷','Seu histórico de prática']];
 main.innerHTML=header('SEU PAINEL DE ESTUDO','Seu progresso começa aqui','Organize provas anteriores, pratique por disciplina e acompanhe sua evolução.',`<button class="btn" onclick="navigate('editions')">Gerenciar biblioteca →</button>`)+
 `<div class="cards">${cards.map(c=>`<div class="card"><span class="stat-mark">${c[2]}</span><span class="card-label">${c[0]}</span><strong class="big-num">${c[1]}</strong><div class="card-foot">${c[3]}</div></div>`).join('')}</div>`+
 `<div class="dashboard-grid"><section class="panel"><h2 class="panel-title">Questões por disciplina <small>${totals.questions} no total</small></h2>`+
 (subjects.length ? subjects.map(x=>`<div class="bar-row"><span class="bar-label">${esc(x.subject)}</span><div class="bar-track"><div class="bar-fill" style="width:${pct(x.count,Math.max(...subjects.map(s=>s.count)))}%"></div></div><span class="bar-count">${x.count}</span></div>`).join('') : iconEmpty('Nenhuma prova importada','Comece importando uma edição da '+state.institution+'.','<button class="btn btn-primary" onclick="navigate(\'editions\')">Importar PDF</button>'))+
 `</section><section class="panel"><h2 class="panel-title">Simulados recentes <small><a href="#" onclick="event.preventDefault();navigate('history')">Ver todos →</a></small></h2>`+
 (history.length ? history.slice(0,5).map(x=>`<div class="recent-row"><div><strong>${esc(x.title)}</strong><small>${fmtDate(x.completed_at)}</small></div><div class="score">${x.institution==='UFSC'&&x.objective_max?Math.round(100*x.objective_points/x.objective_max):pct(x.correct,x.graded)}%</div></div>`).join('') : iconEmpty('Nenhum simulado ainda','Crie um simulado para começar a registrar seus acertos.'))+
 `</section></div><div class="callout" style="margin-top:18px"><strong>Como estudar:</strong> selecione as matérias e edições que deseja combinar; responda sem consultar o gabarito e finalize para ver correção por disciplina. Use a anotação de cada questão para registrar erros recorrentes.</div>`;
}

function renderBuilder(){
 const editions=state.editions,subjects=state.subjects;
 main.innerHTML=header('PREPARAÇÃO PERSONALIZADA','Montar simulado','Misture edições e matérias. O sistema sempre seleciona primeiro as questões que você acertou menos vezes.')+
 `<form class="grid-2" id="builder-form"><section class="panel"><h2 class="panel-title">01 / Escolha as edições</h2><p class="section-intro">Você pode misturar provas de diferentes anos em um único simulado. Sem marcações, serão utilizadas todas as edições.</p>`+
 (editions.length?`<div class="check-grid" id="edition-choice">${editions.map(e=>`<label class="check-tile"><input type="checkbox" name="edition" value="${e.id}"> <span>${esc(e.label)}${e.institution==='UFSC'?' · '+esc(e.booklet_color):''} <small class="muted">(${e.questions})</small></span></label>`).join('')}</div>`:iconEmpty('Importe uma edição primeiro','Os arquivos ficam no Cloudflare R2 e os dados das questões no NeonDB.','<button class="btn" type="button" onclick="navigate(\'editions\')">Ir para biblioteca</button>'))+
 `<div class="divider"></div><h2 class="panel-title">02 / Selecione as disciplinas</h2><p class="section-intro">Nenhuma marcada = todas as matérias disponíveis.</p><div class="check-grid" id="subject-choice">${subjects.map(s=>`<label class="check-tile"><input type="checkbox" name="subject" value="${esc(s.subject)}"><span>${esc(s.subject)} <small class="muted">(${s.count})</small></span></label>`).join('')}</div></section>`+
 `<section class="panel"><h2 class="panel-title">03 / Configuração</h2><div class="form-field"><label for="exam-title" class="input-label">Nome do simulado</label><input class="input" id="exam-title" value="Revisão de vestibular" maxlength="100"></div>`+
 `<div class="form-field"><label for="exam-count" class="input-label">Número de questões</label><input class="input" id="exam-count" type="number" value="20" min="1" max="500"><p class="hint">Se houver menos questões que o solicitado, o simulado incluirá todas as disponíveis.</p></div>`+
 `<label class="check-tile" style="margin-bottom:10px"><input type="checkbox" id="exam-shuffle" checked> Embaralhar a ordem das questões selecionadas</label>`+
 `<label class="check-tile" style="margin-bottom:10px"><input type="checkbox" id="exam-cancelled" ${state.institution==='UFSC'?'':'checked'}> Excluir questões anuladas</label>`+
 `<label class="check-tile" style="margin-bottom:15px"><input type="checkbox" id="exam-key" checked> Apenas questões com gabarito</label>`+
 `<div class="callout"><strong>Revisão inteligente:</strong> questões com menos acertos em simulados já concluídos têm prioridade na seleção. O embaralhamento muda somente a ordem de apresentação (e sorteia empates). Acertos são contabilizados apenas ao finalizar.</div>`+
 `<button class="btn btn-primary btn-block" style="margin-top:25px;padding:14px" type="submit" ${!editions.length?'disabled':''}>Iniciar simulado →</button></section></form>`;
 document.getElementById('builder-form').addEventListener('submit',async(e)=>{
  e.preventDefault();
  const c={institution:state.institution, title:document.getElementById('exam-title').value||'Simulado '+state.institution,
    edition_ids:[...document.querySelectorAll('[name="edition"]:checked')].map(e=>Number(e.value)),
    subjects:[...document.querySelectorAll('[name="subject"]:checked')].map(e=>e.value),
    count:Number(document.getElementById('exam-count').value)||20,
    shuffle:document.getElementById('exam-shuffle').checked,
    exclude_annulled:document.getElementById('exam-cancelled').checked,
    only_with_key:document.getElementById('exam-key').checked};
  try{const out=await request('/exams',{method:'POST',body:JSON.stringify(c)});state.selectedExam=out.id;state.examIndex=0;await navigate('exam')}catch(err){fail(err)}
 });
}

function renderEditions(){
 const ufsc=state.institution==='UFSC';
 main.innerHTML=header('ARQUIVO DE VESTIBULARES','Provas e gabaritos',`Biblioteca privada da ${state.institution}. Cada cor/modelo UFSC tem sua própria numeração e gabarito.`)+
 `<div class="grid-2"><section class="panel"><h2 class="panel-title">Importar ${state.institution}</h2><form id="upload-form">`+
 `<input type="hidden" name="institution" value="${state.institution}">`+
 `<div class="form-field"><label class="input-label">Edição</label><input class="input" name="label" placeholder="Ex.: 2025/1" required minlength="3"></div>`+
 (ufsc?`<div class="form-field"><label class="input-label">Cor/modelo da prova</label><input name="color" class="input" value="azul" placeholder="azul, amarela, cinza ..." required></div>`:'')+
 `<div class="form-field file-drop"><p>${ufsc?'Prova 1 (PDF)':'Prova matutina (PDF)'}</p><input type="file" name="morning" accept=".pdf,application/pdf"></div>`+
 `<div class="form-field file-drop"><p>${ufsc?'Prova 2 (PDF)':'Prova vespertina (PDF)'}</p><input type="file" name="afternoon" accept=".pdf,application/pdf"></div>`+
 (ufsc ? `<div class="form-field file-drop"><p>Gabarito da Prova 1 (PDF ou HTML)</p><input type="file" name="key_morning" accept=".pdf,.html,.htm"></div><div class="form-field file-drop"><p>Gabarito da Prova 2 (PDF ou HTML)</p><input type="file" name="key_afternoon" accept=".pdf,.html,.htm"></div>`:
 `<div class="form-field file-drop"><p>Gabarito oficial (PDF)</p><input type="file" name="key" accept=".pdf,application/pdf"></div>`)+
 `<button type="submit" class="btn btn-primary btn-block" id="upload-btn">Importar edição</button></form></section>`+
 `<section class="panel"><h2 class="panel-title">Modelo de correção</h2><p class="section-intro">${ufsc?'Somatório de proposições (01, 02, 04, 08, 16, 32 e 64), pontuação parcial conforme edital e questões abertas. A nota oficial de classificação exige redação, discursivas, língua estrangeira e pesos do curso.':'Uma alternativa correta A–E para cada questão objetiva.'}</p><div class="callout">Língua estrangeira é ignorada na importação. A conta e o armazenamento permanecem separados por usuário, universidade e edição.</div><p class="hint">Para a UFSC, escolha sempre a cor correspondente aos dois PDFs e seus respectivos gabaritos.</p></section></div>`+
 `<section style="margin-top:30px"><h2>Edições cadastradas</h2>`+
 (state.editions.length?state.editions.map(e=>`<div class="edition-row"><div class="edition-icon">▱</div><div class="edition-row-content"><strong>${esc(e.institution)} ${esc(e.label)} ${e.institution==='UFSC'?'· '+esc(e.booklet_color):''}</strong><div class="meta"><span>${e.questions} questões</span><span>${e.files} documentos</span><span>${e.keys} com gabarito</span></div>${e.warning?`<p class="hint danger-text">${esc(e.warning)}</p>`:''}</div><button class="btn btn-sm" onclick="showEditionQuestions(${e.id})">Questões</button>${!ufsc?`<button class="btn btn-sm" onclick="attachKey(${e.id})">Gabarito</button>`:''}<button class="btn btn-sm btn-danger" onclick="removeEdition(${e.id},'${esc(e.label)}')">Excluir</button></div>`).join(''):iconEmpty('Biblioteca vazia','Importe uma prova e seu gabarito.'))+'</section>';
 document.getElementById('upload-form').addEventListener('submit',async e=>{
  e.preventDefault();const form=new FormData(e.target);
  if(!form.get('morning')?.size&&!form.get('afternoon')?.size){toast('Selecione uma prova PDF',true);return}
  for(const name of ['morning','afternoon','key','key_morning','key_afternoon'])if(!form.get(name)?.size)form.delete(name);
  const btn=document.getElementById('upload-btn');btn.disabled=true;btn.textContent='Processando PDFs...';
  try{const r=await request('/editions/import',{method:'POST',body:form});toast(`${r.imported} questões importadas · ${r.answers} gabaritos · ${r.annulled} anuladas`);await navigate('editions')}
  catch(err){fail(err);btn.disabled=false;btn.textContent='Importar edição'}
 });
}
window.showEditionQuestions=(id)=>{state.filters={search:'',edition:String(id),subject:'',offset:0};navigate('questions')};
window.removeEdition=async(id,label)=>{
 if(!confirm(`Excluir a edição ${label} e seus PDFs? As anotações associadas serão apagadas. Os resultados históricos já finalizados continuarão salvos.`))return;
 try{await request('/editions/'+id,{method:'DELETE'});toast('Edição excluída.');await navigate('editions')}catch(e){fail(e)}
};

async function renderQuestions(){
 const f=state.filters;
 main.innerHTML=header('QUESTÕES IMPORTADAS','Banco de questões','Pesquise, confira recortes e gabaritos, edite disciplinas e registre suas anotações.')+
 `<div class="filterbar"><input class="input" id="qsearch" placeholder="Pesquisar no enunciado..." value="${esc(f.search)}"><select class="select" id="qsubject"><option value="">Todas as disciplinas</option>${state.subjects.map(s=>`<option value="${esc(s.subject)}" ${f.subject===s.subject?'selected':''}>${esc(s.subject)}</option>`).join('')}</select><select class="select" id="qedition"><option value="">Todas as edições</option>${state.editions.map(e=>`<option value="${e.id}" ${f.edition===String(e.id)?'selected':''}>${esc(e.label)}</option>`).join('')}</select></div><div id="questions-list"></div>`;
 const changeFilter=()=>{f.subject=document.getElementById('qsubject').value;f.edition=document.getElementById('qedition').value;f.offset=0;listQuestions()};
 document.getElementById('qsubject').onchange=changeFilter;document.getElementById('qedition').onchange=changeFilter;
 let debounce;document.getElementById('qsearch').oninput=e=>{const s=e.target.value;clearTimeout(debounce);debounce=setTimeout(()=>{f.search=s;f.offset=0;listQuestions()},250)};
 await listQuestions();
}
async function listQuestions(){
 const el=document.getElementById('questions-list');if(!el)return;
 const f=state.filters;
 try{
  const p=new URLSearchParams({limit:'24',offset:String(f.offset)});
  if(f.edition)p.set('edition_id',f.edition);if(f.subject)p.set('subject',f.subject);if(f.search)p.set('search',f.search);
  const data=await request('/questions?'+p);
  el.innerHTML=`<div class="flex-between" style="margin:0 0 15px"><span class="muted text-small">${data.total} questões encontradas</span><span class="muted text-small">Página ${1+Math.floor(f.offset/24)}</span></div>`+
  (data.items.length ? data.items.map(q=>`<div class="question-row" onclick="openQuestion(${q.id})"><div class="q-num">${String(q.number).padStart(2,'0')}</div><div class="question-row-content"><div class="flex-wrap"><strong>${esc(q.subject)}</strong><span class="pill">${esc(state.institution)} · ${esc(q.edition)}${q.institution==='UFSC'?' · '+esc(q.booklet_color):''} · ${q.session==='morning'?'manhã':'tarde'}</span>${q.annulled?'<span class="pill orange">Anulada</span>':(q.correct_answer||q.numeric_key!==null)?'<span class="pill green">Com gabarito</span>':'<span class="pill orange">Sem gabarito</span>'}${q.has_note?'<span class="pill">Anotação</span>':''}<span class="pill green">✓ ${q.correct_count||0} acerto(s)</span></div><p>${esc(q.text_content.replace(/\s+/g,' ').slice(0,205))}</p></div><span class="muted">↗</span></div>`).join('') : iconEmpty('Nenhuma questão encontrada','Altere os filtros ou importe outra edição.'))+
  `<div class="flex-between" style="margin-top:21px"><button class="btn btn-sm" ${f.offset<=0?'disabled':''} onclick="pageQuestions(-24)">← Anterior</button><button class="btn btn-sm" ${f.offset+24>=data.total?'disabled':''} onclick="pageQuestions(24)">Próxima →</button></div>`;
 }catch(e){fail(e)}
}
window.pageQuestions=async(delta)=>{state.filters.offset=Math.max(0,state.filters.offset+delta);await listQuestions()};

function renderMedia(id,count,full=false,offset=0){
 if(!id)return '<div class="callout">PDF original excluído. O histórico de respostas foi preservado.</div>';
 return full ? `<img loading="lazy" alt="Página completa do PDF original" src="/api/questions/${id}/page/${offset}?scale=1.45">` :
     Array.from({length:count},(_,i)=>`<img loading="lazy" alt="Recorte original da questão ${i+1}" src="/api/questions/${id}/render/${i}?scale=1.7">`).join('');
}
function renderReferences(id,references){
 if(!id || !Array.isArray(references) || !references.length)return '';
 return `<section class="reference-material" aria-label="Materiais de apoio da questão"><div class="reference-heading">Textos e imagens de apoio <span class="pill">${references.length} referência(s)</span></div>`+
   references.map((r,group)=>`<div class="reference-group"><strong>${esc(r.label)}</strong><div class="reference-images">`+
    Array.from({length:r.clips},(_,i)=>`<img loading="lazy" alt="${esc(r.label)} · trecho ${i+1}" src="/api/questions/${id}/reference/${group}/${i}?scale=1.7">`).join('')+
    `</div></div>`).join('')+`</section>`;
}
function contextControls(id, count, parent='q'){
 return `<div class="question-actions"><button class="btn btn-sm" onclick="showContext(${id},0)">Ver página original</button><button class="btn btn-sm" onclick="showContext(${id},-1)">Página anterior / texto de apoio</button></div>`;
}
window.showContext=(id,initialOffset=0)=>{
 let offset=initialOffset;
 function draw(){
  modalRoot.innerHTML=`<div class="modal-backdrop" onclick="if(event.target===this)closeModal()"><div class="modal"><div class="modal-head"><h2>PDF original · Página ${offset<0?'anterior':offset===0?'da questão':'seguinte'}</h2><button class="modal-close" onclick="closeModal()">×</button></div><div class="page-navigator"><button class="btn btn-sm" id="prev-page">← Anterior</button><span class="pill">Deslocamento ${offset>=0?'+':''}${offset}</span><button class="btn btn-sm" id="next-page">Próxima →</button></div><img class="page-image" src="/api/questions/${id}/page/${offset}?scale=1.4" alt="Página de contexto da prova" onerror="this.alt='Página indisponível';this.style.display='none'"></div></div>`;
  document.getElementById('prev-page').onclick=()=>{offset--;draw()};document.getElementById('next-page').onclick=()=>{offset++;draw()};
 }
 draw();
};
window.closeModal=()=>modalRoot.innerHTML='';

window.openQuestion=async(id)=>{
 try{
  const q=await request('/questions/'+id);state.noteCache[id]=q.note;
  modalRoot.innerHTML=`<div class="modal-backdrop" onclick="if(event.target===this)closeModal()"><div class="modal"><div class="modal-head"><h2>Questão ${String(q.number).padStart(2,'0')} · ${esc(q.subject)} <span class="pill">${esc(q.edition)}${q.institution==='UFSC'?' · '+esc(q.booklet_color):''}</span></h2><button class="modal-close" onclick="closeModal()">×</button></div>${renderReferences(id,q.references)}<div class="question-media">${renderMedia(id,q.clips)}</div>${contextControls(id,q.clips)}`+
  `<div class="divider"></div><p class="hint">Acertos em simulados finalizados: <strong>${q.correct_count||0}</strong>. A contagem considera apenas seus simulados.</p><div class="grid-2"><div><label class="input-label">Disciplina</label><input class="input" id="edit-subject" value="${esc(q.subject)}"><div class="form-field" style="margin-top:14px"><label class="input-label">Gabarito oficial</label>${q.answer_kind==='choice'?`<select class="select" id="edit-answer"><option value="">Não informado</option>${'ABCDE'.split('').map(l=>`<option ${q.correct_answer===l?'selected':''}>${l}</option>`).join('')}</select>`:`<input class="input" id="edit-answer" type="number" min="0" max="127" value="${q.numeric_key??''}" placeholder="Somatório ou resposta aberta"><label class="input-label" style="margin-top:8px">Quantidade de proposições (somatório)</label><input class="input" id="edit-np" type="number" min="1" max="7" value="${q.proposition_count??''}" ${q.answer_kind==='open'?'disabled':''}>`}</div><div class="flex-wrap"><label class="check-tile"><input type="checkbox" id="edit-annulled" ${q.annulled?'checked':''}> Questão anulada</label><select class="select" id="edit-difficulty" style="width:155px"><option value="">Dificuldade —</option>${[1,2,3,4,5].map(x=>`<option value="${x}" ${q.difficulty===x?'selected':''}>Nível ${x}/5</option>`).join('')}</select></div><button class="btn btn-primary" style="margin-top:14px" id="save-question-meta">Salvar informações</button></div>`+
  `<div><label class="input-label">Suas anotações</label><textarea class="textarea" id="question-note" placeholder="Fórmulas, erros, dicas, interpretação...">${esc(q.note)}</textarea><button class="btn" id="save-question-note" style="margin-top:10px">Salvar anotação</button></div></div></div></div>`;
  document.getElementById('save-question-note').onclick=async()=>{
    try{await request('/questions/'+id+'/note',{method:'PUT',body:JSON.stringify({body:document.getElementById('question-note').value})});toast('Anotação salva.');state.noteCache[id]=document.getElementById('question-note').value}
    catch(err){fail(err)}
  };
  document.getElementById('save-question-meta').onclick=async()=>{
    try{await request('/questions/'+id,{method:'PUT',body:JSON.stringify({subject:document.getElementById('edit-subject').value,correct_answer:q.answer_kind==='choice'?(document.getElementById('edit-answer').value||null):null,numeric_key:q.answer_kind!=='choice'&&document.getElementById('edit-answer').value!==''?Number(document.getElementById('edit-answer').value):null,proposition_count:q.answer_kind==='sum'?Number(document.getElementById('edit-np').value)||null:null,annulled:document.getElementById('edit-annulled').checked,difficulty:document.getElementById('edit-difficulty').value?Number(document.getElementById('edit-difficulty').value):null})});toast('Dados da questão atualizados.');await refreshMetadata();if(state.view==='questions')await listQuestions()}
    catch(err){fail(err)}
  };
 }catch(e){fail(e)}
};

async function renderExam(){
 if(!state.selectedExam){await navigate('history');return}
 state.exam=await request('/exams/'+state.selectedExam);
 const exam=state.exam;
 if(state.examIndex>=exam.items.length)state.examIndex=0;
 const q=exam.items[state.examIndex];const completed=exam.status==='completed';
 const answered=exam.items.filter(i=>i.selected).length;
 main.innerHTML=`<div class="page-header"><div><p class="eyebrow">${completed?'REVISÃO DE RESPOSTAS':'SIMULADO EM ANDAMENTO'}</p><h1>${esc(exam.title)}</h1><p class="lead">${exam.items.length} questões · ${answered} respondidas · Iniciado ${fmtDate(exam.created_at)} · Tempo <strong id="exam-timer">00:00:00</strong></p></div><button class="btn" onclick="navigate('history')">← Meus simulados</button></div>`+
 `<div class="exam-layout"><div class="question-surface"><div class="question-header"><div><span class="pill green">${esc(q.edition)}</span><span class="pill" style="margin-left:6px">${esc(q.subject)}</span><h2 style="margin-top:8px">Questão ${state.examIndex+1} de ${exam.items.length} <small class="muted">· Nº ${q.number??'—'} na prova · ${q.correct_count||0} acerto(s) registrados</small></h2></div><button class="btn btn-sm" onclick="toggleFlag()" ${completed?'disabled':''}>${q.flagged?'◆ Marcada':'◇ Marcar revisão'}</button></div>`+
 `${renderReferences(q.id,q.references)}<div class="question-media">${renderMedia(q.id,q.clips)}</div>${q.id?contextControls(q.id,q.clips):''}`+
 `<div class="divider"></div><div class="flex-between"><strong>${completed?'Sua resposta e gabarito':'Selecione a alternativa correta'}</strong><span class="muted text-small">${completed?'Correção oficial':'Uma única resposta por questão'}</span></div>`+
 `<div class="answers">${q.answer_kind==='sum'?
   `<p class="hint">Marque todas as proposições que considera verdadeiras. Somatório escolhido: <strong>${q.selected??0}</strong>.</p><div class="ufsc-options">${[1,2,4,8,16,32,64].slice(0,q.proposition_count||7).map(v=>`<button class="answer-btn ${(Number(q.selected||0)&v)?'chosen':''} ${completed?'review':''}" ${completed?'disabled':''} onclick="toggleUFSC(${v})">${String(v).padStart(2,'0')} ${(Number(q.selected||0)&v)?'✓':''}</button>`).join('')}</div>` : q.answer_kind==='open'?
   `<label class="input-label">Resposta numérica de 00 a 99</label><input id="ufsc-open-answer" class="input" type="number" min="0" max="99" value="${q.selected??''}" ${completed?'disabled':''} placeholder="Digite a resposta"><button class="btn btn-primary" onclick="saveUFSCOpen()" ${completed?'disabled':''}>Salvar resposta</button>`:
   'ABCDE'.split('').map(letter=>{let cl=q.selected===letter?' chosen':'';if(completed&&q.correct_answer===letter)cl+=' correct';else if(completed&&q.selected===letter&&letter!==q.correct_answer)cl+=' wrong';return `<button ${completed?'disabled':''} class="answer-btn${cl}" onclick="selectAnswer('${letter}')">${letter}</button>`}).join('')}</div>`+
 (completed?`<div class="callout" style="margin-top:16px">${q.annulled?'Questão anulada pelo gabarito.':q.answer_kind!=='choice' ? `Gabarito: <strong>${String(q.numeric_key).padStart(2,'0')}</strong> · Sua resposta: ${q.selected??'em branco'}${q.answer_kind==='sum'?' · UFSC aplica pontuação parcial por proposições.':''} · Pontos: <strong>${q.points===null?'—':Number(q.points).toFixed(2).replace('.',',')}</strong>`:q.correct_answer?`Gabarito: <strong>${q.correct_answer}</strong> · ${q.selected===q.correct_answer?'Você acertou.':'Você não acertou.'}`:'Sem gabarito.'}</div>`:
 `<div class="text-small muted" style="margin-top:13px">Você pode alterar antes de finalizar.</div>`)+
 `<div class="exam-footer"><button class="btn" onclick="moveExam(-1)" ${state.examIndex===0?'disabled':''}>← Anterior</button><span class="muted text-small">${state.examIndex+1} / ${exam.items.length}</span><button class="btn btn-primary" onclick="moveExam(1)" ${state.examIndex+1===exam.items.length?'disabled':''}>Próxima →</button></div></div>`+
 `<aside class="exam-side"><section class="panel"><h2 class="panel-title">Navegação <small>${answered}/${exam.items.length}</small></h2><div class="progress-track"><div class="progress-fill" style="width:${pct(answered,exam.items.length)}%"></div></div><div class="question-palette">${exam.items.map((x,i)=>`<button onclick="goExam(${i})" title="Questão ${i+1}" class="palette-item ${i===state.examIndex?'current':''} ${x.selected?'answered':''} ${x.flagged?'flagged':''}">${i+1}</button>`).join('')}</div><p class="hint">Verde: respondida. Ponto amarelo: marcada para revisão.</p>${completed?`<button class="btn btn-primary btn-block" onclick="showExamResult(${exam.id})" style="margin-top:18px">Ver meu resultado</button>`:`<button class="btn btn-primary btn-block" onclick="confirmFinish()" style="margin-top:18px">Finalizar e corrigir</button>`}</section>`+
 `<section class="panel" style="margin-top:15px"><h2 class="panel-title">Anotações da questão</h2><textarea class="textarea" id="exam-note" placeholder="Registre dúvidas, macetes ou fórmulas..."></textarea><button class="btn btn-sm btn-block" id="save-exam-note" style="margin-top:10px" ${!q.id?'disabled':''}>Salvar anotação</button><p class="hint">As anotações ficam ligadas à questão, independentemente do simulado.</p></section></aside></div>`;
 if(q.id){
  const el=document.getElementById('exam-note');
  if(state.noteCache[q.id]!==undefined)el.value=state.noteCache[q.id];
  else request('/questions/'+q.id).then(r=>{state.noteCache[q.id]=r.note;if(state.exam?.items[state.examIndex]?.id===q.id)el.value=r.note}).catch(fail);
  document.getElementById('save-exam-note').onclick=async()=>{
    try{await request('/questions/'+q.id+'/note',{method:'PUT',body:JSON.stringify({body:el.value})});state.noteCache[q.id]=el.value;toast('Anotação salva')}
    catch(err){fail(err)}
  };
 }
}
window.goExam=async(i)=>{state.examIndex=i;await renderExam();window.scrollTo(0,0)};
window.moveExam=async(delta)=>{await window.goExam(Math.max(0,Math.min(state.exam.items.length-1,state.examIndex+delta)))};
window.selectAnswer=async(letter)=>{
 const q=state.exam.items[state.examIndex];if(state.exam.status!=='active')return;
 const old=q.selected, next=old===letter?null:letter;q.selected=next;
 try{await request(`/exams/${state.exam.id}/answers/${q.position}`,{method:'PUT',body:JSON.stringify({answer:next,flagged:q.flagged})});await renderExam()}
 catch(e){q.selected=old;fail(e)}
};
window.toggleUFSC=async(bit)=>{
 const q=state.exam.items[state.examIndex];if(state.exam.status!=='active')return;
 const next=(Number(q.selected||0)^bit);
 // 0 corresponds to clearing all marked propositions.
 await saveUFSCValue(q,next===0?null:String(next));
};
window.saveUFSCOpen=async()=>{
 const q=state.exam.items[state.examIndex], v=document.getElementById('ufsc-open-answer').value;
 if(v!==''&&(+v<0||+v>99||!Number.isInteger(+v))){toast('Digite um inteiro de 00 a 99',true);return}
 await saveUFSCValue(q,v===''?null:String(Number(v)));
};
async function saveUFSCValue(q, value){
 const old=q.selected;q.selected=value;
 try{await request(`/exams/${state.exam.id}/answers/${q.position}`,{method:'PUT',body:JSON.stringify({answer:value,flagged:q.flagged})});await renderExam()}
 catch(e){q.selected=old;fail(e)}
}
window.toggleFlag=async()=>{
 const q=state.exam.items[state.examIndex];q.flagged=!q.flagged;
 try{await request(`/exams/${state.exam.id}/answers/${q.position}`,{method:'PUT',body:JSON.stringify({answer:q.selected,flagged:q.flagged})});await renderExam()}catch(e){fail(e)}
};
window.confirmFinish=async()=>{
 const n=state.exam.items.filter(x=>!x.selected).length;
 if(!confirm(`Finalizar e corrigir o simulado agora? ${n?`${n} questão(ões) sem resposta. `:''}Após finalizar, não será possível alterar respostas.`))return;
 try{const r=await request('/exams/'+state.exam.id+'/finish',{method:'POST'});await navigate('results',{result:r})}catch(e){fail(e)}
};
window.showExamResult=async(id)=>{state.selectedExam=id;try{const r=await request('/exams/'+id+'/finish',{method:'POST'});await navigate('results',{result:r})}catch(e){fail(e)}};

async function renderResults(result){
 if(!result){const exam=state.exam;if(!exam){await navigate('history');return}result=await request('/exams/'+exam.id+'/finish',{method:'POST'})}
 const s=result;
 const pointInfo=s.institution==='UFSC'?`<div class="callout"><strong>Pontuação objetiva UFSC (fórmula COPERVE): ${Number(s.objective_points).toFixed(2).replace('.',',')} de ${Number(s.objective_max).toFixed(2).replace('.',',')} ponto(s)</strong><p>Crédito parcial incluído. ${esc(s.scoring_note)}</p></div>`:'';
 main.innerHTML=header('RESULTADO DO SIMULADO','Sua correção','O percentual considera apenas questões com gabarito válido; anuladas e sem gabarito não entram no denominador.')+
 pointInfo+`<div class="grid-2"><section class="panel"><div class="score-hero"><p class="eyebrow">APROVEITAMENTO</p><div class="number">${s.percentage===null?'—':s.percentage.toLocaleString('pt-BR')+'%'}</div><p class="muted">${s.correct} acertos integrais em ${s.graded} questões avaliáveis</p></div><div class="score-stats"><div class="score-stat"><strong style="color:#168063">${s.correct}</strong><small>Acertos</small></div><div class="score-stat"><strong style="color:var(--red)">${s.wrong}</strong><small>Erros</small></div><div class="score-stat"><strong>${s.unanswered}</strong><small>Em branco</small></div></div><p class="hint text-center">${s.annulled} anuladas · ${s.without_key} sem gabarito · ${s.total} questões na sessão</p><button class="btn btn-primary btn-block" onclick="reviewExam(${s.id})" style="margin-top:15px">Revisar questão por questão →</button></section>`+
 `<section class="panel"><h2 class="panel-title">Desempenho por disciplina</h2>${Object.entries(s.subjects).map(([name,data])=>`<div class="bar-row" style="display:block"><div class="flex-between"><strong class="text-small">${esc(name)}</strong><span class="text-small muted">${data.correct}/${data.total} · ${pct(data.correct,data.total)}%</span></div><div class="bar-track" style="margin-top:8px"><div class="bar-fill" style="width:${pct(data.correct,data.total)}%"></div></div></div>`).join('')||'<p class="muted">Sem questões avaliáveis.</p>'}<div class="callout" style="margin-top:18px">Reabra este simulado em “Meu desempenho” quando quiser. O histórico de respostas será preservado.</div></section></div>`;
}
window.reviewExam=async(id)=>{state.selectedExam=id;state.examIndex=0;await navigate('exam')};

async function renderHistory(){
 const data=await request('/attempts');
 main.innerHTML=header('EVOLUÇÃO DOS ESTUDOS','Meu desempenho','Acompanhe todas as tentativas, retome simulados e revise seus erros.')+
 (data.length?`<section class="panel"><h2 class="panel-title">Histórico de simulados <small>${data.length} registro(s)</small></h2>${data.map(x=>`<div class="recent-row"><div><strong>${esc(x.title)}</strong><small>${fmtDate(x.created_at)} · ${x.total} questões · ${x.status==='completed'?'Concluído':'Em andamento'}</small></div><div class="flex-wrap">${x.status==='completed'?`<strong class="score">${x.institution==='UFSC'&&x.objective_max?Math.round(100*x.objective_points/x.objective_max):pct(x.correct,x.graded)}%</strong>`:`<span class="pill orange">${x.answered}/${x.total} feitas</span>`}<button class="btn btn-sm" onclick="reviewExam(${x.id})">${x.status==='completed'?'Revisar':'Continuar'}</button>${x.status==='completed'?`<button class="btn btn-sm" onclick="showExamResult(${x.id})">Resultado</button>`:''}<button class="btn btn-sm btn-danger" onclick="removeAttempt(${x.id})">Excluir</button></div></div>`).join('')}</section>`:
 iconEmpty('Nenhum simulado realizado','Crie um simulado a partir das suas provas importadas.','<button class="btn btn-primary" onclick="navigate(\'builder\')">Criar simulado</button>'));
}
window.removeAttempt=async(id)=>{if(!confirm('Excluir este simulado e suas respostas?'))return;try{await request('/attempts/'+id,{method:'DELETE'});toast('Simulado removido.');await renderHistory()}catch(e){fail(e)}};

async function renderNotes(){
 const notes=await request('/notes');
 main.innerHTML=header('MEMÓRIA DE APRENDIZADO','Minhas anotações','Registros por questão para revisões futuras, independentemente dos simulados.')+
 (notes.length?`<div class="flex-between" style="margin-bottom:17px"><strong>${notes.length} anotações salvas</strong><span class="muted text-small">Mais recentes primeiro</span></div>${notes.map(n=>`<div class="note-row" onclick="openQuestion(${n.question_id})"><div class="q-num">${String(n.number).padStart(2,'0')}</div><div class="question-row-content"><div class="flex-wrap"><strong>${esc(n.subject)}</strong><span class="pill">${esc(n.edition)}${n.institution==='UFSC'?' · '+esc(n.booklet_color):''} · ${n.session==='morning'?'manhã':'tarde'}</span><span class="muted text-small">${fmtDate(n.updated_at)}</span></div><p>${esc(n.body)}</p></div><span>↗</span></div>`).join('')}`:iconEmpty('Você ainda não tem anotações','Abra uma questão ou inicie um simulado para registrar observações.'));
}

window.addEventListener('keydown',e=>{
 if(e.key==='Escape'&&modalRoot.children.length){closeModal();return}
 if(state.view!=='exam'||modalRoot.children.length||['TEXTAREA','INPUT','SELECT'].includes(document.activeElement?.tagName))return;
 if(e.key==='ArrowRight')moveExam(1);else if(e.key==='ArrowLeft')moveExam(-1);
 else if('abcde'.includes(e.key.toLowerCase())&&e.key.length===1)selectAnswer(e.key.toUpperCase());
});
document.getElementById('logout-button').onclick = logout;
document.getElementById('menu-toggle').onclick=()=>document.querySelector('.sidebar').classList.toggle('open');
document.querySelectorAll('[data-view]').forEach(x=>x.onclick=()=>navigate(x.dataset.view));
document.getElementById('today').textContent=new Date().toLocaleDateString('pt-BR',{weekday:'long',day:'2-digit',month:'long'});
bootstrapAuth();

window.attachKey=async (editionId)=>{
 const picker=document.createElement('input');picker.type='file';picker.accept='.pdf,application/pdf';
 picker.onchange=async()=>{
  if(!picker.files?.length)return;
  const data=new FormData();data.append('key',picker.files[0]);
  try{const r=await request('/editions/'+editionId+'/key',{method:'POST',body:data});toast(`Gabarito atualizado: ${r.matched} questões associadas.`);await navigate('editions')}catch(e){fail(e)}
 };picker.click();
};

setInterval(()=>{
 const el=document.getElementById('exam-timer');
 if(!el||!state.exam)return;
 const sec=Math.max(0,Math.floor((Date.now()-Date.parse(state.exam.created_at))/1000));
 const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=sec%60;
 el.textContent=`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
},1000);
