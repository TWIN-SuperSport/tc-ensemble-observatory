const sessionSelect=document.getElementById('sessionSelect');
const loadStatus=document.getElementById('loadStatus');
const analysisSummary=document.getElementById('analysisSummary');
const analysisTitle=document.getElementById('analysisTitle');
const confidence=document.getElementById('confidence');
const analysisMeta=document.getElementById('analysisMeta');
const conclusion=document.getElementById('conclusion');
const reasoningSteps=document.getElementById('reasoningSteps');
const uncertainties=document.getElementById('uncertainties');
const falsifiers=document.getElementById('falsifiers');
const materials=document.getElementById('materials');
const materialCount=document.getElementById('materialCount');
let indexData=null;

function escapeHtml(value){return String(value??'').replace(/[&<>'"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]))}
function paragraphs(items){const rows=Array.isArray(items)?items:[items];return rows.filter(Boolean).map(text=>`<p>${escapeHtml(text)}</p>`).join('')||'<p>結論はまだ登録されていません。</p>'}
function listItems(items){return (items||[]).map(text=>`<li>${escapeHtml(text)}</li>`).join('')||'<li>登録なし</li>'}
function resolvePath(sessionPath,path){if(!path)return null;if(/^(https?:|data:|\/)/.test(path))return path;const base=sessionPath.split('/').slice(0,-1).join('/');return `${base}/${path}`}
function confidenceLabel(item){if(typeof item==='string')return item;const score=Number(item?.score);const label=item?.label||'未評価';return Number.isFinite(score)?`${label} (${Math.round(score*100)}%)`:label}

function renderMaterial(item,sessionPath){
  const image=resolvePath(sessionPath,item.image);
  const dataFile=resolvePath(sessionPath,item.data);
  const sourceUrl=item.sourceUrl;
  const preview=image?`<a class="materialPreview" href="${escapeHtml(image)}" target="_blank" rel="noopener"><img src="${escapeHtml(image)}" alt="${escapeHtml(item.title||item.id||'解析素材')}" loading="lazy" onerror="this.parentElement.classList.add('placeholder');this.parentElement.textContent='画像を読み込めませんでした'"></a>`:`<div class="materialPreview placeholder">画像なし / JSON・テキスト素材</div>`;
  const tags=(item.tags||[]).map(tag=>`<span>${escapeHtml(tag)}</span>`).join('');
  const links=[image?`<a href="${escapeHtml(image)}" target="_blank" rel="noopener">画像を開く</a>`:'',dataFile?`<a href="${escapeHtml(dataFile)}" target="_blank" rel="noopener">解析JSONを開く</a>`:'',sourceUrl?`<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener">出典を開く</a>`:''].filter(Boolean).join('');
  return `<article class="materialCard">${preview}<div class="materialBody"><h3>${escapeHtml(item.id||'Material')}：${escapeHtml(item.title||item.type||'名称未設定')}</h3><p><strong>用途：</strong>${escapeHtml(item.role||'未設定')}</p>${item.validTime?`<p><strong>対象時刻：</strong>${escapeHtml(item.validTime)}</p>`:''}${item.model?`<p><strong>モデル：</strong>${escapeHtml(item.model)}</p>`:''}<div class="materialTags">${tags}</div><div class="materialLinks">${links}</div></div></article>`;
}

async function loadSession(path){
  loadStatus.textContent='解析セッションを読み込み中…';
  analysisSummary.classList.remove('errorState');
  try{
    const response=await fetch(`${path}?t=${Date.now()}`,{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const data=await response.json();
    analysisTitle.textContent=data.title||data.analysisId||'名称未設定の解析';
    confidence.textContent=confidenceLabel(data.confidence);
    analysisMeta.innerHTML=[data.target?.length?`対象: ${data.target.join(', ')}`:null,data.generatedAt?`生成: ${data.generatedAt}`:null,data.initialTime?`初期値: ${data.initialTime}`:null,data.validTime?`対象時刻: ${data.validTime}`:null,data.status?`状態: ${data.status}`:null].filter(Boolean).map(text=>`<span>${escapeHtml(text)}</span>`).join('');
    conclusion.innerHTML=paragraphs(data.conclusion);
    reasoningSteps.innerHTML=listItems(data.reasoning);
    uncertainties.innerHTML=listItems(data.uncertainties);
    falsifiers.innerHTML=listItems(data.falsifiers);
    const rows=data.materials||[];
    materialCount.textContent=`${rows.length}件`;
    materials.innerHTML=rows.length?rows.map(item=>renderMaterial(item,path)).join(''):'<div class="emptyState">この解析セッションには、まだ素材が登録されていません。</div>';
    loadStatus.textContent=`読込完了：${data.generatedAt||data.analysisId||path}`;
    document.title=`${data.title||'AI解析室'} | 西太平洋台風進路予測観測所`;
  }catch(error){
    analysisSummary.classList.add('errorState');
    analysisTitle.textContent='解析セッションを読み込めませんでした';
    conclusion.innerHTML=`<p>${escapeHtml(error.message)}</p>`;
    reasoningSteps.innerHTML='';uncertainties.innerHTML='';falsifiers.innerHTML='';materials.innerHTML='';materialCount.textContent='0件';
    loadStatus.textContent='読み込み失敗';
  }
}

async function loadIndex(){
  try{
    const response=await fetch(`./analysis/index.json?t=${Date.now()}`,{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    indexData=await response.json();
    const sessions=indexData.sessions||[];
    if(!sessions.length)throw new Error('解析セッションが登録されていません');
    sessionSelect.innerHTML=sessions.map(item=>`<option value="${escapeHtml(item.path)}" ${item.path===indexData.latest?'selected':''}>${escapeHtml(item.label||item.id||item.path)}</option>`).join('');
    sessionSelect.addEventListener('change',()=>loadSession(sessionSelect.value));
    await loadSession(sessionSelect.value||indexData.latest||sessions[0].path);
  }catch(error){
    sessionSelect.innerHTML='<option>解析indexを読み込めません</option>';
    loadStatus.textContent=`読み込み失敗：${error.message}`;
    analysisSummary.classList.add('errorState');
    analysisTitle.textContent='analysis/index.json を確認してください';
    conclusion.innerHTML='<p>解析セッションの一覧を取得できませんでした。</p>';
  }
}

loadIndex();