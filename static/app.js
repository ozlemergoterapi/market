let state, selectedPhoto = null, selectedPhotos = [], photoLoading = false, activeImportCheck = null;
let pdfOcrPhotos = new Set();
let pdfTextLayers = new Map();
let calendarMonth = null;
let lastOcrSupplier = '', activeFirmHistoryId = null;
let batchQueue = [], batchIndex = 0, batchActive = false;
const $ = s => document.querySelector(s);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const currency = cents => new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY'}).format(cents/100);
const unitCurrency = cents => new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY',minimumFractionDigits:2,maximumFractionDigits:2}).format(cents/100);
const niceDate = s => new Date(s+'T12:00:00').toLocaleDateString('tr-TR',{day:'numeric',month:'short',year:'numeric'});
async function api(path, data) {
  let r,value;
  try{
    r = await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    value = await r.json();
  }catch(error){
    throw new Error('Uygulama bağlantısı kapandı. Masaüstündeki Sancaklı Market simgesinden uygulamayı yeniden açın.');
  }
  if(!r.ok) throw new Error(value.error || 'İşlem tamamlanamadı.');
  return value;
}
function toast(message){$('#notice').textContent=message;$('#notice').hidden=false;setTimeout(()=>$('#notice').hidden=true,6000);}
function empty(title,text){return `<div class="empty"><b>${esc(title)}</b>${esc(text)}</div>`;}
function table(head,rows){return `<table><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;}
function invoiceTable(list){
  if(!list.length)return empty('Burada henüz fatura yok.','Firma ve faturanızı eklediğinizde ödeme planınız burada görünecek.');
  return table(['Firma / Fatura','Ödeme tarihi','Kalan borç','Durum',''],list.map(i=>{
    const badge = !i.remaining ? ['','Ödendi'] : i.late_days ? ['late',`${i.late_days} gün gecikti`] : i.due===state.today ? ['today','Bugün'] : ['','Bekliyor'];
    const opening=i.kind==='opening';
    const detail=opening?'Açılış bakiyesi':`${esc(i.number)} · ${niceDate(i.issued)}`;
    const totalNote=opening?`Toplam ${currency(i.total)}`:`Toplam ${currency(i.total)}${i.credited?` · İade ${currency(i.credited)}`:''}`;
    const actions=opening?`<button data-edit-entry="${i.id}">Düzelt</button> <button data-delete-entry="${i.id}" class="delete-button">Sil</button> ${i.remaining?`<button data-pay-opening="${i.id}">Ödeme kaydet</button>`:''}`:`<button data-edit-invoice="${i.id}">Düzelt</button> <button data-delete-invoice="${i.id}" class="delete-button">Sil</button> ${i.has_photo?`<button data-photo="${i.id}" aria-label="Fatura fotoğrafını aç">▧</button> `:''}${i.remaining?`<button data-pay="${i.id}">Ödeme kaydet</button>`:''}`;
    return `<tr><td><strong>${esc(i.firm)}</strong><small>${detail}</small></td><td>${niceDate(i.due)}</td><td><strong>${currency(i.remaining)}</strong><small>${totalNote}</small></td><td><span class="badge ${badge[0]}">${badge[1]}</span></td><td>${actions}</td></tr>`;
  }));
}
function monthKey(date){return date.getUTCFullYear()+'-'+String(date.getUTCMonth()+1).padStart(2,'0');}
function renderCalendar(){
  if(!calendarMonth)calendarMonth=state.today.slice(0,7);
  const [year,month]=calendarMonth.split('-').map(Number),first=new Date(Date.UTC(year,month-1,1));
  $('#calendar-month').textContent=first.toLocaleDateString('tr-TR',{month:'long',year:'numeric',timeZone:'UTC'});
  const startOffset=(first.getUTCDay()+6)%7,days=new Date(Date.UTC(year,month,0)).getUTCDate();
  const byDay={};
  state.obligations.filter(invoice=>invoice.remaining>0&&invoice.due.slice(0,7)===calendarMonth).forEach(invoice=>(byDay[Number(invoice.due.slice(8))]??=[]).push(invoice));
  const cells=[];
  for(let index=0;index<startOffset;index++)cells.push('<div class="calendar-day empty-day" aria-hidden="true"></div>');
  for(let day=1;day<=days;day++){
    const invoices=byDay[day]||[],total=invoices.reduce((sum,invoice)=>sum+invoice.remaining,0);
    const iso=calendarMonth+'-'+String(day).padStart(2,'0');
    cells.push(`<article class="calendar-day${iso===state.today?' today-cell':''}${invoices.length?' has-payment':''}"><span>${day}</span>${invoices.length?`<strong>${currency(total)}</strong>${invoices.map(invoice=>`<small>${esc(invoice.firm)} · ${currency(invoice.remaining)}</small>`).join('')}`:'<small>Ödeme yok</small>'}</article>`);
  }
  $('#payment-calendar').innerHTML='<div class="calendar-weekdays">'+['Pzt','Sal','Çar','Per','Cum','Cmt','Paz'].map(day=>`<b>${day}</b>`).join('')+'</div><div class="calendar-grid">'+cells.join('')+'</div>';
}
function moveCalendar(offset){
  const [year,month]=calendarMonth.split('-').map(Number),next=new Date(Date.UTC(year,month-1+offset,1));
  calendarMonth=monthKey(next);renderCalendar();
}
function showFirmHistory(firmId){
  const firm=state.firms.find(entry=>entry.id===firmId);if(!firm)return;
  activeFirmHistoryId=firmId;
  const invoices=state.invoices.filter(invoice=>invoice.firm_id===firmId),invoiceIds=new Set(invoices.map(invoice=>invoice.id));
  const openings=state.openings.filter(entry=>entry.firm_id===firmId),openingIds=new Set(openings.map(entry=>entry.id));
  const credits=state.credits.filter(entry=>entry.firm_id===firmId);
  const payments=state.payments.filter(payment=>payment.target_type==='invoice'?invoiceIds.has(payment.target_id):openingIds.has(payment.target_id));
  const events=[...invoices.map(invoice=>({date:invoice.issued,type:'invoice',invoice})),...openings.map(entry=>({date:entry.entry_date,type:'opening',entry})),...credits.map(entry=>({date:entry.entry_date,type:'credit',entry})),...payments.map(payment=>({date:payment.paid_on,type:'payment',payment,invoice:invoices.find(invoice=>invoice.id===payment.target_id),opening:openings.find(entry=>entry.id===payment.target_id)}))].sort((a,b)=>b.date.localeCompare(a.date)||Number(b.type==='payment')-Number(a.type==='payment'));
  $('#firm-history-title').textContent=firm.name;
  $('#firm-history-summary').innerHTML=`<div><small>Fatura + açılış</small><strong>${currency(invoices.reduce((sum,invoice)=>sum+invoice.total,0)+openings.reduce((sum,entry)=>sum+entry.amount,0))}</strong></div><div><small>Ödeme + iade</small><strong>${currency(payments.reduce((sum,payment)=>sum+payment.amount,0)+credits.reduce((sum,entry)=>sum+entry.amount,0))}</strong></div><div><small>Kalan cari</small><strong>${currency(firm.balance)}</strong></div>`;
  $('#firm-history-list').innerHTML=events.length?events.map(event=>{
    if(event.type==='invoice')return `<article><time>${niceDate(event.date)}</time><div><b>Fatura · ${esc(event.invoice.number)}</b><p>Toplam ${currency(event.invoice.total)} · İade ${currency(event.invoice.credited||0)} · Kalan ${currency(event.invoice.remaining)} · Vade ${niceDate(event.invoice.due)}</p><button data-edit-invoice="${event.invoice.id}">Faturayı aç</button></div></article>`;
    if(event.type==='opening')return `<article><time>${niceDate(event.date)}</time><div><b>Açılış bakiyesi · ${currency(event.entry.amount)}</b><p>${esc(event.entry.reference||'Devir bakiyesi')} · Kalan ${currency(event.entry.remaining)} · Vade ${niceDate(event.entry.due)}</p><button data-edit-entry="${event.entry.id}">Düzelt</button> <button data-delete-entry="${event.entry.id}" class="delete-button">Sil</button></div></article>`;
    if(event.type==='credit')return `<article class="payment-event"><time>${niceDate(event.date)}</time><div><b>İade / alacak · ${currency(event.entry.amount)}</b><p>${esc(event.entry.invoice_number||'')} · ${esc(event.entry.reference||'Belge numarası yok')}</p><button data-edit-entry="${event.entry.id}">Düzelt</button> <button data-delete-entry="${event.entry.id}" class="delete-button">Sil</button></div></article>`;
    return `<article class="payment-event"><time>${niceDate(event.date)}</time><div><b>Ödeme · ${currency(event.payment.amount)}</b><p>${esc(event.invoice?.number||event.opening?.reference||'Açılış bakiyesi')}</p><button data-edit-payment="${event.payment.id}" data-payment-type="${event.payment.target_type}">Ödemeyi aç</button></div></article>`;
  }).join(''):empty('Henüz hareket yok.','Bu firmaya ait fatura, açılış bakiyesi, iade veya ödeme bulunmuyor.');
  $('#statement-from').value=state.today.slice(0,4)+'-01-01';$('#statement-to').value=state.today;$('#firm-statement').hidden=true;
  $('#firm-history-dialog').showModal();
}
function renderFirmStatement(){
  const from=$('#statement-from').value,to=$('#statement-to').value,firm=state.firms.find(entry=>entry.id===activeFirmHistoryId);
  if(!firm||!from||!to||from>to){toast('Cari ekstre için geçerli başlangıç ve bitiş tarihleri seçin.');return;}
  const invoices=state.invoices.filter(invoice=>invoice.firm_id===firm.id),ids=new Set(invoices.map(invoice=>invoice.id));
  const openings=state.openings.filter(entry=>entry.firm_id===firm.id),openingIds=new Set(openings.map(entry=>entry.id));
  const credits=state.credits.filter(entry=>entry.firm_id===firm.id);
  const payments=state.payments.filter(payment=>payment.target_type==='invoice'?ids.has(payment.target_id):openingIds.has(payment.target_id));
  const openingInvoices=invoices.filter(invoice=>invoice.issued<from).reduce((sum,invoice)=>sum+invoice.total,0)+openings.filter(entry=>entry.entry_date<from).reduce((sum,entry)=>sum+entry.amount,0);
  const openingPayments=payments.filter(payment=>payment.paid_on<from).reduce((sum,payment)=>sum+payment.amount,0)+credits.filter(entry=>entry.entry_date<from).reduce((sum,entry)=>sum+entry.amount,0);
  const periodInvoices=invoices.filter(invoice=>invoice.issued>=from&&invoice.issued<=to);
  const periodOpenings=openings.filter(entry=>entry.entry_date>=from&&entry.entry_date<=to);
  const periodCredits=credits.filter(entry=>entry.entry_date>=from&&entry.entry_date<=to);
  const periodPayments=payments.filter(payment=>payment.paid_on>=from&&payment.paid_on<=to);
  const opening=openingInvoices-openingPayments,invoiceTotal=periodInvoices.reduce((sum,invoice)=>sum+invoice.total,0)+periodOpenings.reduce((sum,entry)=>sum+entry.amount,0),paymentTotal=periodPayments.reduce((sum,payment)=>sum+payment.amount,0)+periodCredits.reduce((sum,entry)=>sum+entry.amount,0),closing=opening+invoiceTotal-paymentTotal;
  const rows=[...periodInvoices.map(invoice=>({date:invoice.issued,label:'Fatura '+invoice.number,debit:invoice.total,credit:0})),...periodOpenings.map(entry=>({date:entry.entry_date,label:'Açılış bakiyesi · '+(entry.reference||'Devir'),debit:entry.amount,credit:0})),...periodCredits.map(entry=>({date:entry.entry_date,label:'İade / alacak · '+(entry.reference||entry.invoice_number||''),debit:0,credit:entry.amount})),...periodPayments.map(payment=>({date:payment.paid_on,label:'Ödeme',debit:0,credit:payment.amount}))].sort((a,b)=>a.date.localeCompare(b.date)||b.debit-a.debit);
  $('#firm-statement').hidden=false;
  $('#firm-statement').innerHTML=`<h3>${niceDate(from)} – ${niceDate(to)} cari ekstresi</h3><div class="history-summary"><div><small>Dönem başı borcu</small><strong>${currency(opening)}</strong></div><div><small>Dönem faturaları</small><strong>${currency(invoiceTotal)}</strong></div><div><small>Dönem ödemeleri</small><strong>${currency(paymentTotal)}</strong></div><div><small>Dönem sonu borcu</small><strong>${currency(closing)}</strong></div></div>${rows.length?table(['Tarih','Hareket','Borç','Ödeme'],rows.map(row=>`<tr><td>${niceDate(row.date)}</td><td>${esc(row.label)}</td><td>${row.debit?currency(row.debit):'—'}</td><td>${row.credit?currency(row.credit):'—'}</td></tr>`)):empty('Bu dönemde hareket yok.','Seçilen tarihler arasında fatura veya ödeme bulunmuyor.')}`;
}
function showPriceHistory(historyId){
  const history=(state.price_histories||[]).find(entry=>entry.id===historyId);if(!history)return;
  $('#price-history-title').textContent=history.product;
  $('#price-history-meta').textContent=history.firm+' · '+history.unit+' · Kod: '+(history.code||'—');
  $('#price-history-list').innerHTML=history.points.map((point,index)=>{const previous=history.points[index-1],change=previous?((point.price/previous.price-1)*100):null;return `<article><div><time>${niceDate(point.issued)}</time><small>${esc(point.number)} · ${esc(point.product)}</small></div><strong>${unitCurrency(point.price)}</strong>${change===null?'<span class="badge">İlk fiyat</span>':`<span class="badge ${change>0?'late':''}">${change>0?'↑':'↓'} %${Math.abs(change).toLocaleString('tr-TR',{maximumFractionDigits:2})}</span>`}<button data-edit-invoice="${point.invoice_id}">Faturayı aç</button></article>`;}).join('');
  $('#price-history-dialog').showModal();
}
function renderProductSearch(){
  const query=$('#product-search').value.trim().toLocaleLowerCase('tr');
  const values=state.items.filter(item=>!query||(item.product+' '+(item.code||'')).toLocaleLowerCase('tr').includes(query)).sort((a,b)=>b.issued.localeCompare(a.issued)||b.id-a.id).slice(0,300);
  $('#product-search-results').innerHTML=values.length?table(['Ürün / Kod','Firma','Fatura tarihi','Alış fiyatı',''],values.map(item=>`<tr><td><strong>${esc(item.product)}</strong><small>Kod: ${esc(item.code||'—')} · ${esc(item.unit)}</small></td><td>${esc(item.firm)}</td><td>${niceDate(item.issued)}<small>${esc(item.number)}</small></td><td><strong>${unitCurrency(item.price)}</strong></td><td><button data-edit-invoice="${item.invoice_id}">Faturayı aç</button></td></tr>`)):empty(query?'Ürün bulunamadı.':'Henüz ürün yok.',query?'Adın veya kodun bir bölümünü deneyin.':'Fatura ürünleri kaydedildiğinde burada aranabilir.');
}
function renderReviewQueue(){
  const alerts=state.alerts.filter(alert=>!alert.approved),suggestions=state.product_suggestions||[],unmatched=state.unmatched_products||[];
  const count=alerts.length+suggestions.length+unmatched.length;
  $('#review-queue').innerHTML=count?`<div class="review-cards"><article><small>Fiyat değişimi</small><strong>${alerts.length}</strong></article><article><small>Ürün eşleştirmesi</small><strong>${suggestions.length}</strong></article><article><small>Eşleşmeyen ürün</small><strong>${unmatched.length}</strong></article></div>${alerts.length?`<div class="panel review-panel"><h3>Onay bekleyen fiyatlar</h3>${alerts.map(alert=>`<p><b>${esc(alert.product)}</b> · ${esc(alert.firm)} · ${unitCurrency(alert.old_price)} → ${unitCurrency(alert.price)} <button data-price-history="${alert.history_id}">Geçmiş</button> <button data-approve="${alert.fingerprint}" class="primary">Onayla</button></p>`).join('')}</div>`:''}${suggestions.length?`<div class="panel review-panel"><h3>Karar bekleyen ürünler</h3>${suggestions.map(value=>`<p><b>${esc(value.current.product)}</b> ↔ ${esc(value.previous.product)} · ${esc(value.current.firm)} <button data-open-prices>Karar ver</button></p>`).join('')}</div>`:''}${unmatched.length?`<div class="panel review-panel"><h3>Geçmişi eşleşmeyen ürünler</h3>${unmatched.map(item=>`<p><b>${esc(item.product)}</b> · ${esc(item.firm)} · ${esc(item.number)} <button data-edit-invoice="${item.invoice_id}">Faturayı kontrol et</button></p>`).join('')}</div>`:''}`:empty('Kontrol bekleyen kayıt yok.','Yeni fiyat veya belirsiz ürün çıktığında burada gösterilecek.');
}
function render(){
  let banner=$('#price-notification');
  if(!banner){banner=document.createElement('div');banner.id='price-notification';banner.className='tip';$('#overview').before(banner);}
  const pending=state.alerts.filter(a=>!a.approved).length;const matching=(state.product_suggestions||[]).length;
  banner.hidden=!(pending||matching);
  banner.innerHTML=`<b>${pending} fiyat değişimi · ${matching} ürün eşleştirmesi onay bekliyor.</b><span>Artış ve düşüşlerin tamamını kontrol edin. Yanlış okunan fiyatı faturadan düzeltebilirsiniz.</span><button id="review-prices">Fiyat değişimlerini incele</button>`;
  banner.querySelector('button').onclick=()=>$('[data-tab=prices]').click();

  $('#date').textContent=new Date(state.today+'T12:00:00').toLocaleDateString('tr-TR',{weekday:'long',day:'numeric',month:'long',year:'numeric'});
  Object.entries(state.totals).forEach(([k,v])=>$('#'+k+'-total').textContent=currency(v));
  const filter=$('#agenda-filter').value;
  const end=new Date(state.today+'T12:00:00');end.setDate(end.getDate()+7);
  $('#agenda').innerHTML=invoiceTable(state.obligations.filter(i=>i.remaining>0 && (filter==='open'||filter==='today'&&i.due===state.today||filter==='late'&&i.late_days>0||filter==='week'&&i.due>state.today&&new Date(i.due+'T12:00:00')<=end)));
  const daily = {};
  state.obligations.filter(i=>i.remaining>0).forEach(i=>daily[i.due]=(daily[i.due]||0)+i.remaining);
  let dailySection=$('#daily-summary');
  if(!dailySection){dailySection=document.createElement('div');dailySection.id='daily-summary';$('#agenda').before(dailySection);}
  dailySection.innerHTML=Object.keys(daily).length?`<div class="daily-totals">${Object.entries(daily).sort(([a],[b])=>a.localeCompare(b)).map(([d,total])=>`<div><small>${niceDate(d)}${d<state.today?' · Gecikti':''}</small><strong>${currency(total)}</strong></div>`).join('')}</div>`:'';
  renderCalendar();
  const backup=state.backup||{};
  $('#backup-status').innerHTML=`<b>Otomatik yedekleme açık</b><span>${backup.latest?`Son yedek: ${esc(backup.latest)} · Toplam ${backup.count} yedek`:'İlk yedek, uygulamayı bir sonraki açışınızda oluşturulacak.'}<br>Konum: ${esc(backup.directory||'')}</span><div class="backup-actions"><button data-choose-backup>Klasörü değiştir</button><button data-reset-backup>Varsayılan klasöre dön</button></div>${backup.files?.length?`<div class="backup-restore"><select id="backup-file" aria-label="Geri yüklenecek yedek">${backup.files.map(file=>`<option value="${esc(file.name)}">${esc(file.name)} · ${(file.size/1048576).toLocaleString('tr-TR',{maximumFractionDigits:1})} MB</option>`).join('')}</select><button data-restore-backup>Seçili yedeği geri yükle</button></div>`:''}`;
  const search=$('#search').value.toLocaleLowerCase('tr');
  $('#invoice-list').innerHTML=invoiceTable(state.invoices.filter(i=>(i.firm+' '+i.number).toLocaleLowerCase('tr').includes(search)));
  $('#firm-list').innerHTML=state.firms.length?state.firms.map(f=>`<article class="firm-card"><h2>${esc(f.name)}</h2><p>Kalan cari borç</p><strong>${currency(f.balance)}</strong><p>Fatura tanıma: ${f.ocr_samples?`${f.ocr_samples} onaylı örnek`:'İlk örnek bekleniyor'}</p>${f.ocr_aliases?`<small>${esc(f.ocr_aliases)}</small>`:''}<p><button data-firm-history="${f.id}" class="primary">Hesap geçmişi</button> <button data-edit-firm="${f.id}">Düzelt</button> <button data-delete-firm="${f.id}" class="delete-button">Sil</button></p></article>`).join(''):empty('İlk firmanızı ekleyin.','Faturalarını takip edeceğiniz tedarikçilerinizi buradan kaydedin.');
  const entries=[...state.openings,...state.credits].sort((a,b)=>b.entry_date.localeCompare(a.entry_date)||b.id-a.id);
  $('#account-entry-list').innerHTML=entries.length?table(['Firma / Hareket','Tarih','Tutar / Kalan',''],entries.map(entry=>`<tr><td><strong>${esc(entry.firm)}</strong><small>${entry.kind==='opening'?'Açılış bakiyesi':'İade / alacak · Fatura '+esc(entry.invoice_number||'')}</small></td><td>${niceDate(entry.entry_date)}${entry.kind==='opening'?`<small>Vade ${niceDate(entry.due)}</small>`:''}</td><td><strong>${entry.kind==='credit'?'− ':''}${currency(entry.amount)}</strong>${entry.kind==='opening'?`<small>Kalan ${currency(entry.remaining)}</small>`:''}<small>${esc(entry.reference||'')}</small></td><td><button data-edit-entry="${entry.id}">Düzelt</button> <button data-delete-entry="${entry.id}" class="delete-button">Sil</button></td></tr>`)):empty('Henüz açılış veya iade kaydı yok.','Gerekirse yukarıdaki düğmelerden ekleyebilirsiniz.');
  $('#price-list').innerHTML=state.alerts.length?table(['Ürün / Firma','Önceki fiyat','Yeni fiyat','Değişim','Onay'],state.alerts.map(a=>`<tr><td><strong>${esc(a.product)}</strong><small>${esc(a.unit)} · Kod: ${esc(a.code||'—')} · ${esc(a.matched_by||'')} · ${esc(a.firm)} · ${niceDate(a.issued)} · ${esc(a.number)}</small></td><td>${unitCurrency(a.old_price)}<small>${esc(a.old_product||a.product)}</small></td><td>${unitCurrency(a.price)}</td><td><span class="badge ${a.change>0?'late':''}">${a.change>0?'↑':'↓'} %${Math.abs(a.change).toLocaleString('tr-TR')}</span></td><td><button data-price-history="${a.history_id}">Fiyat geçmişi</button> ${a.approved?'<span class="badge">Onaylandı</span>':`<button data-approve="${a.fingerprint}" class="primary">Değişimi onayla</button> <button data-edit-invoice="${a.invoice_id}">Faturayı düzelt</button>`}</td></tr>`)):empty('Henüz fiyat değişimi yok.','Aynı firmanın aynı ürün ve ambalajını iki faturada kaydettiğinizde değişim burada görünür.');
  renderProductSuggestions();
  renderProductSearch();renderReviewQueue();
  $('#payment-list').innerHTML=state.payments.length?table(['Firma / Borç','Ödeme tarihi','Ödenen',''],state.payments.map(p=>{const target=p.target_type==='invoice'?state.invoices.find(i=>i.id===p.target_id):state.openings.find(i=>i.id===p.target_id);return `<tr><td>${esc(target?.firm||'')}<small>${esc(p.target_type==='invoice'?(target?.number||'Fatura'):(target?.reference||'Açılış bakiyesi'))}</small></td><td>${niceDate(p.paid_on)}</td><td>${currency(p.amount)}</td><td><button data-edit-payment="${p.id}" data-payment-type="${p.target_type}">Düzelt</button> <button data-delete-payment="${p.id}" data-payment-type="${p.target_type}" class="delete-button">Sil</button></td></tr>`;})):empty('Henüz ödeme kaydı yok.','Fatura veya açılış bakiyesi satırındaki “Ödeme kaydet” düğmesini kullanın.');
}
async function refresh(){state=await api('state');render();}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-tab]').forEach(t=>t.classList.toggle('active',t===b));
  ['overview','invoices','firms','products','prices','review'].forEach(id=>$('#'+id).hidden=id!==b.dataset.tab);
  const titles={overview:['Hesabınız net olsun.','Ödemelerinizi planlayın, işinize odaklanın.'],invoices:['Faturalarınız.','Her faturanın vadesi, her ödemenin kaydı.'],firms:['İş ortaklarınız.','Firma firma, tüm cari hesaplarınız.'],products:['Ürün fiyatlarını bulun.','Kod veya adla geçmiş alışları inceleyin.'],prices:['Fiyatları gözden kaçırmayın.','Faturalarınız arasındaki birim fiyat değişimleri.'],review:['Kontrol listeniz.','Kararınızı bekleyen kayıtları tek yerde inceleyin.']};
  $('#title').textContent=titles[b.dataset.tab][0];$('#subtitle').textContent=titles[b.dataset.tab][1];
});
document.querySelectorAll('.close').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('#agenda-filter').onchange=render;$('#search').oninput=render;
$('#product-search').oninput=renderProductSearch;$('#prepare-statement').onclick=renderFirmStatement;
$('#calendar-prev').onclick=()=>moveCalendar(-1);$('#calendar-next').onclick=()=>moveCalendar(1);$('#calendar-today').onclick=()=>{calendarMonth=state.today.slice(0,7);renderCalendar();};
document.addEventListener('click',event=>{
  const firm=event.target.closest('[data-firm-history]'),price=event.target.closest('[data-price-history]');
  if(firm)showFirmHistory(Number(firm.dataset.firmHistory));
  if(price)showPriceHistory(price.dataset.priceHistory);
  if(event.target.closest('[data-open-prices]'))$('[data-tab="prices"]').click();
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-restore-backup]');if(!button)return;
  const name=$('#backup-file')?.value;if(!name)return;
  if(!window.confirm(name+' yedeği geri yüklenecek. Mevcut durum önce ayrıca yedeklenecek, ardından ekrandaki kayıtlar seçilen tarihe dönecek. Devam edilsin mi?'))return;
  button.disabled=true;
  try{await api('backups/restore',{name});calendarMonth=null;await refresh();toast('Yedek geri yüklendi. Kayıtlar seçilen tarihe döndürüldü.');}
  catch(error){toast(error.message);button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const choose=event.target.closest('[data-choose-backup]'),reset=event.target.closest('[data-reset-backup]');
  if(!choose&&!reset)return;
  const button=choose||reset;
  if(reset&&!window.confirm('Yedekler yeniden uygulamanın kendi backups klasörüne yazılacak. Mevcut yedek dosyaları silinmeyecek. Devam edilsin mi?'))return;
  button.disabled=true;
  try{await api('backups/directory',{action:choose?'choose':'reset'});await refresh();toast(choose?'Yedek klasörü değiştirildi ve yeni yedek oluşturuldu.':'Varsayılan yedek klasörüne dönüldü.');}
  catch(error){toast(error.message);button.disabled=false;}
});
$('#batch-invoice').onclick=()=>{renderBatchQueue();$('#batch-status').textContent='';$('#batch-dialog').showModal();};
$('#batch-files').onchange=async event=>{
  const files=[...event.target.files];event.target.value='';
  if(!files.length)return;
  if(files.length>10||files.some(file=>!isPdfFile(file))){$('#batch-status').textContent='En fazla 10 PDF seçin. Her PDF ayrı bir fatura olmalıdır.';return;}
  batchQueue=[];batchIndex=0;batchActive=false;$('#batch-status').textContent='PDF dosyaları hazırlanıyor…';$('#start-batch').disabled=true;
  try{
    for(const file of files){
      if(file.size>8*1024*1024)throw new Error(file.name+' 8 MB sınırını aşıyor.');
      $('#batch-status').textContent=file.name+' hazırlanıyor…';
      const rendered=await pdfPageImages(file,6),photos=rendered.photos;
      if(photos.reduce((sum,photo)=>sum+photoBytes(photo),0)>8*1024*1024)throw new Error(file.name+' görüntüye çevrildiğinde 8 MB sınırını aşıyor.');
      batchQueue.push({name:file.name,photos,textLayers:rendered.layers});renderBatchQueue();
    }
    $('#batch-status').textContent=batchQueue.length+' ayrı fatura sıraya alındı. Her birini kontrol edip kaydedin.';
  }catch(error){batchQueue=[];renderBatchQueue();$('#batch-status').textContent=error.message||'PDF sırası hazırlanamadı.';}
};
$('#start-batch').onclick=openBatchInvoice;
$('#new-firm').onclick=()=>{setMode('firm',null);$('#firm-form').reset();$('#firm-form .form-error').textContent='';$('#firm-dialog').showModal();};
function openAccountEntry(kind,id=null){
  if(!state.firms.length){toast('Önce firma ekleyin.');$('#new-firm').click();return;}
  const form=$('#account-entry-form'),entry=id?[...state.openings,...state.credits].find(value=>value.id===id):null;
  kind=entry?.kind||kind;form.reset();form.dataset.editId=id||'';form.elements.kind.value=kind;
  form.elements.firm_id.innerHTML=state.firms.map(firm=>`<option value="${firm.id}">${esc(firm.name)}</option>`).join('');
  form.elements.entry_date.value=entry?.entry_date||state.today;form.elements.due.value=entry?.due||state.today;
  form.elements.amount.value=entry?(entry.amount/100).toFixed(2):'';form.elements.reference.value=entry?.reference||'';
  if(entry)form.elements.firm_id.value=entry.firm_id;
  const credit=kind==='credit';form.querySelector('h2').textContent=(credit?'İade / alacak':'Açılış bakiyesi')+(id?' düzelt':' ekle');
  form.querySelector('.credit-field').hidden=!credit;form.querySelector('.opening-field').hidden=credit;form.elements.invoice_id.required=credit;form.elements.due.required=!credit;
  const fillInvoices=()=>{const firmId=Number(form.elements.firm_id.value);form.elements.invoice_id.innerHTML=state.invoices.filter(invoice=>invoice.firm_id===firmId&&(invoice.remaining>0||invoice.id===entry?.invoice_id)).map(invoice=>`<option value="${invoice.id}">${esc(invoice.number)} · Kalan ${currency(invoice.remaining+(invoice.id===entry?.invoice_id?entry.amount:0))}</option>`).join('');if(entry?.invoice_id)form.elements.invoice_id.value=entry.invoice_id;};
  fillInvoices();form.elements.firm_id.onchange=fillInvoices;
  form.querySelector('#account-entry-help').textContent=credit?'Tutar seçilen faturanın kalan borcundan düşer ve ödeme planı hemen güncellenir.':'Bu eski borç vade tarihinde ödeme ajandasında görünür ve ödeme kaydedildikçe azalır.';
  form.querySelector('.form-error').textContent='';form.querySelector('button.primary').textContent=id?'Düzeltmeyi kaydet':'Kaydet';$('#account-entry-dialog').showModal();
}
$('#new-opening').onclick=()=>openAccountEntry('opening');
$('#new-credit').onclick=()=>openAccountEntry('credit');
$('#new-invoice').onclick=()=>{
  if(!state)return;
  if(!state.firms.length){toast('Önce faturanın ait olduğu firmayı ekleyin.');$('[data-tab="firms"]').click();$('#new-firm').click();return;}
  activeImportCheck=null;lastOcrSupplier='';ocrGeneration++;setMode('invoice',null);$('#invoice-form').reset();$('#invoice-form .form-error').textContent='';selectedPhoto=null;selectedPhotos=[];pdfOcrPhotos.clear();pdfTextLayers.clear();delete $('#invoice-form').dataset.batchJob;
  $('#expected-item-count').required=true;
  [...$('#invoice-form').elements].forEach(e=>{delete e.dataset.userEdited;e.title='';});
  renderSelectedPhotos();$('#ocr-text').hidden=true;$('#copy-ocr').hidden=true;$('#ocr-review').hidden=true;$('#ocr-status').textContent='PDF veya fotoğraf isteğe bağlıdır. Okunan yazılar otomatik kaydedilmez.';
  $('#item-rows').innerHTML='';renderImportCheck();$('#due-preview').value='';
  $('#firm-select').innerHTML=state.firms.map(f=>`<option value="${f.id}">${esc(f.name)}</option>`).join('');
  $('#invoice-form [name=issued]').value=state.today;$('#invoice-dialog').showModal();
};
function updateDue(){const form=$('#invoice-form'),issued=form.elements.issued.value,days=form.elements.days.value;if(!issued||days==='')return;const d=new Date(issued+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+Number(days));if(Number.isNaN(d.getTime()))return;$('#due-preview').value=d.toISOString().slice(0,10);}
$('#invoice-form [name=issued]').oninput=updateDue;$('#invoice-form [name=days]').oninput=updateDue;
$('#due-preview').oninput=()=>{const issued=$('#invoice-form [name=issued]').value,due=$('#due-preview').value;if(issued&&due)$('#invoice-form [name=days]').value=Math.round((new Date(due)-new Date(issued))/86400000);};
$('#add-item').onclick=()=>{
  const row=document.createElement('div');row.className='item-row';
  row.innerHTML='<label class="item-product">Ürün adı<input class="product" aria-label="Ürün adı" placeholder="Marka, ürün ve gramaj" required maxlength="500"></label><label>Faturadaki sıra no<input class="row-number" aria-label="Faturadaki sıra no" type="number" min="1" max="500" step="1" placeholder="Örn. 1"></label><label>Ürün kodu<input class="code" aria-label="Ürün kodu" placeholder="Faturadaki kod" maxlength="100"></label><label>Miktar<input class="quantity" aria-label="Ürün miktarı" type="number" step="0.0001" min="0.0001" placeholder="Örn. 18" required></label><label>Birim / ambalaj<input class="unit" aria-label="Ambalaj ve fiyat birimi" placeholder="Adet / koli / kg" required maxlength="200"></label><label>KDV dahil son birim maliyet (TL)<input class="price" aria-label="KDV dahil son birim maliyet TL" type="number" step="0.01" min="0.01" placeholder="Otomatik hesaplanır" required></label><label>Faturada yazan Tutarı (TL)<input class="line-total" aria-label="Faturada yazan satır tutarı TL" type="number" step="0.01" min="0.01" placeholder="Örn. 473,49"></label><label>Faturada yazan KDV T. (TL)<input class="vat" aria-label="Faturada yazan KDV tutarı TL" type="number" step="0.01" min="0" placeholder="Örn. 4,72"></label><button type="button" aria-label="Ürün satırını sil">Sil</button>';
  function recalculatePrice(){
    const quantity=Number(row.querySelector('.quantity').value),net=Number(row.querySelector('.line-total').value),vat=Number(row.querySelector('.vat').value),gross=net+vat;
    if(Number.isFinite(quantity)&&quantity>0&&Number.isFinite(net)&&net>0&&Number.isFinite(vat)&&vat>=0){
      row.querySelector('.price').value=(gross/quantity).toFixed(2);
      const evidence=row.querySelector('.item-evidence');
      if(evidence)evidence.textContent='Miktar veya satır toplamı değişti. Son birim fiyat yeniden hesaplandı; kontrol edin.';
    }
  }
  row.querySelector('.quantity').oninput=recalculatePrice;
  row.querySelector('.line-total').oninput=recalculatePrice;
  row.querySelector('.vat').oninput=recalculatePrice;


  row.querySelector('.row-number').required=Boolean(activeImportCheck);row.querySelector('.line-total').required=Boolean(activeImportCheck);
  row.querySelector('button').onclick=()=>{row.remove();renderImportCheck();};$('#item-rows').append(row);
};
function photoBytes(value){
  const base64=value.split(',')[1]||'';
  return Math.floor(base64.length*3/4)-(base64.endsWith('==')?2:base64.endsWith('=')?1:0);
}
let pdfJsPromise=null;
function loadPdfJs(){
  if(!pdfJsPromise)pdfJsPromise=import('/vendor/pdfjs/pdf.min.mjs?v=4.10.38').then(pdfjs=>{
    pdfjs.GlobalWorkerOptions.workerSrc='/vendor/pdfjs/pdf.worker.min.mjs?v=4.10.38';
    return pdfjs;
  });
  return pdfJsPromise;
}
function fileDataUrl(file){
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('Dosya okunamadı.'));reader.readAsDataURL(file);});
}
function isPdfFile(file){return file.type==='application/pdf'||file.name.toLocaleLowerCase('tr').endsWith('.pdf');}
async function pdfPageImages(file,availablePages){
  const pdfjs=await loadPdfJs();
  const loading=pdfjs.getDocument({data:new Uint8Array(await file.arrayBuffer())});
  let pdfDocument;
  try{
    pdfDocument=await loading.promise;
    if(!pdfDocument.numPages)throw new Error('PDF içinde sayfa bulunamadı.');
    if(pdfDocument.numPages>availablePages)throw new Error(`PDF ${pdfDocument.numPages} sayfa. Toplam en fazla 6 sayfa ekleyebilirsiniz.`);
    const pages=[],layers=[];
    for(let number=1;number<=pdfDocument.numPages;number++){
      const page=await pdfDocument.getPage(number),base=page.getViewport({scale:1});
      const scale=Math.min(2400/base.width,3800/base.height);
      const viewport=page.getViewport({scale});
      const canvas=document.createElement('canvas');canvas.width=Math.ceil(viewport.width);canvas.height=Math.ceil(viewport.height);
      const context=canvas.getContext('2d',{alpha:false});
      if(!context)throw new Error('PDF sayfası görüntüye çevrilemedi.');
      context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);
      await page.render({canvasContext:context,viewport}).promise;
      // Keep the PDF render lossless. A second JPEG conversion before OCR can
      // erase thin table characters and cause otherwise readable rows to vanish.
      pages.push(canvas.toDataURL('image/png'));
      const content=await page.getTextContent(),words=[];
      for(const item of content.items||[]){
        const value=String(item.str||'');if(!value.trim())continue;
        const [left,baseline]=viewport.convertToViewportPoint(item.transform[4],item.transform[5]);
        const height=Math.max(1,Math.abs(Number(item.height||10)*scale));
        const width=Math.max(1,Math.abs(Number(item.width||value.length*5)*scale));
        for(const match of value.matchAll(/\S+/gu)){
          const start=match.index||0,end=start+match[0].length;
          words.push({text:match[0],left:Math.round(left+width*start/value.length),top:Math.round(baseline-height),width:Math.max(1,Math.round(width*(end-start)/value.length)),height:Math.round(height)});
        }
      }
      layers.push({width:canvas.width,height:canvas.height,words});
      page.cleanup();
    }
    return {photos:pages,layers};
  }catch(error){
    if(error?.name==='PasswordException')throw new Error('Şifreli PDF açılamıyor. Şifresiz bir kopyasını ekleyin.');
    if(error?.name==='InvalidPDFException')throw new Error('PDF dosyası bozuk veya geçersiz.');
    throw error;
  }finally{
    if(pdfDocument)await pdfDocument.destroy();else await loading.destroy();
  }
}
function renderBatchQueue(){
  $('#batch-list').innerHTML=batchQueue.length?batchQueue.map((entry,index)=>`<article class="${index===batchIndex&&batchActive?'active':''}"><span>${index+1}</span><div><b>${esc(entry.name)}</b><small>${entry.photos.length} sayfa · ${index<batchIndex?'Kaydedildi':index===batchIndex&&batchActive?'Sırada':'Bekliyor'}</small></div></article>`).join(''):'';
  $('#start-batch').disabled=!batchQueue.length||batchIndex>=batchQueue.length;
  $('#start-batch').textContent=batchActive?'Sıradaki faturayı yeniden aç':'İlk faturayı aç';
}
async function openBatchInvoice(){
  if(batchIndex>=batchQueue.length){batchActive=false;renderBatchQueue();toast('Toplu PDF sırası tamamlandı.');return;}
  if(!state.firms.length){$('#batch-dialog').close();$('[data-tab="firms"]').click();$('#new-firm').click();toast('Toplu faturaya başlamadan önce firmayı ekleyin.');return;}
  batchActive=true;const entry=batchQueue[batchIndex];$('#batch-dialog').close();$('#new-invoice').click();$('#invoice-form').dataset.batchJob='1';
  selectedPhotos=[...entry.photos];pdfOcrPhotos=new Set(selectedPhotos);pdfTextLayers=new Map(selectedPhotos.map((photo,index)=>[photo,entry.textLayers?.[index]||null]).filter(([,layer])=>layer));photoSelectionChanged();
  $('#ocr-status').textContent=`Toplu sıra ${batchIndex+1}/${batchQueue.length} · ${entry.name} hazırlandı. Fatura okunuyor…`;
  setTimeout(()=>$('#read-photo').click(),0);renderBatchQueue();
}
function loadOcrImage(photo){
  return new Promise((resolve,reject)=>{
    const image=new Image();
    image.onload=()=>resolve(image);
    image.onerror=()=>reject(new Error('Fotoğraf OCR için hazırlanamadı.'));
    image.src=photo;
  });
}
function longestActiveRun(values,minimum){
  let best=null,start=-1;
  for(let index=0;index<=values.length;index++){
    if(index<values.length&&values[index]){
      if(start<0)start=index;
    }else if(start>=0){
      const run={start,end:index,length:index-start};
      if(run.length>=minimum&&(!best||run.length>best.length))best=run;
      start=-1;
    }
  }
  return best;
}
function ocrDocumentBands(image){
  const scanWidth=Math.min(240,image.naturalWidth);
  const scanHeight=Math.max(1,Math.round(image.naturalHeight*scanWidth/image.naturalWidth));
  const canvas=document.createElement('canvas');canvas.width=scanWidth;canvas.height=scanHeight;
  const context=canvas.getContext('2d',{willReadFrequently:true});
  if(!context)throw new Error('Fotoğraf işlenemedi.');
  context.drawImage(image,0,0,scanWidth,scanHeight);
  const pixels=context.getImageData(0,0,scanWidth,scanHeight).data;
  const rowLight=[];
  for(let y=0;y<scanHeight;y++){
    let light=0;
    for(let x=0;x<scanWidth;x++){
      const offset=(y*scanWidth+x)*4;
      if(pixels[offset]*.299+pixels[offset+1]*.587+pixels[offset+2]*.114>=185)light++;
    }
    rowLight.push(light/scanWidth);
  }
  const windowSize=Math.max(1,Math.round(scanHeight*.004));
  const activeRows=rowLight.map((value,index)=>{
    let sum=0,count=0;
    for(let y=Math.max(0,index-windowSize);y<=Math.min(scanHeight-1,index+windowSize);y++){sum+=rowLight[y];count++;}
    return sum/count>=.53;
  });
  const gapAllowance=Math.max(2,Math.round(scanHeight*.008));
  const minimumHeight=Math.max(36,Math.round(scanHeight*.085));
  const rawBands=[];
  let start=-1,lastActive=-1;
  for(let y=0;y<=scanHeight;y++){
    if(y<scanHeight&&activeRows[y]){
      if(start<0)start=y;
      lastActive=y;
    }
    if(start>=0&&(y===scanHeight||y-lastActive>gapAllowance)){
      if(lastActive-start+1>=minimumHeight)rawBands.push({top:start,bottom:lastActive+1});
      start=-1;lastActive=-1;
    }
  }
  if(!rawBands.length)return [{x:0,y:0,width:image.naturalWidth,height:image.naturalHeight}];
  const scaleX=image.naturalWidth/scanWidth,scaleY=image.naturalHeight/scanHeight;
  return rawBands.map(band=>{
    const columnLight=[];
    for(let x=0;x<scanWidth;x++){
      let light=0,total=0;
      for(let y=band.top;y<band.bottom;y++){
        const offset=(y*scanWidth+x)*4;
        if(pixels[offset]*.299+pixels[offset+1]*.587+pixels[offset+2]*.114>=185)light++;
        total++;
      }
      columnLight.push(total?light/total>=.38:false);
    }
    const column=longestActiveRun(columnLight,Math.round(scanWidth*.42));
    const left=Math.max(0,(column?column.start:0)-2),right=Math.min(scanWidth,(column?column.end:scanWidth)+2);
    const top=Math.max(0,band.top-2),bottom=Math.min(scanHeight,band.bottom+2);
    return {x:Math.floor(left*scaleX),y:Math.floor(top*scaleY),width:Math.ceil((right-left)*scaleX),height:Math.ceil((bottom-top)*scaleY)};
  });
}
function renderOcrRegion(image,region){
  const scale=Math.min(2400/region.width,5200/region.height);
  const width=Math.max(1,Math.round(region.width*scale)),height=Math.max(1,Math.round(region.height*scale));
  const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
  const context=canvas.getContext('2d');
  if(!context)throw new Error('Fotoğraf işlenemedi.');
  context.fillStyle='#fff';context.fillRect(0,0,width,height);
  context.imageSmoothingEnabled=true;context.imageSmoothingQuality='high';
  if('filter' in context)context.filter='grayscale(1) contrast(1.15)';
  context.drawImage(image,region.x,region.y,region.width,region.height,0,0,width,height);
  const prepared=canvas.toDataURL('image/jpeg',.88);
  if(!prepared.startsWith('data:image/jpeg;base64,'))throw new Error('Fotoğraf dönüştürülemedi.');
  return prepared;
}
async function prepareOcrPhotos(photos,includeOriginals=false){
  try{
    const prepared=[];
    for(const photo of photos){
      const image=await loadOcrImage(photo);
      for(const region of ocrDocumentBands(image))prepared.push(renderOcrRegion(image,region));
    }
    // Two complementary readings recover thin characters that can disappear
    // during contrast preparation (for example a row number inside a grid).
    if(includeOriginals){
      const combined=[...photos,...prepared.filter(photo=>!photos.includes(photo))];
      if(combined.length<=6&&combined.reduce((sum,p)=>sum+photoBytes(p),0)<=8*1024*1024)return combined;
    }
    if(prepared.length&&prepared.length<=6&&prepared.reduce((sum,p)=>sum+photoBytes(p),0)<=8*1024*1024)return prepared;
    const fullPages=[];
    for(const photo of photos){
      const image=await loadOcrImage(photo);
      fullPages.push(renderOcrRegion(image,{x:0,y:0,width:image.naturalWidth,height:image.naturalHeight}));
    }
    if(fullPages.reduce((sum,p)=>sum+photoBytes(p),0)<=8*1024*1024)return fullPages;
    return photos;
  }catch{
    return photos;
  }
}
function renderSelectedPhotos(){
  selectedPhoto=selectedPhotos[0]||null;
  $('#photo-previews').innerHTML=selectedPhotos.map((photo,index)=>`<article class="photo-page"><img src="${esc(photo)}" alt="Fatura sayfa ${index+1}"><div><strong>Sayfa ${index+1}</strong><button type="button" data-remove-photo="${index}" aria-label="Sayfa ${index+1} fotoğrafını kaldır" class="delete-button">Kaldır</button></div></article>`).join('');
  $('#photo-count').textContent=selectedPhotos.length?selectedPhotos.length+' sayfa eklendi · En fazla 6 sayfa, toplam 8 MB.':'';
}
function photoSelectionChanged(){
  ocrGeneration++;
  $('.confirm input').checked=false;
  $('#ocr-text').hidden=true;$('#copy-ocr').hidden=true;$('#ocr-review').hidden=true;
  renderSelectedPhotos();
  $('#ocr-status').textContent=selectedPhotos.length+' sayfa eklendi. Tüm sayfaları okumak için “Faturayı oku ve alanları doldur” düğmesine basın. Önceden doldurulmuş alanları yeniden kontrol edin.';
}
$('#add-photo').onclick=()=>$('#photo').click();
$('#photo-previews').onclick=e=>{
  const button=e.target.closest('[data-remove-photo]');
  if(!button||photoLoading||$('#read-photo').disabled)return;
  const [removed]=selectedPhotos.splice(Number(button.dataset.removePhoto),1);
  pdfOcrPhotos.delete(removed);
  pdfTextLayers.delete(removed);
  photoSelectionChanged();
};
$('#photo').onchange=async e=>{
  const files=[...e.target.files];e.target.value='';
  if(!files.length)return;
  if(photoLoading||$('#read-photo').disabled){$('#ocr-status').textContent='Devam eden fotoğraf işleminin bitmesini bekleyin.';return;}
  const existingBytes=selectedPhotos.reduce((sum,p)=>sum+photoBytes(p),0);
  const imageTypes=['image/jpeg','image/png','image/webp'];
  if(existingBytes+files.reduce((sum,f)=>sum+f.size,0)>8*1024*1024||files.some(file=>!isPdfFile(file)&&!imageTypes.includes(file.type))){$('#ocr-status').textContent='Eklenemedi. PDF, JPEG, PNG veya WebP seçin; toplam dosya boyutu en fazla 8 MB olabilir. Mevcut sayfalarınız korundu.';return;}
  const generation=ocrGeneration;
  photoLoading=true;$('#add-photo').disabled=true;$('#read-photo').disabled=true;
  try{
    const added=[];const addedPdfPages=new Set();let pdfPages=0;
    for(const file of files){
      if(isPdfFile(file)){
        $('#ocr-status').textContent=`${file.name} PDF sayfaları hazırlanıyor…`;
        const rendered=await pdfPageImages(file,6-selectedPhotos.length-added.length),pages=rendered.photos;
        added.push(...pages);pages.forEach((page,index)=>{addedPdfPages.add(page);pdfTextLayers.set(page,rendered.layers[index]);});pdfPages+=pages.length;
      }else{
        if(selectedPhotos.length+added.length>=6)throw new Error('Toplam en fazla 6 sayfa ekleyebilirsiniz.');
        added.push(await fileDataUrl(file));
      }
    }
    if(generation!==ocrGeneration||!$('#invoice-dialog').open)return;
    let duplicate=false;const unique=[];
    for(const photo of added){if(selectedPhotos.includes(photo)||unique.includes(photo)){duplicate=true;}else{unique.push(photo);}}
    if(selectedPhotos.length+unique.length>6)throw new Error('Toplam en fazla 6 sayfa ekleyebilirsiniz.');
    if(existingBytes+unique.reduce((sum,p)=>sum+photoBytes(p),0)>8*1024*1024)throw new Error('PDF sayfaları görüntüye çevrildiğinde 8 MB sınırını aşıyor. Daha küçük bir PDF deneyin.');
    selectedPhotos.push(...unique);
    unique.filter(photo=>addedPdfPages.has(photo)).forEach(photo=>pdfOcrPhotos.add(photo));
    photoSelectionChanged();
    if(pdfPages)$('#ocr-status').textContent+=` PDF içinden ${pdfPages} sayfa hazırlandı.`;
    if(duplicate)$('#ocr-status').textContent+=' Aynı sayfa tekrar eklenmedi.';
  }catch(error){$('#ocr-status').textContent=(error?.message||'Dosya yüklenemedi.')+' Mevcut sayfalarınız korundu.';}
  finally{photoLoading=false;$('#add-photo').disabled=false;$('#read-photo').disabled=false;}
};

let ocrGeneration=0;
$('#read-photo').onclick=async()=>{
  if(photoLoading)return;
  if(!selectedPhoto){$('#ocr-status').textContent='Önce PDF veya fotoğraf ekleyin.';return;}
  const form=$('#invoice-form');
  if((form.elements.number.value||form.elements.total.value||$('#item-rows').children.length)&&!window.confirm('Dosyadan okunan bilgiler bu formdaki tarih, numara, toplam, firma ve ürünlerin yerine yazılacak. Kaydettiğiniz kayıt değişmez; önce kontrol edeceksiniz. Devam edilsin mi?'))return;
  const generation=++ocrGeneration, requestedPhoto=selectedPhoto;
  $('#read-photo').disabled=true;$('#add-photo').disabled=true;$('#ocr-status').textContent='Fatura okunuyor ve alanlar hazırlanıyor…';
  form.querySelector('button.primary').disabled=true;
  try{
    const originalPhotos=selectedPhotos.length?selectedPhotos:[requestedPhoto];
    const ocrPhotos=await prepareOcrPhotos(originalPhotos,originalPhotos.every(photo=>pdfOcrPhotos.has(photo)));
    if(generation!==ocrGeneration||selectedPhoto!==requestedPhoto||!$('#invoice-dialog').open)return;
    const textLayers=originalPhotos.map(photo=>pdfTextLayers.get(photo)).filter(Boolean);
    const result=await api('ocr',{photos:ocrPhotos,pdf_text_layers:textLayers});
    if(generation!==ocrGeneration||selectedPhoto!==requestedPhoto||!$('#invoice-dialog').open)return;
    const draft=result.draft;
    lastOcrSupplier=draft.supplier||'';
    $('#ocr-text').value=result.text;$('#ocr-text').hidden=false;$('#copy-ocr').hidden=false;
    for(const key of ['number','issued','total']){
      if(draft[key])form.elements[key].value=draft[key];
      else if(key==='issued'&&!form.dataset.editId&&!form.elements.issued.dataset.userEdited)form.elements.issued.value='';
      form.elements[key].title=draft.sources?.[key]?'Fotoğraftaki kaynak: '+draft.sources[key]:'Fotoğraftan okunamadı; alanı kontrol edin.';
    }
    if(draft.firm_id)form.elements.firm_id.value=draft.firm_id;
    $('#due-preview').value='';updateDue();
    if(draft.import_check){
      const detectedRows=draft.import_check.expected_rows||[],detectedCount=detectedRows.length?Math.max(...detectedRows):0;
      activeImportCheck={...draft.import_check,detected_count:detectedCount};
      $('#expected-item-count').value=detectedCount||'';
    }
    if(draft.items.length)$('#item-rows').innerHTML='';
    draft.items.forEach(item=>{
      $('#add-item').click();const row=$('#item-rows').lastElementChild;
      row.querySelector('.product').value=item.product;
      row.querySelector('.unit').value=item.unit;
      row.querySelector('.price').value=Number(item.price).toFixed(2);row.querySelector('.row-number').value=item.row_number||'';row.querySelector('.line-total').value=item.line_total||'';
      row.querySelector('.vat').value=item.vat||'0';row.querySelector('.quantity').value=item.quantity||'';row.querySelector('.code').value=item.code||'';
      row.dataset.sourceNet=item.net;row.dataset.sourceVat=item.vat;
      const evidence=document.createElement('small');evidence.className='item-evidence';evidence.textContent=`Kod: ${item.code} · Miktar: ${item.quantity} · Satır: ${item.net} + KDV ${item.vat} TL`;row.append(evidence);
      if(item.review){row.classList.add('needs-review');const review=document.createElement('label');review.className='item-review';const check=document.createElement('input');check.type='checkbox';check.required=true;review.append(check,document.createTextNode(item.review+' Bu satırı kontrol ettim.'));row.append(review);}
      row.title=`Ürün kodu: ${item.code} · Miktar: ${item.quantity} · Faturadaki birim fiyat: ${item.source_price} · KDV hariç satır: ${item.net} · KDV: ${item.vat}`;
    });
    renderImportCheck();$('.confirm input').checked=false;
    $('#ocr-review').hidden=false;
    $('#ocr-review').textContent=(draft.supplier?'Okunan firma: '+draft.supplier+'\n':'')+draft.warnings.join('\n');
    $('#ocr-status').textContent=`${draft.items.length} ürün satırı ve okunabilen fatura alanları dolduruldu. Henüz kaydedilmedi. Tüm sayfaların eksiksiz okunduğunu kontrol edin; boş alanları tamamlayın. Birim fiyatlar satır tutarı + KDV üzerinden hesaplandı.`;
  }catch(e){$('#ocr-status').textContent=e.message;}
  finally{$('#read-photo').disabled=false;$('#add-photo').disabled=false;form.querySelector('button.primary').disabled=false;}
};

document.addEventListener('click',async e=>{
  const pay=e.target.closest('[data-pay]'),payOpening=e.target.closest('[data-pay-opening]'),photo=e.target.closest('[data-photo]');
  if(pay||payOpening){setMode('payment',null);const opening=Boolean(payOpening);const i=opening?state.openings.find(i=>i.id===Number(payOpening.dataset.payOpening)):state.invoices.find(i=>i.id===Number(pay.dataset.pay));const f=$('#payment-form');f.reset();f.querySelector('.form-error').textContent='';paymentOptions(f);f.elements.target.value=(opening?'opening:':'invoice:')+i.id;f.elements.amount.value=(i.remaining/100).toFixed(2);f.elements.amount.max=(i.remaining/100).toFixed(2);f.elements.paid_on.value=state.today;f.elements.paid_on.max=state.today;$('#payment-description').textContent=`${i.firm} · ${opening?(i.reference||'Açılış bakiyesi'):i.number} · Kalan ${currency(i.remaining)}`;$('#payment-dialog').showModal();}
  if(photo){try{const r=await api('photo/'+photo.dataset.photo);$('#photo-pages').innerHTML=(r.photos||[r.photo]).map((p,index)=>`<p>Sayfa ${index+1}</p><img src="${esc(p)}" alt="Fatura sayfa ${index+1}" style="max-width:100%">`).join('');$('#photo-dialog').showModal();}catch(e){toast(e.message);}}
});
for(const [id,path] of [['firm','firms'],['invoice','invoices'],['payment','payments']]){
  $('#'+id+'-form').onsubmit=async e=>{
    e.preventDefault();const form=e.target,button=form.querySelector('button.primary'),error=form.querySelector('.form-error');error.textContent='';
    if((photoLoading||$('#read-photo').disabled)&&id==='invoice'){error.textContent='Fatura dosyasının hazırlanmasını bekleyin.';return;}
    const data=Object.fromEntries(new FormData(form));
    if(id==='payment'){
      const [targetType,targetId]=String(data.target||'').split(':');
      data.target_type=targetType;data.target_id=Number(targetId);
      if(targetType==='invoice')data.invoice_id=Number(targetId);
      delete data.target;
    }
    if(id==='invoice'){data.photo=selectedPhotos.length?null:selectedPhoto;data.photos=selectedPhotos;data.ocr_supplier=lastOcrSupplier;data.items=[...document.querySelectorAll('.item-row')].map(r=>({product:r.querySelector('.product').value,unit:r.querySelector('.unit').value,price:r.querySelector('.price').value,quantity:r.querySelector('.quantity').value,code:r.querySelector('.code').value,net:r.querySelector('.line-total').value,vat:r.querySelector('.vat').value,row_number:r.querySelector('.row-number').value,line_total:r.querySelector('.line-total').value}));}
    if(id==='invoice'){const errors=renderImportCheck();data.import_check=activeImportCheck;if(errors.length){error.textContent='Eksik veya tutarsız ürünlerle kaydedilemez. '+errors.join(' ');return;}}
    const editing=form.dataset.editId;
    if(editing)data.id=Number(editing);
    button.disabled=true;
    try{
      try{await api(path+(editing?'/edit':''),data);}
      catch(saveError){
        if(id!=='invoice'||!saveError.message.startsWith('MÜKERRER_ONAYI:'))throw saveError;
        const warning=saveError.message.slice('MÜKERRER_ONAYI:'.length);
        if(!window.confirm(warning))throw new Error('Mükerrer olabilecek fatura kaydedilmedi.');
        data.duplicate_confirmed=true;await api(path+(editing?'/edit':''),data);
      }
      form.closest('dialog').close();await refresh();
      if(id==='invoice'&&!editing&&batchActive&&form.dataset.batchJob){batchIndex++;renderBatchQueue();if(batchIndex<batchQueue.length){toast('Fatura kaydedildi. Sıradaki PDF açılıyor.');setTimeout(openBatchInvoice,250);}else{batchActive=false;renderBatchQueue();toast('Toplu PDF sırası tamamlandı.');}}
      else toast(editing?'Düzeltme kaydedildi; bakiyeler ve fiyat değişimleri güncellendi.':'Kaydınız oluşturuldu.');
    }
    catch(e){error.textContent=e.message;}finally{button.disabled=false;}
  };
}
$('#account-entry-form').onsubmit=async event=>{
  event.preventDefault();const form=event.target,button=form.querySelector('button.primary'),error=form.querySelector('.form-error');error.textContent='';
  const data=Object.fromEntries(new FormData(form)),editing=form.dataset.editId;if(editing)data.id=Number(editing);
  button.disabled=true;
  try{await api('account-entries'+(editing?'/edit':''),data);form.closest('dialog').close();await refresh();toast(editing?'Cari hareket düzeltildi; tüm bakiyeler güncellendi.':'Cari hareket kaydedildi; tüm bakiyeler güncellendi.');}
  catch(value){error.textContent=value.message;}finally{button.disabled=false;}
};
refresh().catch(e=>toast('Veriler yüklenemedi: '+e.message));
setInterval(()=>{if(!document.querySelector('dialog[open]'))refresh().catch(()=>{});},60000);

function setMode(kind,id){
  const form=$('#'+kind+'-form');
  form.dataset.editId=id||'';
  const nouns={firm:'Firma',invoice:'Fatura',payment:'Ödeme'};
  form.querySelector('h2').textContent=nouns[kind]+(id?' düzelt':kind==='payment'?' kaydet':' ekle');
  form.querySelector('button.primary').textContent=id?'Düzeltmeyi kaydet':kind==='invoice'?'Onayla ve faturayı kaydet':kind==='firm'?'Firmayı kaydet':'Ödemeyi kaydet';
}
function paymentOptions(form,type=null){
  form.elements.target.innerHTML=state.obligations.filter(i=>!type||(i.kind==='opening'?'opening':'invoice')===type).map(i=>`<option value="${i.kind==='opening'?'opening':'invoice'}:${i.id}">${esc(i.firm)} · ${esc(i.kind==='opening'?(i.reference||'Açılış bakiyesi'):i.number)}</option>`).join('');
}
document.addEventListener('click',async e=>{
  const firm=e.target.closest('[data-edit-firm]'),invoice=e.target.closest('[data-edit-invoice]'),entry=e.target.closest('[data-edit-entry]'),payment=e.target.closest('[data-edit-payment]'),approve=e.target.closest('[data-approve]');
  if(firm){
    $('#new-firm').click();const f=state.firms.find(f=>f.id===Number(firm.dataset.editFirm));setMode('firm',f.id);$('#firm-form [name=name]').value=f.name;
  }
  if(invoice){
    invoice.closest('#firm-history-dialog,#price-history-dialog')?.close();
    const i=state.invoices.find(i=>i.id===Number(invoice.dataset.editInvoice));$('#new-invoice').click();setMode('invoice',i.id);
    const form=$('#invoice-form');const metadata=JSON.parse(i.import_meta||'{}'),savedItems=state.items.filter(t=>t.invoice_id===i.id);if(metadata.expected_rows&&!metadata.line_total_kind&&savedItems.length&&savedItems.every(t=>t.source_net!==''&&t.source_vat!==''))metadata.line_total_kind='net';activeImportCheck=metadata.expected_rows?metadata:null;form.elements.firm_id.value=i.firm_id;form.elements.number.value=i.number;form.elements.issued.value=i.issued;form.elements.days.value=Math.round((new Date(i.due)-new Date(i.issued))/86400000);form.elements.total.value=(i.total/100).toFixed(2);$('#expected-item-count').value=metadata.expected_rows?.length?Math.max(...metadata.expected_rows):'';$('#expected-item-count').required=Boolean(metadata.expected_rows);updateDue();
    savedItems.forEach(t=>{$('#add-item').click();const row=$('#item-rows').lastElementChild;row.querySelector('.product').value=t.product;row.querySelector('.unit').value=t.unit;row.querySelector('.price').value=Number(t.unit_price||t.price/100).toFixed(2);row.querySelector('.row-number').value=t.row_number||'';row.querySelector('.line-total').value=metadata.line_total_kind==='net'?(t.source_net||t.line_total):(t.line_total||'');row.querySelector('.vat').value=t.source_vat||'0';row.querySelector('.quantity').value=t.quantity||'';row.querySelector('.code').value=t.code||'';if(t.source_net!==''&&t.source_vat!==''){row.dataset.sourceNet=t.source_net;row.dataset.sourceVat=t.source_vat;const evidence=document.createElement('small');evidence.className='item-evidence';evidence.textContent='Faturada yazan: '+t.source_net+' TL · KDV: '+t.source_vat+' TL';row.append(evidence);}});
    renderImportCheck();
    if(i.has_photo){
      photoLoading=true;form.querySelector('button.primary').disabled=true;
      try{const r=await api('photo/'+i.id);selectedPhotos=r.photos||[r.photo];pdfOcrPhotos.clear();pdfTextLayers.clear();renderSelectedPhotos();}
      catch(error){form.closest('dialog').close();toast('Fatura sayfaları yüklenemedi. Kaydı yeniden açın.');}
      finally{photoLoading=false;form.querySelector('button.primary').disabled=false;}
    }
  }
  if(entry){entry.closest('#firm-history-dialog')?.close();openAccountEntry(null,Number(entry.dataset.editEntry));}
  if(payment){
    payment.closest('#firm-history-dialog')?.close();
    const type=payment.dataset.paymentType||'invoice';const p=state.payments.find(p=>p.id===Number(payment.dataset.editPayment)&&p.target_type===type);const form=$('#payment-form');form.reset();setMode('payment',p.id);paymentOptions(form,p.target_type);form.elements.target.value=p.target_type+':'+p.target_id;form.elements.amount.value=(p.amount/100).toFixed(2);form.elements.amount.removeAttribute('max');form.elements.paid_on.value=p.paid_on;form.elements.paid_on.max=state.today;form.querySelector('.form-error').textContent='';$('#payment-description').textContent='Tutarı, tarihi veya aynı türde yanlış seçilen borcu düzeltebilirsiniz.';$('#payment-dialog').showModal();
  }
  if(approve){
    approve.disabled=true;
    try{await api('prices/approve',{fingerprint:approve.dataset.approve});await refresh();toast('Fiyat değişimi onaylandı.');}
    catch(error){toast(error.message);approve.disabled=false;}
  }
});

$('#payment-form [name=target]').onchange=()=>{
  const form=$('#payment-form'),[type,id]=form.elements.target.value.split(':'),i=type==='opening'?state.openings.find(i=>i.id===Number(id)):state.invoices.find(i=>i.id===Number(id));
  const old=state.payments.find(p=>p.id===Number(form.dataset.editId)&&p.target_type===type);
  form.elements.amount.max=((i.remaining+(old&&old.target_id===i.id?old.amount:0))/100).toFixed(2);
};

document.addEventListener('click',async e=>{
  const button=e.target.closest('[data-delete-firm],[data-delete-invoice],[data-delete-payment],[data-delete-entry]');
  if(!button)return;
  let path, data, message;
  if(button.dataset.deletePayment!==undefined){
    const type=button.dataset.paymentType||'invoice';const payment=state.payments.find(p=>p.id===Number(button.dataset.deletePayment)&&p.target_type===type);
    if(!payment)return;
    path='payments/delete';data={id:payment.id,target_type:type};
    message=currency(payment.amount)+' tutarındaki ödeme silinecek. İlgili borcun kalan tutarı bu kadar artacak. Onaylıyor musunuz?';
  }else if(button.dataset.deleteEntry!==undefined){
    const entry=[...state.openings,...state.credits].find(value=>value.id===Number(button.dataset.deleteEntry));if(!entry)return;
    path='account-entries/delete';data={id:entry.id};
    message=(entry.kind==='opening'?'Açılış bakiyesi':'İade / alacak')+' kaydı silinecek.'+(entry.kind==='opening'&&entry.paid?' Bağlı ödemeler de silinecek.':'')+' Cari ve ödeme planı yeniden hesaplanacak. Onaylıyor musunuz?';
  }else{
    const firmMode=button.dataset.deleteFirm!==undefined;
    const id=Number(firmMode?button.dataset.deleteFirm:button.dataset.deleteInvoice);
    const record=(firmMode?state.firms:state.invoices).find(r=>r.id===id);
    if(!record)return;
    const invoices=state.invoices.filter(i=>firmMode?i.firm_id===id:i.id===id);
    const ids=new Set(invoices.map(i=>i.id));
    const payments=state.payments.filter(p=>ids.has(p.invoice_id));
    const items=state.items.filter(t=>ids.has(t.invoice_id));
    const entries=[...state.openings,...state.credits].filter(entry=>firmMode?entry.firm_id===id:entry.invoice_id===id);
    const openingIds=new Set(entries.filter(entry=>entry.kind==='opening').map(entry=>entry.id));
    const openingPayments=state.payments.filter(payment=>payment.target_type==='opening'&&openingIds.has(payment.target_id));
    path=firmMode?'firms/delete':'invoices/delete';
    data={id,expected:{invoices:invoices.length,payments:payments.length,items:items.length,entries:entries.length,opening_payments:openingPayments.length}};
    message=(firmMode?record.name+' firması':record.number+' faturası')+' silinecek.\nBağlı '+invoices.length+' fatura, '+payments.length+' fatura ödemesi, '+entries.length+' açılış/iade kaydı, '+openingPayments.length+' açılış ödemesi ve '+items.length+' ürün satırı da silinecek.\nCari toplamlar ve fiyat karşılaştırmaları yeniden hesaplanacak. Bu işlem ekrandan geri alınamaz. Onaylıyor musunuz?';
  }
  if(!window.confirm(message))return;
  button.disabled=true;
  try{button.closest('#firm-history-dialog')?.close();await api(path,data);await refresh();toast('Kayıt silindi; ödeme ve cari toplamları güncellendi.');}
  catch(error){toast(error.message);button.disabled=false;}
});

$('#invoice-form').addEventListener('input',event=>{event.target.dataset.userEdited='1';if(event.target!==$('.confirm input'))$('.confirm input').checked=false;renderImportCheck();});

function renderImportCheck(){
  const panel=$('#item-check');
  const countField=$('#expected-item-count'),rawCount=countField.value.trim(),enteredCount=Number(rawCount),countErrors=[];let countNote='';
  if(rawCount&&Number.isInteger(enteredCount)&&enteredCount>=1&&enteredCount<=500){
    const detected=Number(activeImportCheck?.detected_count||0);
    activeImportCheck={...(activeImportCheck||{}),detected_count:detected,expected_rows:Array.from({length:enteredCount},(_,index)=>index+1),line_total_kind:'net'};
    if(enteredCount<detected)countNote=`OCR en büyük sıra numarasını ${detected} okudu; siz ${enteredCount} girdiniz. Faturadaki son sıra numarasını esas alın.`;
  }else if(rawCount||activeImportCheck){countErrors.push('Faturadaki toplam ürün kalemini 1 ile 500 arasında tam sayı olarak girin.');}
  panel.hidden=!activeImportCheck&&!rawCount;
  if(panel.hidden)return [];
  const rows=[...document.querySelectorAll('.item-row')],expected=activeImportCheck?.expected_rows||[],numbers=rows.map(r=>Number(r.querySelector('.row-number').value)),errors=[...countErrors];
  rows.forEach(row=>{row.querySelector('.row-number').required=true;row.querySelector('.line-total').required=true;row.querySelector('.vat').required=true;});
  const missing=expected.filter(n=>!numbers.includes(n));
  if(!expected.length)errors.push('Faturanın kalem sayısı belirlenemedi. Yeniden okutun.');
  if(missing.length)errors.push('Eksik sıra no: '+missing.join(', ')+'.');
  if(new Set(numbers).size!==numbers.length)errors.push('Tekrarlanan sıra numarası var.');
  if(numbers.some(n=>!expected.includes(n)))errors.push('Sıra numaraları faturayla uyuşmuyor.');
  let total=0;
  rows.forEach((row,index)=>{
    const quantity=Number(row.querySelector('.quantity').value),price=Number(row.querySelector('.price').value),line=Number(row.querySelector('.line-total').value),vat=Number(row.querySelector('.vat').value),gross=line+vat;
    if(!Number.isFinite(quantity)||quantity<=0||!Number.isFinite(price)||price<=0||!Number.isFinite(line)||line<=0||!Number.isFinite(vat)||vat<0)errors.push((index+1)+'. ürünün miktar, fiyat, faturadaki tutar ve KDV alanlarını doldurun.');
    else{total+=Math.round(gross*100);if(Math.abs(quantity*price-gross)>quantity*.005+.020001)errors.push((index+1)+'. ürünün miktar × KDV dahil maliyeti, faturadaki tutar + KDV ile uyuşmuyor.');}
  });
  const target=Math.round(Number($('#invoice-form [name=total]').value)*100);
  if(!target||Math.abs(total-target)>2)errors.push('Ürün satır toplamları ile fatura toplamı uyuşmuyor.');
  panel.classList.toggle('check-error',errors.length>0);
  panel.textContent='Faturada beklenen: '+expected.length+' kalem · Listedeki: '+rows.length+' kalem · Tutar + KDV toplamı: '+currency(total)+'\n'+(countNote?countNote+'\n':'')+(errors.length?errors.join('\n'):'Kalem sayısı ile faturada yazan tutar ve KDV toplamları uyumlu. Ürün adlarını, kodlarını ve miktarları ayrıca fotoğraftan doğrulayın.');
  return errors;
}

function renderProductSuggestions(){
  let container=$('#product-matches');
  if(!container){container=document.createElement('div');container.id='product-matches';container.className='panel';$('#price-list').before(container);}
  const suggestions=state.product_suggestions||[],unmatched=state.unmatched_products||[],history=state.product_match_history||[];
  container.hidden=!(suggestions.length||unmatched.length||history.length);
  container.innerHTML=(suggestions.length?'<h2>Bu ürünler aynı mı?</h2><p class="muted">Belirsiz eşleşmeler onayınız olmadan fiyat karşılaştırmasına alınmaz. Kod, ürün, gramaj ve birimi kontrol edin.</p>'+table(['Yeni faturadaki ürün','Önceki faturadaki ürün','Karar'],suggestions.map(s=>`<tr><td><strong>${esc(s.current.product)}</strong><small>Kod: ${esc(s.current.code||'Yok')} · ${esc(s.current.unit)} · ${currency(s.current.price)} · ${esc(s.current.firm)} · ${esc(s.current.number)}</small></td><td><strong>${esc(s.previous.product)}</strong><small>Kod: ${esc(s.previous.code||'Yok')} · ${esc(s.previous.unit)} · ${currency(s.previous.price)} · ${esc(s.previous.number)}</small></td><td><button data-match-product="${s.fingerprint}" data-decision="same">Aynı ürün</button> <button data-match-product="${s.fingerprint}" data-decision="different">Farklı ürün</button></td></tr>`)):'')+(unmatched.length?'<details class="tip"><summary>'+unmatched.length+' ürünün geçmiş fiyatı eşleşmedi</summary><p>Yeni ürün veya okunamayan kod/ad olabilir. Bu ürünlerde fiyat değişmediği varsayılmaz. Faturadan ad, kod ve birimi kontrol edin.</p>'+unmatched.map(i=>`<p>${esc(i.product)} · ${esc(i.code||'Kod yok')} · ${esc(i.firm)} · ${esc(i.number)} <button data-edit-invoice="${i.invoice_id}">Faturayı düzelt</button></p>`).join('')+'</details>':'')+(history.length?'<details class="tip"><summary>Ürün eşleştirme kararları</summary>'+history.map(h=>`<p>${esc(h.left_name)} ↔ ${esc(h.right_name)} · ${h.decision==='same'?'Aynı ürün':'Farklı ürün'} <button data-unmatch-product="${h.fingerprint}">Kararı kaldır</button></p>`).join('')+'</details>':'');
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-match-product]');if(!button)return;
  if(button.dataset.decision==='same'&&!window.confirm('Bu iki kaydın aynı ürün, gramaj ve fiyat birimine ait olduğunu onaylıyor musunuz? Fiyat geçmişleri birlikte karşılaştırılacak.'))return;
  button.disabled=true;
  try{await api('products/match',{fingerprint:button.dataset.matchProduct,decision:button.dataset.decision});await refresh();toast('Ürün eşleştirme kararı kaydedildi.');}catch(error){toast(error.message);button.disabled=false;}
});

document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-unmatch-product]');if(!button)return;
  if(!window.confirm('Bu eşleştirme kararını kaldırmak istiyor musunuz? Fiyat karşılaştırmaları yeniden hesaplanacak.'))return;
  button.disabled=true;
  try{await api('products/unmatch',{fingerprint:button.dataset.unmatchProduct});await refresh();toast('Eşleştirme kararı kaldırıldı.');}catch(error){toast(error.message);button.disabled=false;}
});

$('#copy-ocr').onclick=async()=>{
  const field=$('#ocr-text');
  try{await navigator.clipboard.writeText(field.value);$('#ocr-status').textContent='Okunan metin kopyalandı. Hatalı alanları incelemek için sohbete yapıştırabilirsiniz.';}
  catch{field.focus();field.select();$('#ocr-status').textContent='Metin seçildi. Mac’te Command + C, Windows’ta Control + C ile kopyalayabilirsiniz.';}
};
