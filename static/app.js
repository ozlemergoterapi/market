let state, selectedPhoto = null, selectedPhotos = [], photoLoading = false, activeImportCheck = null;
let pdfOcrPhotos = new Set();
let pdfTextLayers = new Map();
let calendarDate = null;
let lastOcrSupplier = '', activeFirmHistoryId = null;
let ocrFirmHint = {};
let batchQueue = [], batchIndex = 0, batchActive = false;
const $ = s => document.querySelector(s);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const currency = cents => new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY'}).format(cents/100);
const unitCurrency = cents => new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY',minimumFractionDigits:2,maximumFractionDigits:4}).format(cents/100);
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
const draftKinds={'firm-form':'firm','invoice-form':'invoice','payment-form':'payment','account-entry-form':'entry'};
const draftTimers=new WeakMap(),draftLast=new Map(),draftWrites=new Map(),draftRestoreTokens=new WeakMap();
function formDraftKey(form){
  const kind=draftKinds[form?.id];
  if(!kind)return null;
  if(kind==='firm'&&form.dataset.fromInvoice==='1')return null;
  const edit=form.dataset.editId;
  if(kind==='invoice')return edit&&form.dataset.invoiceIdentity?'invoice:uid:'+form.dataset.invoiceIdentity:null;
  return kind+':'+(edit&&kind==='payment'?form.dataset.paymentType+':'+edit:edit||'new');
}
function formDraftSnapshot(form){
  const fields=[...form.elements].filter(element=>element.name).map(element=>({name:element.name,value:element.value,checked:element.type==='checkbox'||element.type==='radio'?element.checked:null}));
  const payload={fields};
  if(form.id==='invoice-form'){
    payload.layoutFirm=$('#layout-firm').value;payload.expectedCount=$('#expected-item-count').value;payload.activeImportCheck=activeImportCheck;payload.lastOcrSupplier=lastOcrSupplier;payload.ocrFirmHint=ocrFirmHint;
    payload.dueEdited=Boolean(form.elements.days.dataset.userEdited||$('#due-preview').dataset.userEdited);
    payload.photos=[...selectedPhotos];payload.pdfOcrIndexes=selectedPhotos.map((photo,index)=>pdfOcrPhotos.has(photo)?index:null).filter(index=>index!==null);
    payload.textLayers=selectedPhotos.map(photo=>pdfTextLayers.get(photo)||null);
    payload.items=[...$('#item-rows').children].map(row=>({product:row.querySelector('.product').value,unit:row.querySelector('.unit').value,price:row.querySelector('.price').value,rowNumber:row.querySelector('.row-number').value,lineTotal:row.querySelector('.line-total').value,vat:row.querySelector('.vat').value,quantity:row.querySelector('.quantity').value,giftQuantity:row.querySelector('.gift-quantity').value,giftOnly:row.querySelector('.gift-only').checked,code:row.querySelector('.code').value,sourceNet:row.dataset.sourceNet||'',sourceVat:row.dataset.sourceVat||'',review:row.dataset.review||''}));
  }
  return payload;
}
function applyDraftFields(form,fields=[]){
  fields.forEach(saved=>{const elements=[...form.elements].filter(element=>element.name===saved.name);elements.forEach(element=>{if(saved.checked!==null&&(element.type==='checkbox'||element.type==='radio'))element.checked=Boolean(saved.checked);else element.value=saved.value??'';});});
}
async function restoreFormDraft(form){
  const key=formDraftKey(form);if(!key)return;const token=(draftRestoreTokens.get(form)||0)+1;draftRestoreTokens.set(form,token);
  try{
    const result=await api('draft/'+encodeURIComponent(key));
    if(draftRestoreTokens.get(form)!==token||formDraftKey(form)!==key||!result.draft)return;
    const payload=result.draft.payload||{};applyDraftFields(form,payload.fields);
    if(form.id==='invoice-form'){
      if([...$('#layout-firm').options].some(option=>option.value===payload.layoutFirm))$('#layout-firm').value=payload.layoutFirm;
      activeImportCheck=payload.activeImportCheck||null;lastOcrSupplier=payload.lastOcrSupplier||'';ocrFirmHint=payload.ocrFirmHint||{};
      if(payload.dueEdited??Boolean(form.elements.days.value))form.elements.days.dataset.userEdited='1';
      $('#expected-item-count').value=payload.expectedCount||'';
      selectedPhotos=Array.isArray(payload.photos)?payload.photos:[];selectedPhoto=selectedPhotos[0]||null;pdfOcrPhotos=new Set((payload.pdfOcrIndexes||[]).map(index=>selectedPhotos[index]).filter(Boolean));pdfTextLayers=new Map(selectedPhotos.map((photo,index)=>[photo,payload.textLayers?.[index]]).filter(([,layer])=>layer));renderSelectedPhotos();
      $('#item-rows').innerHTML='';(payload.items||[]).forEach(item=>{$('#add-item').click();const row=$('#item-rows').lastElementChild;row.querySelector('.product').value=item.product||'';row.querySelector('.unit').value=item.unit||'';row.querySelector('.price').value=item.price||'';row.querySelector('.row-number').value=item.rowNumber||'';row.querySelector('.line-total').value=item.lineTotal||'';row.querySelector('.vat').value=item.vat||'';row.querySelector('.quantity').value=item.quantity||'';row.querySelector('.gift-quantity').value=item.giftQuantity||'';row.querySelector('.code').value=item.code||'';row.dataset.sourceNet=item.sourceNet||'';row.dataset.sourceVat=item.sourceVat||'';setGiftOnly(row,item.giftOnly===true);markItemReview(row,item.review);});
      updateDue();renderImportCheck();
    }
    if(form.id==='payment-form')updatePaymentLimit();
    draftLast.set(key,JSON.stringify(formDraftSnapshot(form)));toast('Kaydedilmemiş taslağınız otomatik geri getirildi.');
  }catch(error){toast('Taslak geri getirilemedi: '+error.message);}
}
function markItemReview(row,message){
  if(!message)return;
  row.dataset.review=message;
  row.classList.add('needs-review');
  const label=document.createElement('label'), checkbox=document.createElement('input');
  label.className='item-review';
  checkbox.type='checkbox';
  checkbox.required=true;
  label.append(checkbox,document.createTextNode(message+' Bu satırı kontrol ettim.'));
  row.append(label);
}
function enqueueDraftWrite(key,write){
  const pending=(draftWrites.get(key)||Promise.resolve()).catch(()=>{}).then(write);
  draftWrites.set(key,pending);
  const release=()=>{if(draftWrites.get(key)===pending)draftWrites.delete(key);};
  pending.then(release,release);
  return pending;
}
function scheduleFormDraft(form){
  const key=formDraftKey(form);
  if(!key||!form.closest('dialog[open]'))return;
  draftRestoreTokens.set(form,(draftRestoreTokens.get(form)||0)+1);
  clearTimeout(draftTimers.get(form));
  const payload=formDraftSnapshot(form),serialized=JSON.stringify(payload);
  draftTimers.set(form,setTimeout(()=>{
    enqueueDraftWrite(key,async()=>{
      if(draftLast.get(key)===serialized)return;
      await api('drafts',{key,payload});
      draftLast.set(key,serialized);
    }).catch(error=>toast('Otomatik taslak kaydedilemedi: '+error.message));
  },700));
}
function clearFormDraft(form,key=formDraftKey(form)){
  if(!key)return Promise.resolve();
  clearTimeout(draftTimers.get(form));
  return enqueueDraftWrite(key,async()=>{
    await api('drafts',{action:'clear',key});
    draftLast.delete(key);
  }).catch(error=>toast('Kayıt tamamlandı ancak taslak temizlenemedi: '+error.message));
}
let noticeTimer;
function toast(message){
  clearTimeout(noticeTimer);
  $('#notice').textContent=message;
  $('#notice').hidden=false;
  noticeTimer=setTimeout(()=>$('#notice').hidden=true,6000);
}
function empty(title,text){return `<div class="empty"><b>${esc(title)}</b>${esc(text)}</div>`;}
function table(head,rows){return `<table><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;}
function invoiceTable(list){
  if(!list.length)return empty('Burada henüz fatura yok.','Firma ve faturanızı eklediğinizde ödeme planınız burada görünecek.');
  return table(['Firma / Fatura','Ödeme tarihi','Planlanan kalan','Durum',''],list.map(i=>{
    const badge = !i.remaining ? ['','Ödendi'] : i.late_days ? ['late',`${i.late_days} gün gecikti`] : i.due===state.today ? ['today','Bugün'] : ['','Bekliyor'];
    const opening=i.kind==='opening';
    const detail=opening?'Açılış bakiyesi':`${esc(i.number)} · ${niceDate(i.issued)}`;
    const totalNote=opening?`Toplam ${currency(i.total)}`:`Toplam ${currency(i.total)}${i.credited?` · İade ${currency(i.credited)}`:''}`;
    const actions=opening?`<button data-edit-entry="${i.id}">Düzelt</button> <button data-delete-entry="${i.id}" class="delete-button">Sil</button> ${i.remaining?`<button data-pay-firm="${i.firm_id}">Ödeme kaydet</button>`:''}`:`<button data-edit-invoice="${i.id}">Düzelt</button> <button data-delete-invoice="${i.id}" class="delete-button">Sil</button> ${i.has_photo?`<button data-photo="${i.id}" aria-label="Fatura fotoğrafını aç">▧</button> `:''}${i.remaining?`<button data-pay-firm="${i.firm_id}">Ödeme kaydet</button>`:''}`;
    return `<tr><td><strong>${esc(i.firm)}</strong><small>${detail}</small></td><td>${niceDate(i.due)}</td><td><strong>${currency(i.remaining)}</strong><small>${totalNote}</small></td><td><span class="badge ${badge[0]}">${badge[1]}</span></td><td>${actions}</td></tr>`;
  }));
}
function calendarDay(date){return date.toISOString().slice(0,10);}
function calendarShift(date,days){return new Date(date.getTime()+days*86400000);}
function calendarMonthDate(year,month,day=1){
  const date=new Date(0);
  date.setUTCFullYear(year,month,day);
  return date;
}
function renderCalendar(){
  if(!state)return;
  calendarDate??=state.today;
  const mode=$('#calendar-mode').value;
  const anchor=new Date(calendarDate+'T00:00:00Z');
  const year=anchor.getUTCFullYear(),month=anchor.getUTCMonth();
  const monthStart=calendarMonthDate(year,month);
  const monthEnd=calendarMonthDate(year,month+1,0);
  const weekStart=calendarShift(anchor,-((anchor.getUTCDay()+6)%7));
  const first=mode==='month'?monthStart:mode==='week'?new Date(Math.max(weekStart,calendarMonthDate(1,0))):anchor;
  const last=mode==='month'?monthEnd:mode==='week'?new Date(Math.min(calendarShift(weekStart,6),calendarMonthDate(9999,11,31))):anchor;
  const periodStart=calendarDay(first),periodEnd=calendarDay(last);
  const periodName={day:'gün',week:'hafta',month:'ay'}[mode];
  $('#calendar-prev').ariaLabel='Önceki '+periodName;
  $('#calendar-next').ariaLabel='Sonraki '+periodName;
  $('#calendar-today').textContent={day:'Bugün',week:'Bu hafta',month:'Bu ay'}[mode];
  $('#calendar-date').value=calendarDate;
  $('#calendar-month').textContent=mode==='month'
    ?first.toLocaleDateString('tr-TR',{month:'long',year:'numeric',timeZone:'UTC'})
    :mode==='week'?niceDate(periodStart)+' – '+niceDate(periodEnd):niceDate(periodStart);
  const firmSelect=$('#calendar-firm'), selectedFirm=firmSelect.value;
  firmSelect.innerHTML='<option value="">Tüm firmalar</option>'+state.firms.map(firm=>`<option value="${firm.id}">${esc(firm.name)}</option>`).join('');
  if(state.firms.some(firm=>String(firm.id)===selectedFirm))firmSelect.value=selectedFirm;
  const matching=state.obligations.filter(item=>item.remaining>0&&(!firmSelect.value||String(item.firm_id)===firmSelect.value));
  const obligations=matching.filter(item=>item.due>=periodStart&&item.due<=periodEnd);
  const byDay=new Map(),byFirm=new Map();
  for(const item of obligations){
    if(!byDay.has(item.due))byDay.set(item.due,[]);
    byDay.get(item.due).push(item);
    byFirm.set(item.firm,(byFirm.get(item.firm)||0)+item.remaining);
  }
  const total=obligations.reduce((sum,item)=>sum+item.remaining,0);
  const overdue=obligations.filter(item=>item.due<state.today).reduce((sum,item)=>sum+item.remaining,0);
  const firmTotals=[...byFirm].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0],'tr'));
  $('#calendar-summary').innerHTML=`<div><small>Seçili dönemde kalan</small><strong>${currency(total)}</strong></div><div><small>Dönemdeki gecikmiş borç</small><strong>${currency(overdue)}</strong></div><div><small>Tüm gecikmiş borç</small><strong>${currency(matching.filter(item=>item.due<state.today).reduce((sum,item)=>sum+item.remaining,0))}</strong></div>`;
  $('#calendar-firms').hidden=!firmTotals.length;
  $('#calendar-firms').innerHTML=firmTotals.length?'<h4>Firma bazında kalan</h4>'+table(['Firma','Kalan borç'],firmTotals.map(([firm,amount])=>`<tr><td>${esc(firm)}</td><td>${currency(amount)}</td></tr>`)):'';

  const calendar=$('#payment-calendar');
  if(mode==='day'){
    calendar.innerHTML=obligations.length?invoiceTable(obligations):empty('Bu gün için açık ödeme yok.','');
    return;
  }
  const weekdays=['Pzt','Sal','Çar','Per','Cum','Cmt','Paz'];
  const cells=Array.from({length:mode==='month'?(first.getUTCDay()+6)%7:0},()=>'<div class="calendar-day empty-day" aria-hidden="true"></div>');
  for(let date=first;date<=last;date=calendarShift(date,1)){
    const iso=calendarDay(date),invoices=byDay.get(iso)||[];
    const amount=invoices.reduce((sum,item)=>sum+item.remaining,0);
    const label=mode==='week'?weekdays[(date.getUTCDay()+6)%7]+' '+date.getUTCDate():String(date.getUTCDate());
    const accounts=new Map();
    for(const item of invoices)accounts.set(item.firm,(accounts.get(item.firm)||0)+item.remaining);
    cells.push(`<button type="button" data-calendar-day="${iso}" aria-label="${niceDate(iso)} · ${currency(amount)}" class="calendar-day${iso===state.today?' today-cell':''}${invoices.length?' has-payment':''}"><span>${label}</span>${invoices.length?`<strong>${currency(amount)}</strong>${[...accounts].map(([firm,value])=>`<small>${esc(firm)} · ${currency(value)}</small>`).join('')}`:'<small>Ödeme yok</small>'}</button>`);
  }
  const heading=mode==='month'?'<div class="calendar-weekdays">'+weekdays.map(day=>`<b>${day}</b>`).join('')+'</div>':'';
  calendar.innerHTML=heading+`<div class="calendar-grid ${mode==='week'?'calendar-week':''}">${cells.join('')}</div>`;
}
function moveCalendar(offset){
  if(!state)return;
  const current=new Date((calendarDate||state.today)+'T00:00:00Z');
  const mode=$('#calendar-mode').value;
  let next;
  if(mode==='month'){
    const year=current.getUTCFullYear(),month=current.getUTCMonth()+offset;
    const last=calendarMonthDate(year,month+1,0).getUTCDate();
    next=calendarMonthDate(year,month,Math.min(current.getUTCDate(),last));
  }else{
    next=calendarShift(current,offset*(mode==='week'?7:1));
  }
  if(next.getUTCFullYear()<1||next.getUTCFullYear()>9999)return;
  calendarDate=calendarDay(next);renderCalendar();
}
function showFirmHistory(firmId){
  const firm=state.firms.find(entry=>entry.id===firmId);if(!firm)return;
  activeFirmHistoryId=firmId;
  const invoices=state.invoices.filter(invoice=>invoice.firm_id===firmId);
  const openings=state.openings.filter(entry=>entry.firm_id===firmId);
  const credits=state.credits.filter(entry=>entry.firm_id===firmId);
  const payments=state.payments.filter(payment=>payment.firm_id===firmId);
  const events=[...invoices.map(invoice=>({date:invoice.issued,type:'invoice',invoice})),...openings.map(entry=>({date:entry.entry_date,type:'opening',entry})),...credits.map(entry=>({date:entry.entry_date,type:'credit',entry})),...payments.map(payment=>({date:payment.paid_on,type:'payment',payment,invoice:payment.target_type==='invoice'?invoices.find(invoice=>invoice.id===payment.target_id):null,opening:payment.target_type==='opening'?openings.find(entry=>entry.id===payment.target_id):null}))].sort((a,b)=>b.date.localeCompare(a.date)||Number(b.type==='payment')-Number(a.type==='payment'));
  $('#firm-history-title').textContent=firm.name;
  $('#firm-history-summary').innerHTML=`<div><small>Fatura + açılış</small><strong>${currency(invoices.reduce((sum,invoice)=>sum+invoice.total,0)+openings.reduce((sum,entry)=>sum+entry.amount,0))}</strong></div><div><small>Ödeme + iade</small><strong>${currency(payments.reduce((sum,payment)=>sum+payment.amount,0)+credits.reduce((sum,entry)=>sum+entry.amount,0))}</strong></div><div><small>Kalan cari</small><strong>${currency(firm.balance)}</strong></div>`;
  $('#firm-history-list').innerHTML=events.length?events.map(event=>{
    if(event.type==='invoice')return `<article><time>${niceDate(event.date)}</time><div><b>Fatura · ${esc(event.invoice.number)}</b><p>Toplam ${currency(event.invoice.total)} · İade ${currency(event.invoice.credited||0)} · Kalan ${currency(event.invoice.remaining)} · Vade ${niceDate(event.invoice.due)}</p><button data-edit-invoice="${event.invoice.id}">Faturayı aç</button></div></article>`;
    if(event.type==='opening')return `<article><time>${niceDate(event.date)}</time><div><b>Açılış bakiyesi · ${currency(event.entry.amount)}</b><p>${esc(event.entry.reference||'Devir bakiyesi')} · Kalan ${currency(event.entry.remaining)} · Vade ${niceDate(event.entry.due)}</p><button data-edit-entry="${event.entry.id}">Düzelt</button> <button data-delete-entry="${event.entry.id}" class="delete-button">Sil</button></div></article>`;
    if(event.type==='credit')return `<article class="payment-event"><time>${niceDate(event.date)}</time><div><b>İade / alacak · ${currency(event.entry.amount)}</b><p>${esc(event.entry.invoice_number||'')} · ${esc(event.entry.reference||'Belge numarası yok')}</p><button data-edit-entry="${event.entry.id}">Düzelt</button> <button data-delete-entry="${event.entry.id}" class="delete-button">Sil</button></div></article>`;
    return `<article class="payment-event"><time>${niceDate(event.date)}</time><div><b>Ödeme · ${currency(event.payment.amount)}</b><p>${esc(event.invoice?.number||(event.payment.target_type==='opening'?(event.opening?.reference||'Açılış bakiyesi'):'Cari ödeme'))}</p><button data-edit-payment="${event.payment.id}" data-payment-type="${event.payment.target_type}">Ödemeyi aç</button></div></article>`;
  }).join(''):empty('Henüz hareket yok.','Bu firmaya ait fatura, açılış bakiyesi, iade veya ödeme bulunmuyor.');
  $('#statement-from').value=state.today.slice(0,4)+'-01-01';$('#statement-to').value=state.today;$('#firm-statement').hidden=true;
  $('#firm-history-dialog').showModal();
}
function renderFirmStatement(){
  const from=$('#statement-from').value,to=$('#statement-to').value,firm=state.firms.find(entry=>entry.id===activeFirmHistoryId);
  if(!firm||!from||!to||from>to){toast('Cari ekstre için geçerli başlangıç ve bitiş tarihleri seçin.');return;}
  const invoices=state.invoices.filter(invoice=>invoice.firm_id===firm.id);
  const openings=state.openings.filter(entry=>entry.firm_id===firm.id);
  const credits=state.credits.filter(entry=>entry.firm_id===firm.id);
  const payments=state.payments.filter(payment=>payment.firm_id===firm.id);
  const openingInvoices=invoices.filter(invoice=>invoice.issued<from).reduce((sum,invoice)=>sum+invoice.total,0)+openings.filter(entry=>entry.entry_date<from).reduce((sum,entry)=>sum+entry.amount,0);
  const openingPayments=payments.filter(payment=>payment.paid_on<from).reduce((sum,payment)=>sum+payment.amount,0)+credits.filter(entry=>entry.entry_date<from).reduce((sum,entry)=>sum+entry.amount,0);
  const periodInvoices=invoices.filter(invoice=>invoice.issued>=from&&invoice.issued<=to);
  const periodOpenings=openings.filter(entry=>entry.entry_date>=from&&entry.entry_date<=to);
  const periodCredits=credits.filter(entry=>entry.entry_date>=from&&entry.entry_date<=to);
  const periodPayments=payments.filter(payment=>payment.paid_on>=from&&payment.paid_on<=to);
  const opening=openingInvoices-openingPayments,invoiceTotal=periodInvoices.reduce((sum,invoice)=>sum+invoice.total,0)+periodOpenings.reduce((sum,entry)=>sum+entry.amount,0),paymentTotal=periodPayments.reduce((sum,payment)=>sum+payment.amount,0),creditTotal=periodCredits.reduce((sum,entry)=>sum+entry.amount,0),closing=opening+invoiceTotal-paymentTotal-creditTotal;
  const rows=[...periodInvoices.map(invoice=>({date:invoice.issued,label:'Fatura '+invoice.number,debit:invoice.total,credit:0})),...periodOpenings.map(entry=>({date:entry.entry_date,label:'Açılış bakiyesi · '+(entry.reference||'Devir'),debit:entry.amount,credit:0})),...periodCredits.map(entry=>({date:entry.entry_date,label:'İade / alacak · '+(entry.reference||entry.invoice_number||''),debit:0,credit:0,refund:entry.amount})),...periodPayments.map(payment=>({date:payment.paid_on,label:'Ödeme',debit:0,credit:payment.amount}))].sort((a,b)=>a.date.localeCompare(b.date)||b.debit-a.debit);
  let balance=opening;
  $('#firm-statement').hidden=false;
  $('#firm-statement').innerHTML=`<h3>${esc(firm.name)} · ${niceDate(from)} – ${niceDate(to)} cari ekstresi</h3><button type="button" id="print-statement">Ekstreyi yazdır</button><div class="history-summary"><div><small>Dönem başı borcu</small><strong>${currency(opening)}</strong></div><div><small>Dönem faturaları</small><strong>${currency(invoiceTotal)}</strong></div><div><small>Dönem ödemeleri</small><strong>${currency(paymentTotal)}</strong></div><div><small>Dönem iadeleri</small><strong>${currency(creditTotal)}</strong></div><div><small>Dönem sonu borcu</small><strong>${currency(closing)}</strong></div></div>${rows.length?table(['Tarih','Hareket','Borç','Ödeme','İade','Kalan borç'],rows.map(row=>{balance+=row.debit-row.credit-(row.refund||0);return `<tr><td>${niceDate(row.date)}</td><td>${esc(row.label)}</td><td>${row.debit?currency(row.debit):'—'}</td><td>${row.credit?currency(row.credit):'—'}</td><td>${row.refund?currency(row.refund):'—'}</td><td>${currency(balance)}</td></tr>`;})):empty('Bu dönemde hareket yok.','Seçilen tarihler arasında fatura veya ödeme bulunmuyor.')}`;
  $('#print-statement').onclick=()=>window.print();
}
function showPriceHistory(historyId){
  const history=(state.price_histories||[]).find(entry=>entry.id===historyId);if(!history)return;
  $('#price-history-title').textContent=history.product;
  $('#price-history-meta').textContent=history.firm+' · '+history.unit+' · Kod: '+(history.code||'—');
  $('#price-history-list').innerHTML=history.points.map((point,index)=>{const previous=history.points[index-1],change=previous?((point.price/previous.price-1)*100):null;return `<article><div><time>${niceDate(point.issued)}</time><small>${esc(point.number)} · ${esc(point.product)}</small></div><strong>${unitCurrency(point.price)}</strong>${change===null?'<span class="badge">İlk fiyat</span>':`<span class="badge ${change>0?'late':''}">${change>0?'↑':'↓'} %${Math.abs(change).toLocaleString('tr-TR',{maximumFractionDigits:2})}</span>`}<button data-edit-invoice="${point.invoice_id}">Faturayı aç</button></article>`;}).join('');
  $('#price-history-dialog').showModal();
}
let supplierGroups=null,supplierRequest=0;
const commonPriceCurrency=new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY',minimumFractionDigits:2,maximumFractionDigits:6});
function renderSupplierComparisons(){
  if(!supplierGroups)return;
  const query=$('#product-search').value.trim().toLocaleLowerCase('tr');
  const groups=supplierGroups.filter(group=>!query||group.suppliers.some(supplier=>
    (supplier.product+' '+supplier.code).toLocaleLowerCase('tr').includes(query)));
  const panel=$('#supplier-comparisons');
  if(!groups.length){
    panel.innerHTML=empty('Karşılaştırılabilir alış kaydı bulunamadı.','Aynı ürün en az iki firmada ve ortak ölçü biriminde kayıtlı olmalı.');
    return;
  }
  panel.innerHTML='<p class="muted">Her tedarikçinin son karşılaştırılabilir alış fiyatı gösterilir. Ambalajdaki adet belli olduğunda fiyat adet başına hesaplanır.</p>'+
    groups.map(group=>`<details class="panel supplier-group" ${groups.length===1?'open':''}><summary><strong>${esc(group.label)}</strong> <span class="muted">· ${group.suppliers.length} firma · ${esc(group.unit)} başına</span></summary><div class="supplier-table">${table(
      ['Tedarikçi','Son alış','Ürün / ambalaj','Alış birim fiyatı','Ortak birim fiyatı',''],
      group.suppliers.map(offer=>`<tr>
        <td><strong>${esc(offer.firm)}</strong>${offer.archived?'<small>Arşivde</small>':''}</td>
        <td>${niceDate(offer.issued)}<small>${esc(offer.number)}</small></td>
        <td>${esc(offer.product)}<small>${esc(offer.unit)}${offer.count>1?' · '+offer.count+' adet':''}${offer.code?' · Kod: '+esc(offer.code):''}</small></td>
        <td>${unitCurrency(Number(offer.unit_price)*100)}</td>
        <td><strong>${commonPriceCurrency.format(Number(offer.common_price))} / ${esc(group.unit)}</strong>${offer.lowest?'<small class="supplier-lowest">En düşük kayıtlı fiyat</small>':''}</td>
        <td><button data-edit-invoice="${offer.invoice_id}">Faturayı aç</button></td>
      </tr>`)
    )}</div></details>`).join('');
}
async function loadSupplierComparisons(){
  const version=++supplierRequest;
  const panel=$('#supplier-comparisons');
  supplierGroups=null;
  panel.textContent='Tedarikçilerin alış fiyatları hazırlanıyor…';
  try{
    const result=await api('supplier-comparisons');
    if(version!==supplierRequest)return;
    supplierGroups=result.groups;
    renderSupplierComparisons();
  }catch(error){
    if(version===supplierRequest)panel.textContent=error.message;
  }
}
const packageSelection=new Map();
const packageItemVersion=item=>JSON.stringify([item.invoice_id,item.issued,item.firm_id,item.product,item.unit,item.unit_price]);
let packageRequest=0;
const packageQuantity=new Intl.NumberFormat('tr-TR',{maximumFractionDigits:8});
function renderPackageSelection(){
  const items=state.items.filter(item=>!item.gift_only&&packageSelection.get(item.id)===packageItemVersion(item));
  if(items.length!==packageSelection.size){
    for(const id of packageSelection.keys())if(!items.some(item=>item.id===id))packageSelection.delete(id);
    ++packageRequest;
    $('#package-comparison-result').textContent='';
    $('#package-comparison-error').textContent='Seçilen alış kaydı değişti. Yeniden seçin.';
  }
  const labels=items.map(item=>`<li>${esc(item.product)} · ${esc(item.firm)} · ${esc(item.number)}
    <button type="button" data-package-pick="${item.id}">Kaldır</button></li>`);
  $('#package-selection').innerHTML=labels.length?`<ul>${labels.join('')}</ul><button type="button" id="package-compare" ${labels.length===2?'':'disabled'}>Karşılaştır</button>`
    :'<span>Karşılaştırmak için aşağıdaki listeden iki alış seçin.</span>';
  const submit=$('#package-compare');
  if(submit)submit.onclick=compareSelectedPackages;
}
async function compareSelectedPackages(){
  const version=++packageRequest,button=$('#package-compare'),error=$('#package-comparison-error'),result=$('#package-comparison-result');
  button.disabled=true;
  error.textContent='';
  result.innerHTML='';
  try{
    const comparison=await api('products/package-comparison',{item_ids:[...packageSelection.keys()]});
    if(version!==packageRequest)return;
    const difference=Number(comparison.difference),percent=Number(comparison.percent);
    const signed=difference>0?'+':'';
    result.innerHTML=`<div class="panel purchase-table">${table(
      ['Alış','Ürün / fatura','Ambalajın net miktarı','Alış fiyatı','Ortak birim fiyatı'],
      comparison.rows.map((row,index)=>`<tr><td>${index===0?'Önceki':'Yeni'} · ${niceDate(row.issued)}</td>
        <td>${esc(row.product)}<small>${esc(row.firm)} · ${esc(row.number)}</small><button data-edit-invoice="${row.invoice_id}">Faturayı aç</button></td>
        <td>${packageQuantity.format(Number(row.measure))} ${esc(comparison.unit)} / ${esc(row.unit)}</td>
        <td>${unitCurrency(Number(row.unit_price)*100)} / ${esc(row.unit)}</td>
        <td><strong>${unitCurrency(Number(row.normalized_price)*100)} / ${esc(comparison.unit)}</strong></td></tr>`)
    )}</div><p><strong>Ortak birimde fiyat farkı ${signed}${unitCurrency(difference*100)} / ${esc(comparison.unit)}</strong> · ${reportPercent.format(percent/100)}</p>`;
  }catch(value){
    if(version===packageRequest)error.textContent=value.message;
  }finally{
    if(version===packageRequest)button.disabled=false;
  }
}
$('#products').addEventListener('click',event=>{
  const pick=event.target.closest('[data-package-pick]');
  if(!pick)return;
  const id=Number(pick.dataset.packagePick);
  if(packageSelection.has(id))packageSelection.delete(id);
  else if(packageSelection.size<2){
    const item=state.items.find(row=>row.id===id&&!row.gift_only);
    if(item)packageSelection.set(id,packageItemVersion(item));
  }
  ++packageRequest;
  $('#package-comparison-result').textContent='';
  $('#package-comparison-error').textContent='';
  renderProductSearch();
});
function renderProductSearch(){
  const view=$('#product-view').value,comparing=view==='suppliers',packages=view==='packages';
  $('#product-search-results').hidden=comparing;
  $('#supplier-comparisons').hidden=!comparing;
  $('#open-package-comparison').hidden=!comparing;
  $('#package-comparison').hidden=!packages;
  if(comparing){renderSupplierComparisons();return;}
  if(!state)return;
  if(packages)renderPackageSelection();
  const query=$('#product-search').value.trim().toLocaleLowerCase('tr');
  const values=state.items.filter(item=>!item.gift_only&&(!query||(item.product+' '+(item.code||'')).toLocaleLowerCase('tr').includes(query))).sort((a,b)=>b.issued.localeCompare(a.issued)||b.id-a.id).slice(0,300);
  $('#product-search-results').innerHTML=values.length?table(['Ürün / Kod','Firma','Fatura tarihi','Alış fiyatı',''],values.map(item=>`<tr><td><strong>${esc(item.product)}</strong><small>Kod: ${esc(item.code||'—')} · ${esc(item.unit)}</small></td><td>${esc(item.firm)}</td><td>${niceDate(item.issued)}<small>${esc(item.number)}</small></td><td><strong>${unitCurrency(item.price)}</strong></td><td>${packages?`<button type="button" data-package-pick="${item.id}" ${packageSelection.size>=2&&!packageSelection.has(item.id)?'disabled':''}>${packageSelection.has(item.id)?'Kaldır':'Alışı seç'}</button>`
    :`<button data-edit-invoice="${item.invoice_id}">Faturayı aç</button>`}</td></tr>`)):empty(query?'Ürün bulunamadı.':'Henüz ürün yok.',query?'Adın veya kodun bir bölümünü deneyin.':'Fatura ürünleri kaydedildiğinde burada aranabilir.');
}
function renderReviewQueue(){
  const alerts=state.alerts.filter(alert=>!alert.approved),suggestions=state.product_suggestions||[],unmatched=state.unmatched_products||[];
  const count=alerts.length+suggestions.length+unmatched.length;
  $('#review-queue').innerHTML=count?`<div class="review-cards"><article><small>Fiyat değişimi</small><strong>${alerts.length}</strong></article><article><small>Ürün eşleştirmesi</small><strong>${suggestions.length}</strong></article><article><small>Eşleşmeyen ürün</small><strong>${unmatched.length}</strong></article></div>${alerts.length?`<div class="panel review-panel"><h3>Onay bekleyen fiyatlar</h3>${alerts.map(alert=>`<p><b>${esc(alert.product)}</b> · ${esc(alert.firm)} · ${unitCurrency(alert.old_price)} → ${unitCurrency(alert.price)} <button data-price-history="${alert.history_id}">Geçmiş</button> <button data-approve="${alert.fingerprint}" class="primary">Onayla</button></p>`).join('')}</div>`:''}${suggestions.length?`<div class="panel review-panel"><h3>Karar bekleyen ürünler</h3>${suggestions.map(value=>`<p><b>${esc(value.current.product)}</b> ↔ ${esc(value.previous.product)} · ${esc(value.current.firm)} <button data-open-prices>Karar ver</button></p>`).join('')}</div>`:''}${unmatched.length?`<div class="panel review-panel"><h3>Geçmişi eşleşmeyen ürünler</h3>${unmatched.map(item=>`<p><b>${esc(item.product)}</b> · ${esc(item.firm)} · ${esc(item.number)} <button data-edit-invoice="${item.invoice_id}">Faturayı kontrol et</button></p>`).join('')}</div>`:''}`:empty('Kontrol bekleyen kayıt yok.','Yeni fiyat veya belirsiz ürün çıktığında burada gösterilecek.');
}
let priceInvoiceId=null;
function showInvoicePriceNotice(invoiceId,count,hasNext){
  const notice=$('#invoice-price-notice');
  notice.hidden=!count;
  if(!count)return;
  $('#invoice-price-notice-text').textContent=`Bu faturada ${count} ürünün fiyatı değişti.`;
  $('#invoice-price-open').onclick=()=>{
    priceInvoiceId=invoiceId;
    $('#price-firm').value='';
    $('[data-tab=prices]').click();
    renderPriceChanges();
    $('#price-invoice-clear').focus();
  };
  const next=$('#invoice-price-continue');
  next.hidden=!hasNext;
  next.onclick=()=>{
    next.hidden=true;
    openBatchInvoice();
  };
}
$('#price-invoice-clear').onclick=()=>{
  priceInvoiceId=null;
  renderPriceChanges();
  $('#price-firm').focus();
};
function renderPriceChanges(){
  if(!state)return;
  const filter=$('#price-firm'),selected=filter.value;
  filter.innerHTML='<option value="">Tüm tedarikçiler</option>'+state.firms.map(firm=>
    `<option value="${firm.id}">${esc(firm.name)}${firm.archived?' (arşivde)':''}</option>`).join('');
  if(state.firms.some(firm=>String(firm.id)===selected))filter.value=selected;
  const firmId=Number(filter.value);
  const invoice=state.invoices.find(row=>row.id===priceInvoiceId);
  if(!invoice)priceInvoiceId=null;
  const invoiceFilter=$('#price-invoice-filter');
  invoiceFilter.hidden=!invoice;
  invoiceFilter.querySelector('span').textContent=invoice?`${invoice.number} · ${invoice.firm}`:'';
  filter.disabled=Boolean(invoice);
  if(!state.analysis_loaded){
    $('#price-list').textContent='Fiyat analizi hazırlanıyor…';
    renderProductSuggestions(firmId,Boolean(invoice));
    return;
  }
  const alerts=state.alerts.filter(alert=>invoice?alert.invoice_id===invoice.id:!firmId||alert.firm_id===firmId);
  $('#price-list').innerHTML=alerts.length?table(
    ['Ürün / Firma','Önceki fiyat','Yeni fiyat','Değişim','Fatura ve onay'],
    alerts.map(alert=>{
      // Prices are in cents, with up to two fractional digits of a cent.
      const difference=(Math.round(alert.price*100)-Math.round(alert.old_price*100))/100;
      const increase=difference>0;
      const percent=alert.change===0?'%0,01’den az':'%'+Math.abs(alert.change).toLocaleString('tr-TR');
      return `<tr>
        <td><strong>${esc(alert.product)}</strong><small>${esc(alert.unit)} · Kod ${esc(alert.code||'—')} · ${esc(alert.firm)}</small></td>
        <td>${unitCurrency(alert.old_price)}${alert.old_product!==alert.product?`<small>${esc(alert.old_product)}</small>`:''}</td>
        <td>${unitCurrency(alert.price)}</td>
        <td><span class="badge ${increase?'late':''}">${increase?'↑ +':'↓ −'}${unitCurrency(Math.abs(difference))}</span><small>${percent}</small></td>
        <td><strong>${esc(alert.number)}</strong><small>${niceDate(alert.issued)}</small>
          <button data-edit-invoice="${alert.invoice_id}">Faturayı aç</button>
          <button data-price-history="${alert.history_id}">Fiyat geçmişi</button>
          ${alert.approved?'<span class="badge">Onaylandı</span>':`<button data-approve="${alert.fingerprint}" class="primary">Değişimi onayla</button>`}
        </td>
      </tr>`;
    })
  ):empty(invoice?'Bu faturada fiyat değişimi yok.':firmId?'Bu tedarikçide fiyat değişimi yok.':'Henüz fiyat değişimi yok.',
          firmId?'Başka bir tedarikçi seçebilir veya tümünü gösterebilirsiniz.':'Aynı ürünün farklı faturalardaki fiyatı değiştiğinde burada görünür.');
  renderProductSuggestions(firmId,Boolean(invoice));
}
$('#price-firm').onchange=renderPriceChanges;
function refreshFirmFilter(firmFilter){
  const selected=firmFilter.value;
  firmFilter.innerHTML='<option value="">Tüm firmalar</option>'+state.firms.map(firm=>
    `<option value="${firm.id}">${esc(firm.name)}${firm.archived?' (arşivde)':''}</option>`).join('');
  if(state.firms.some(firm=>String(firm.id)===selected))firmFilter.value=selected;
}
function renderInvoiceList(){
  if(!state)return;
  const firmFilter=$('#invoice-firm');
  refreshFirmFilter(firmFilter);
  const search=$('#search').value.trim().toLocaleLowerCase('tr');
  const fromInput=$('#invoice-date-from'),toInput=$('#invoice-date-to');
  const from=fromInput.value,to=toInput.value,firmId=Number(firmFilter.value);
  const status=$('#invoice-filter-status'),list=$('#invoice-list');
  const invalidRange=!fromInput.validity.valid||!toInput.validity.valid||(from&&to&&from>to);
  list.hidden=Boolean(invalidRange);
  if(invalidRange){
    status.textContent='Geçerli bir tarih aralığı seçin. İlk tarih son tarihten sonra olamaz.';
    return;
  }
  const matches=state.invoices.filter(invoice=>
    (!firmId||invoice.firm_id===firmId)&&
    (!from||invoice.issued>=from)&&(!to||invoice.issued<=to)&&
    (invoice.firm+' '+invoice.number).toLocaleLowerCase('tr').includes(search));
  const total=matches.reduce((sum,invoice)=>sum+invoice.total,0);
  status.textContent=`${matches.length} fatura · Fatura toplamı ${currency(total)}`;
  list.innerHTML=matches.length?invoiceTable(matches):empty('Bu filtrelere uyan fatura yok.','');
}
function renderPaymentList(){
  if(!state)return;
  const firmFilter=$('#payment-firm');
  refreshFirmFilter(firmFilter);
  const fromInput=$('#payment-date-from'),toInput=$('#payment-date-to');
  const from=fromInput.value,to=toInput.value,firmId=Number(firmFilter.value);
  const status=$('#payment-filter-status'),list=$('#payment-list');
  const invalidRange=!fromInput.validity.valid||!toInput.validity.valid||(from&&to&&from>to);
  list.hidden=Boolean(invalidRange);
  if(invalidRange){
    status.textContent='Geçerli bir ödeme tarihi aralığı seçin. İlk tarih son tarihten sonra olamaz.';
    return;
  }
  const matches=state.payments.filter(payment=>
    (!firmId||payment.firm_id===firmId)&&
    (!from||payment.paid_on>=from)&&(!to||payment.paid_on<=to));
  const total=matches.reduce((sum,payment)=>sum+payment.amount,0);
  status.textContent=`${matches.length} ödeme · Ödenen toplam ${currency(total)}`;
  const names=new Map(state.firms.map(firm=>[firm.id,firm.name]));
  list.innerHTML=matches.length?table(['Firma','Ödeme tarihi','Ödenen',''],matches.map(payment=>`<tr>
    <td>${esc(names.get(payment.firm_id)||'')}<small>${payment.target_type==='firm'?'Cari ödeme':'Ödeme kaydı'}</small></td>
    <td>${niceDate(payment.paid_on)}</td>
    <td>${currency(payment.amount)}</td>
    <td><button data-edit-payment="${payment.id}" data-payment-type="${payment.target_type}">Düzelt</button>
      <button data-delete-payment="${payment.id}" data-payment-type="${payment.target_type}" class="delete-button">Sil</button></td>
  </tr>`)):empty('Bu filtrelere uyan ödeme yok.','');
}
function render(){
  const invoiceConfirmation=$('.confirm input');
  invoiceConfirmation.required=!state.desktop_invoice_confirmation;
  invoiceConfirmation.closest('label').hidden=Boolean(state.desktop_invoice_confirmation);
  let banner=$('#price-notification');
  if(!banner){banner=document.createElement('div');banner.id='price-notification';banner.className='tip';$('#overview').before(banner);}
  const pending=state.alerts.filter(a=>!a.approved).length;const matching=state.product_suggestions.length;
  banner.hidden=!(pending||matching)&&state.analysis_loaded;
  banner.innerHTML=`${state.analysis_loaded?`<b>${pending} fiyat değişimi · ${matching} ürün eşleştirmesi onay bekliyor.</b>`:''}<button id="review-prices">Fiyat değişimlerini incele</button>`;
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
  const latestBackup=backup.latest_at?new Date(backup.latest_at).toLocaleString('tr-TR',{timeZone:'Europe/Istanbul'}):backup.latest;
  $('#backup-status').innerHTML=`<b>Otomatik yedekleme açık</b><span>${backup.latest?`Son başarılı yedek: ${esc(latestBackup)} · Toplam ${backup.count} yedek`:'Henüz başarılı bir yedek yok.'}<br>Konum: ${esc(backup.directory||'')}${backup.error?'<br>Son yedekleme hatası: '+esc(backup.error):''}</span><div class="backup-actions"><button data-create-backup>Şimdi yedekle</button><button data-choose-backup>Klasörü değiştir</button><button data-reset-backup>Varsayılan klasöre dön</button></div>${backup.files?.length?`<div class="backup-restore"><select id="backup-file" aria-label="Geri yüklenecek yedek">${backup.files.map(file=>`<option value="${esc(file.name)}">${esc(file.name)} · ${(file.size/1048576).toLocaleString('tr-TR',{maximumFractionDigits:1})} MB</option>`).join('')}</select><button data-restore-backup>Seçili yedeği geri yükle</button></div>`:''}`;
  renderInvoiceList();
  const debtByFirm=new Map();
  for(const obligation of state.obligations){
    if(obligation.remaining<=0)continue;
    const amounts=debtByFirm.get(obligation.firm_id)||{overdue:0,today:0,future:0};
    const bucket=obligation.due<state.today?'overdue':obligation.due===state.today?'today':'future';
    amounts[bucket]+=obligation.remaining;
    debtByFirm.set(obligation.firm_id,amounts);
  }
  const firms=state.firms.filter(f=>Boolean(f.archived)===$('#show-archived-firms').checked);
  $('#firm-list').innerHTML=firms.length?firms.map(f=>`<article class="firm-card"><h2>${esc(f.name)}</h2><p>${f.balance<0?'Cari alacak':'Kalan cari borç'}</p><strong>${currency(Math.abs(f.balance))}</strong><dl class="firm-debt">
    <div><dt>Gecikmiş</dt><dd>${currency(debtByFirm.get(f.id)?.overdue||0)}</dd></div>
    <div><dt>Vadesi bugün</dt><dd>${currency(debtByFirm.get(f.id)?.today||0)}</dd></div>
    <div><dt>İleri vadeli</dt><dd>${currency(debtByFirm.get(f.id)?.future||0)}</dd></div>
  </dl>${f.tax_number?`<p>Vergi no ${esc(f.tax_number)}</p>`:''}${f.phone?`<p>Telefon <a href="tel:${esc(f.phone.replace(/[^0-9+]/g,''))}">${esc(f.phone)}</a></p>`:''}<p>Varsayılan vade: ${f.default_due_days==null?'—':f.default_due_days+' gün'}</p><p>Fatura tanıma: ${f.ocr_samples?`${f.ocr_samples} onaylı örnek`:'İlk örnek bekleniyor'}</p>${f.ocr_aliases?`<small>${esc(f.ocr_aliases)}</small>`:''}<label class="supplier-layout">Fatura sütun düzeni<select data-supplier-layout="${f.id}">${[['','Genel okuma'],['vat_net','KDV tutarı → KDV hariç satır tutarı'],['net_vat','KDV hariç satır tutarı → KDV tutarı']].map(([value,label])=>`<option value="${value}" ${f.ocr_amount_order===value?'selected':''}>${label}</option>`).join('')}</select></label><p><button data-firm-history="${f.id}" class="primary">Hesap geçmişi</button> ${f.balance>0?`<button data-pay-firm="${f.id}">Ödeme kaydet</button>`:''} <button data-edit-firm="${f.id}">Düzelt</button> ${state.firms.length>1?`<button data-merge-firm="${f.id}">Birleştir</button>`: ''} <button data-archive-firm="${f.id}">${f.archived?'Arşivden çıkar':'Arşivle'}</button></p></article>`).join(''):empty($('#show-archived-firms').checked?'Arşivde firma yok.':'Etkin firma yok.','');
  const entries=[...state.openings,...state.credits].sort((a,b)=>b.entry_date.localeCompare(a.entry_date)||b.id-a.id);
  $('#account-entry-list').innerHTML=entries.length?table(['Firma / Hareket','Tarih','Tutar / Kalan',''],entries.map(entry=>`<tr><td><strong>${esc(entry.firm)}</strong><small>${entry.kind==='opening'?'Açılış bakiyesi':'İade / alacak · Fatura '+esc(entry.invoice_number||'')}</small></td><td>${niceDate(entry.entry_date)}${entry.kind==='opening'?`<small>Vade ${niceDate(entry.due)}</small>`:''}</td><td><strong>${entry.kind==='credit'?'− ':''}${currency(entry.amount)}</strong>${entry.kind==='opening'?`<small>Kalan ${currency(entry.remaining)}</small>`:''}<small>${esc(entry.reference||'')}</small></td><td><button data-edit-entry="${entry.id}">Düzelt</button> <button data-delete-entry="${entry.id}" class="delete-button">Sil</button></td></tr>`)):empty('Henüz açılış veya iade kaydı yok.','Gerekirse yukarıdaki düğmelerden ekleyebilirsiniz.');
  renderPriceChanges();
  renderProductSearch();renderReviewQueue();
  renderPaymentList();
}
let activeReport='monthly', reportRequest=0, reportDatesInitialized=false;
const reportMonthName=new Intl.DateTimeFormat('tr-TR',{month:'long',year:'numeric',timeZone:'UTC'});
const reportPercent=new Intl.NumberFormat('tr-TR',{style:'percent',maximumFractionDigits:2,signDisplay:'exceptZero'});
const dateInputValue=input=>input.validity.valid?input.value:'';
function selectedReportRange(){
  const period=$('#report-period').value, today=state.today;
  const anchor=dateInputValue($('#report-anchor'));
  if(period==='custom'){
    const start=dateInputValue($('#report-start')),end=dateInputValue($('#report-end'));
    if(!start||!end||start>end)return null;
    const first=new Date(start+'T00:00:00Z'),last=new Date(end+'T00:00:00Z');
    const previousEnd=calendarShift(first,-1);
    const previousStart=calendarShift(previousEnd,-(last-first)/86400000);
    return reportDates(first,last,previousStart,previousEnd);
  }
  if(!anchor)return null;
  const day=new Date(anchor+'T00:00:00Z'),year=day.getUTCFullYear(),month=day.getUTCMonth();
  const asOf=new Date(today+'T00:00:00Z');
  if(period==='day'){
    const previous=calendarShift(day,-1);
    return reportDates(day,day,previous,previous);
  }
  if(period==='week'){
    const start=calendarShift(day,-((day.getUTCDay()+6)%7));
    const last=calendarShift(start,6);
    const end=start<=asOf&&asOf<last?asOf:last;
    return reportDates(start,end,calendarShift(start,-7),calendarShift(end,-7));
  }
  if(period==='year'){
    const start=calendarMonthDate(year,0),last=calendarMonthDate(year,11,31);
    const end=start<=asOf&&asOf<last?asOf:last;
    const endMonth=end.getUTCMonth();
    const previousDay=Math.min(end.getUTCDate(),calendarMonthDate(year-1,endMonth+1,0).getUTCDate());
    return reportDates(start,end,calendarMonthDate(year-1,0),calendarMonthDate(year-1,endMonth,previousDay));
  }
  const start=calendarMonthDate(year,month),last=calendarMonthDate(year,month+1,0);
  const previousStart=calendarMonthDate(year,month-1),previousLast=calendarMonthDate(year,month,0);
  if($('#report-comparison').value==='full')return reportDates(start,last,previousStart,previousLast);
  const count=Math.min(start<=asOf&&asOf<last?asOf.getUTCDate():last.getUTCDate(),previousLast.getUTCDate());
  return reportDates(start,calendarShift(start,count-1),previousStart,calendarShift(previousStart,count-1));
}
function reportDates(first,last,previousFirst,previousLast){
  const earliest=calendarMonthDate(1,0),latest=calendarMonthDate(9999,11,31);
  const start=calendarDay(new Date(Math.max(first,earliest)));
  const end=calendarDay(new Date(Math.min(last,latest)));
  if(previousFirst<earliest||previousLast<earliest)return {start,end};
  return {start,end,previous_start:calendarDay(previousFirst),previous_end:calendarDay(previousLast)};
}
function reportCard(label,amount,details=''){
  return `<article><span>${label}</span><strong>${currency(amount)}</strong>${details?`<small>${details}</small>`:''}</article>`;
}
function reportChange(amount,previous,hasPrevious){
  if(!hasPrevious)return '—';
  const difference=amount-previous;
  const percent=previous>0?reportPercent.format(difference/previous):'Yüzde hesaplanamadı';
  return `<strong>${difference>0?'+':''}${currency(difference)}</strong><small>${percent}</small>`;
}
function reportSupplier(firm){
  return `<button type="button" data-firm-history="${firm.id}">${esc(firm.name)}</button>${firm.archived?'<small>Arşivde</small>':''}`;
}
function reportTable(headers,rows){
  return rows.length?'<div class="panel purchase-table">'+table(headers,rows)+'</div>'
    :empty('Bu dönemde gösterilecek kayıt yok.','');
}
function renderReport(report,range){
  const current=report.current,previous=report.previous;
  const compared=Boolean(range.previous_start);
  const net=current.purchases-current.returns,previousNet=previous.purchases-previous.returns;
  let cards,body;
  if(activeReport==='purchases'){
    cards=reportCard('Toplam alış',current.purchases)+reportCard('İadeler',current.returns)+reportCard('Net alış',net);
    const rows=report.suppliers.filter(f=>f.current.purchases||f.current.returns||f.previous.purchases||f.previous.returns).map(f=>{
      const amount=f.current.purchases-f.current.returns,prior=f.previous.purchases-f.previous.returns;
      return `<tr><td>${reportSupplier(f)}</td><td>${currency(f.current.purchases)}</td><td>${currency(f.current.returns)}</td><td>${currency(amount)}</td><td>${compared?currency(prior):'—'}</td><td>${reportChange(amount,prior,compared)}</td></tr>`;
    });
    body=reportTable(['Tedarikçi','Alış','İade','Net alış','Önceki dönem net alış','Net değişim'],rows);
  }else if(activeReport==='payments'){
    cards=reportCard('Toplam ödenen',current.payments,`${current.payment_count} ödeme`);
    const rows=report.suppliers.filter(f=>f.current.payment_count).sort((a,b)=>b.current.payments-a.current.payments).map(f=>
      `<tr><td>${reportSupplier(f)}</td><td>${f.current.payment_count}</td><td>${currency(f.current.payments)}</td></tr>`);
    body=reportTable(['Tedarikçi','Ödeme sayısı','Toplam ödenen'],rows);
  }else if(activeReport==='debt'){
    const suppliers=report.suppliers.filter(f=>f.closing!==0).sort((a,b)=>b.closing-a.closing);
    cards=reportCard('Kalan cari borç',report.closing_debt)+reportCard('Cari alacak',report.closing_credit);
    body=reportTable(['Tedarikçi','Gecikmiş','O gün vadeli','İleri vadeli','Kalan borç','Cari alacak'],suppliers.map(f=>
      `<tr><td>${reportSupplier(f)}</td><td>${currency(f.overdue)}</td><td>${currency(f.due_today)}</td><td>${currency(f.future)}</td><td>${currency(Math.max(0,f.closing))}</td><td>${currency(Math.max(0,-f.closing))}</td></tr>`));
  }else{
    cards=reportCard('Dönem başı cari borç',report.opening_debt,report.opening_credit?`Cari alacak ${currency(report.opening_credit)}`:'')+
      reportCard('Dönem içi alış',current.purchases)+reportCard('Dönem içi iade',current.returns)+
      reportCard('Dönem içi ödeme',current.payments,`${current.payment_count} ödeme`)+
      reportCard('Dönem içi açılış kaydı',current.openings)+
      reportCard('Dönem sonu cari borç',report.closing_debt,report.closing_credit?`Cari alacak ${currency(report.closing_credit)}`:'');
    if(activeReport==='annual'){
      body=reportTable(['Ay','Alış','İade','Ödeme','Kalan cari borç','Cari alacak'],report.months.map(month=>
        `<tr><td>${reportMonthName.format(new Date(month.month+'-01T00:00:00Z'))}${month.month===range.end.slice(0,7)?`<small>${niceDate(range.end)} itibarıyla</small>`:''}</td><td>${currency(month.purchases)}</td><td>${currency(month.returns)}</td><td>${currency(month.payments)}</td><td>${currency(month.closing_debt)}</td><td>${currency(month.closing_credit)}</td></tr>`));
    }else{
      body=reportTable(['Gösterge','Seçili dönem','Önceki dönem','Değişim'],[
        ['Net alış',net,previousNet],['Ödenen tutar',current.payments,previous.payments]
      ].map(([label,value,prior])=>`<tr><td>${label}</td><td>${currency(value)}</td><td>${compared?currency(prior):'—'}</td><td>${reportChange(value,prior,compared)}</td></tr>`));
    }
  }
  $('#report-results').innerHTML=`<div class="cards">${cards}</div>${body}`;
}
async function loadReport(){
  if(!state)return;
  const version=++reportRequest, period=$('#report-period').value;
  const status=$('#report-status'),results=$('#report-results'),custom=period==='custom';
  $('#report-anchor-label').hidden=custom;
  $('#report-start-label').hidden=!custom;
  $('#report-end-label').hidden=!custom;
  $('#report-comparison-label').hidden=period!=='month';
  if(!reportDatesInitialized){
    $('#report-anchor').value=state.today;
    $('#report-start').value=state.today.slice(0,7)+'-01';
    $('#report-end').value=state.today;
    reportDatesInitialized=true;
  }
  const range=selectedReportRange();
  results.hidden=true;
  if(!range){
    status.textContent='Geçerli bir tarih aralığı seçin.';
    return;
  }
  status.textContent='Rapor hazırlanıyor…';
  try{
    const report=await api('reports?'+new URLSearchParams(range));
    if(version!==reportRequest)return;
    renderReport(report,range);
    const periodText=range.start===range.end?niceDate(range.end):niceDate(range.start)+' – '+niceDate(range.end);
    status.textContent=activeReport==='debt'?niceDate(range.end)+' itibarıyla cari borç':
      periodText+(range.previous_start&&['monthly','purchases'].includes(activeReport)
        ?' · Karşılaştırma '+niceDate(range.previous_start)+' – '+niceDate(range.previous_end):'');
    results.hidden=false;
  }catch(error){
    if(version===reportRequest)status.textContent=error.message;
  }
}
document.querySelectorAll('[data-report]').forEach(button=>button.onclick=()=>{
  activeReport=button.dataset.report;
  document.querySelectorAll('[data-report]').forEach(tab=>tab.setAttribute('aria-pressed',String(tab===button)));
  if(activeReport==='monthly'||activeReport==='annual')$('#report-period').value=activeReport==='monthly'?'month':'year';
  loadReport();
});
for(const id of ['report-period','report-anchor','report-start','report-end','report-comparison'])$('#'+id).onchange=loadReport;
let refreshVersion=0;
async function refresh(){
  const version=++refreshVersion;
  const analysis=['prices','review'].includes($('[data-tab].active').dataset.tab);
  const next=await api(analysis?'state':'overview');
  if(version===refreshVersion){
    state={...next,analysis_loaded:analysis};render();
    if($('#product-view').value==='suppliers'&&!$('#products').hidden)await loadSupplierComparisons();
    else {supplierGroups=null;++supplierRequest;}
    if(!$('#reports').hidden)await loadReport();
  }
}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-tab]').forEach(t=>t.classList.toggle('active',t===b));
  ['overview','invoices','firms','products','reports','prices','review'].forEach(id=>$('#'+id).hidden=id!==b.dataset.tab);
  const titles={overview:['Hesabınız net olsun.','Ödemelerinizi planlayın, işinize odaklanın.'],invoices:['Faturalarınız.','Her faturanın vadesi, her ödemenin kaydı.'],firms:['İş ortaklarınız.','Firma firma, tüm cari hesaplarınız.'],products:['Ürün fiyatlarını bulun.','Kod veya adla geçmiş alışları inceleyin.'],reports:['Raporlar','Alış, ödeme ve cari borç durumunu seçtiğiniz dönemde inceleyin.'],prices:['Fiyatları gözden kaçırmayın.','Faturalarınız arasındaki birim fiyat değişimleri.'],review:['Kontrol listeniz.','Kararınızı bekleyen kayıtları tek yerde inceleyin.']};
  $('#title').textContent=titles[b.dataset.tab][0];$('#subtitle').textContent=titles[b.dataset.tab][1];
  if(b.dataset.tab==='reports')loadReport();
  if(b.dataset.tab==='products'&&$('#product-view').value==='suppliers')loadSupplierComparisons();
  if(['prices','review'].includes(b.dataset.tab)&&!state?.analysis_loaded){
    const target=b.dataset.tab==='prices'?'#price-list':'#review-queue';
    $(target).textContent='Fiyat analizi hazırlanıyor…';
    refresh().catch(error=>{$(target).textContent=error.message;});
  }
});
document.querySelectorAll('.close').forEach(b=>b.onclick=()=>b.closest('dialog').close());
let mergeSource,mergeDetails,mergeRequest=0;
const mergeForm=$('#firm-merge-form'),mergeDialog=$('#firm-merge-dialog');
const mergeButton=mergeForm.querySelector('button.primary');
function openFirmMerge(source){
  mergeSource=source;
  $('#merge-source').textContent=source.name;
  $('#merge-target').innerHTML='<option value="">Firma seçin</option>'+state.firms.filter(f=>f.id!==source.id)
    .map(f=>`<option value="${f.id}">${esc(f.name)}${f.archived?' (arşivde)':''}</option>`).join('');
  mergeDialog.showModal();
  previewFirmMerge();
}
async function previewFirmMerge(){
  const version=++mergeRequest,target=Number($('#merge-target').value);
  mergeDetails=null;
  mergeButton.disabled=true;
  $('#merge-preview').hidden=true;
  $('#merge-error').textContent='';
  if(!target)return;
  try{
    const result=await api('firms/merge-preview',{source:mergeSource.id,target});
    if(version!==mergeRequest||!mergeDialog.open)return;
    mergeDetails=result;
    const counts=result.counts;
    const warnings=result.duplicates.length
      ?`<p class="form-error">Çakışan fatura numaraları: ${result.duplicates.map(esc).join(', ')}. Önce bu kayıtları kontrol edin.</p>`
      :'<p>İşlemden önce yerel yedek alınır. Değişen fiyat karşılaştırmaları yeniden kontrol isteyebilir.</p>';
    const phone=result.target.phone||result.source.phone;
    const taxConflict=result.source.tax_number&&result.target.tax_number&&result.source.tax_number!==result.target.tax_number;
    $('#merge-preview').innerHTML=`<dl>
      <dt>${esc(result.source.name)} bakiyesi</dt><dd>${currency(result.source.balance)}</dd>
      <dt>${esc(result.target.name)} bakiyesi</dt><dd>${currency(result.target.balance)}</dd>
      <dt>Birleşik cari bakiye</dt><dd><strong>${currency(result.balance)}</strong></dd>
    </dl><p>Taşınacak kayıtlar: ${counts.invoices} fatura, ${counts.payments} ödeme, ${counts.credits} iade ve ${counts.openings} açılış bakiyesi.</p>
    <p>${esc(result.target.name)} kartının vade ve okuma ayarları kullanılır.</p>${phone?`<p>Birleşik kartın telefonu <strong>${esc(phone)}</strong></p>`:''}${warnings}`;
    $('#merge-preview').hidden=false;
    if(taxConflict)$('#merge-error').textContent='Vergi numaraları farklı olan firmalar birleştirilemez.';
    mergeButton.disabled=result.duplicates.length>0||Boolean(taxConflict);
  }catch(error){
    if(version===mergeRequest)$('#merge-error').textContent=error.message;
  }
}
$('#merge-target').onchange=previewFirmMerge;
mergeForm.onsubmit=async event=>{
  event.preventDefault();
  if(!mergeDetails||mergeButton.disabled)return;
  const {source,target,token}=mergeDetails;
  mergeButton.disabled=true;
  $('#merge-target').disabled=true;
  mergeDialog.querySelector('.close').disabled=true;
  try{
    await api('firms/merge',{source:source.id,target:target.id,token});
    showUndo(null);
    mergeDialog.close();
    await refresh();
    toast(`${source.name} kayıtları ${target.name} kartında birleştirildi.`);
  }catch(error){
    await previewFirmMerge();
    $('#merge-error').textContent=error.message;
  }finally{
    $('#merge-target').disabled=false;
    mergeDialog.querySelector('.close').disabled=false;
  }
};
mergeDialog.addEventListener('cancel',event=>{
  if($('#merge-target').disabled)event.preventDefault();
});
$('#firm-list').onchange=async event=>{
  const select=event.target.closest('[data-supplier-layout]');
  if(!select)return;
  const firm=state.firms.find(entry=>entry.id===Number(select.dataset.supplierLayout));
  select.disabled=true;
  try{await api('firms/ocr-layout',{id:firm.id,order:select.value});await refresh();toast('Fatura sütun düzeni kaydedildi.');}
  catch(error){select.value=firm.ocr_amount_order;toast(error.message);}
  finally{select.disabled=false;}
};
$('#show-archived-firms').onchange=render;
$('#agenda-filter').onchange=render;
$('#search').oninput=renderInvoiceList;
for(const id of ['invoice-firm','invoice-date-from','invoice-date-to'])$('#'+id).onchange=renderInvoiceList;
$('#invoice-filters-clear').onclick=()=>{
  for(const id of ['search','invoice-firm','invoice-date-from','invoice-date-to'])$('#'+id).value='';
  renderInvoiceList();
};
for(const id of ['payment-firm','payment-date-from','payment-date-to'])$('#'+id).onchange=renderPaymentList;
$('#payment-filters-clear').onclick=()=>{
  for(const id of ['payment-firm','payment-date-from','payment-date-to'])$('#'+id).value='';
  renderPaymentList();
};
$('#product-view').onchange=()=>{
  renderProductSearch();
  if($('#product-view').value==='suppliers')loadSupplierComparisons();
};
$('#open-package-comparison').onclick=()=>{
  $('#product-view').value='packages';
  renderProductSearch();
  $('#product-search').focus();
};
$('#product-search').oninput=renderProductSearch;$('#prepare-statement').onclick=renderFirmStatement;
$('#calendar-prev').onclick=()=>moveCalendar(-1);
$('#calendar-next').onclick=()=>moveCalendar(1);
$('#calendar-today').onclick=()=>{if(state){calendarDate=state.today;renderCalendar();}};
$('#calendar-mode').onchange=renderCalendar;
$('#calendar-firm').onchange=renderCalendar;
$('#calendar-date').onchange=event=>{
  if(!event.target.validity.valid||!event.target.value)return;
  calendarDate=event.target.value;renderCalendar();
};
$('#payment-calendar').onclick=event=>{
  const day=event.target.closest('[data-calendar-day]');
  if(!day)return;
  calendarDate=day.dataset.calendarDay;
  $('#calendar-mode').value='day';
  renderCalendar();
  $('#calendar-mode').focus();
};
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
  try{await api('backups/restore',{name});calendarDate=null;showUndo(null);await refresh();toast('Yedek geri yüklendi. Kayıtlar seçilen tarihe döndürüldü.');}
  catch(error){toast(error.message);button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const choose=event.target.closest('[data-choose-backup]'),reset=event.target.closest('[data-reset-backup]'),create=event.target.closest('[data-create-backup]');
  if(!choose&&!reset&&!create)return;
  const button=choose||reset||create;
  if(reset&&!window.confirm('Yedekler yeniden uygulamanın kendi backups klasörüne yazılacak. Mevcut yedek dosyaları silinmeyecek. Devam edilsin mi?'))return;
  button.disabled=true;
  try{
    if(create){await api('backups/create',{});await refresh();toast('Yedek oluşturuldu.');return;}
    await api('backups/directory',{action:choose?'choose':'reset'});await refresh();toast(choose?'Yedek klasörü değiştirildi ve yeni yedek oluşturuldu.':'Varsayılan yedek klasörüne dönüldü.');}
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
function openFirmForm(fromInvoice=false){
  const form=$('#firm-form');
  setMode('firm',null);
  form.reset();
  form.dataset.fromInvoice=fromInvoice?'1':'';
  form.querySelector('.form-error').textContent='';
  if(fromInvoice){
    form.elements.name.value=ocrFirmHint.name||'';
    form.elements.tax_number.value=ocrFirmHint.taxNumber||'';
  }
  $('#firm-dialog').showModal();
  if(!fromInvoice)restoreFormDraft(form);
}
$('#new-firm').onclick=()=>openFirmForm();
$('#invoice-new-firm').onclick=()=>{
  if(photoLoading||$('#read-photo').disabled){toast('Fatura dosyasının hazırlanmasını bekleyin.');return;}
  openFirmForm(true);
};
function openAccountEntry(kind,id=null){
  if(!state.firms.some(f=>kind!=='opening'||!f.archived)&&!id){toast('Önce etkin bir firma seçin.');return;}
  const form=$('#account-entry-form'),entry=id?[...state.openings,...state.credits].find(value=>value.id===id):null;
  kind=entry?.kind||kind;form.reset();form.dataset.editId=id||'';form.elements.kind.value=kind;
  form.elements.firm_id.innerHTML=state.firms.filter(firm=>kind==='credit'||!firm.archived||firm.id===entry?.firm_id).map(firm=>`<option value="${firm.id}">${esc(firm.name)}</option>`).join('');
  form.elements.entry_date.value=entry?.entry_date||state.today;form.elements.due.value=entry?.due||state.today;
  form.elements.amount.value=entry?(entry.amount/100).toFixed(2):'';form.elements.reference.value=entry?.reference||'';
  if(entry)form.elements.firm_id.value=entry.firm_id;
  const credit=kind==='credit';form.querySelector('h2').textContent=(credit?'İade / alacak':'Açılış bakiyesi')+(id?' düzelt':' ekle');
  form.querySelector('.credit-field').hidden=!credit;form.querySelector('.opening-field').hidden=credit;form.elements.invoice_id.required=credit;form.elements.due.required=!credit;
  const fillInvoices=()=>{const firmId=Number(form.elements.firm_id.value);form.elements.invoice_id.innerHTML=state.invoices.filter(invoice=>invoice.firm_id===firmId&&(invoice.document_remaining>0||invoice.id===entry?.invoice_id)).map(invoice=>`<option value="${invoice.id}">${esc(invoice.number)} · Kalan ${currency(invoice.document_remaining+(invoice.id===entry?.invoice_id?entry.amount:0))}</option>`).join('');if(entry?.invoice_id)form.elements.invoice_id.value=entry.invoice_id;};
  fillInvoices();form.elements.firm_id.onchange=fillInvoices;
  form.querySelector('#account-entry-help').textContent=credit?'Tutar seçilen faturanın kalan borcundan düşer ve ödeme planı hemen güncellenir.':'Bu eski borç vade tarihinde ödeme ajandasında görünür ve ödeme kaydedildikçe azalır.';
  form.querySelector('.form-error').textContent='';form.querySelector('button.primary').textContent=id?'Düzeltmeyi kaydet':'Kaydet';$('#account-entry-dialog').showModal();restoreFormDraft(form);
}
$('#new-opening').onclick=()=>openAccountEntry('opening');
$('#new-credit').onclick=()=>openAccountEntry('credit');
function openInvoiceForm(existingFirmId=null){
  if(!state)return;
  activeImportCheck=null;lastOcrSupplier='';ocrFirmHint={};ocrGeneration++;setMode('invoice',null);$('#invoice-form').reset();$('#invoice-form .form-error').textContent='';selectedPhoto=null;selectedPhotos=[];pdfOcrPhotos.clear();pdfTextLayers.clear();delete $('#invoice-form').dataset.batchJob;
  photoLoading=false;$('#add-photo').disabled=false;$('#read-photo').disabled=false;$('#invoice-form button.primary').disabled=false;
  $('#expected-item-count').required=true;
  [...$('#invoice-form').elements].forEach(e=>{delete e.dataset.userEdited;e.title='';});
  renderSelectedPhotos();$('#ocr-text').value='';$('#ocr-text').hidden=true;$('#copy-ocr').hidden=true;$('#ocr-review').textContent='';$('#ocr-review').hidden=true;$('#ocr-status').textContent='PDF veya fotoğraf isteğe bağlıdır. Okunan yazılar otomatik kaydedilmez.';
  $('#item-rows').innerHTML='';renderImportCheck();$('#due-preview').value='';
  $('#firm-select').innerHTML=state.firms.filter(f=>!f.archived||f.id===existingFirmId).map(f=>`<option value="${f.id}">${esc(f.name)}</option>`).join('');
  $('#layout-firm').innerHTML='<option value="">Otomatik firma tanıma</option>'+state.firms.filter(f=>f.ocr_amount_order&&(!f.archived||f.id===existingFirmId)).map(f=>`<option value="${f.id}">${esc(f.name)}</option>`).join('');
  $('#layout-firm').closest('label').hidden=$('#layout-firm').options.length===1;
  if(existingFirmId&&state.firms.some(f=>f.id===existingFirmId&&f.ocr_amount_order))$('#layout-firm').value=String(existingFirmId);
  $('#invoice-form [name=issued]').value=state.today;$('#invoice-dialog').showModal();
  if(!existingFirmId)applyDefaultDue();
}
$('#invoice-dialog').addEventListener('close',event=>{
  if(event.currentTarget.open)return;
  ocrGeneration++;
  selectedPhoto=null;selectedPhotos=[];pdfOcrPhotos.clear();pdfTextLayers.clear();
  renderSelectedPhotos();
});
$('#new-invoice').onclick=()=>openInvoiceForm();
function applyDefaultDue(){
  const form=$('#invoice-form'),firm=state.firms.find(f=>f.id===Number(form.elements.firm_id.value));
  form.elements.days.value=firm?.default_due_days??'';
  delete form.elements.days.dataset.userEdited;
  delete $('#due-preview').dataset.userEdited;
  updateDue();
}
$('#firm-select').onchange=applyDefaultDue;
function updateDue(){const form=$('#invoice-form'),issued=form.elements.issued.value,days=form.elements.days.value;if(!issued||days===''){$('#due-preview').value='';return;}const d=new Date(issued+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+Number(days));if(Number.isNaN(d.getTime()))return;$('#due-preview').value=d.toISOString().slice(0,10);}
$('#invoice-form [name=issued]').oninput=updateDue;$('#invoice-form [name=days]').oninput=updateDue;
$('#due-preview').oninput=()=>{const issued=$('#invoice-form [name=issued]').value,due=$('#due-preview').value;if(issued&&due)$('#invoice-form [name=days]').value=Math.round((new Date(due)-new Date(issued))/86400000);};
function setGiftOnly(row,enabled){
  const checkbox=row.querySelector('.gift-only');
  checkbox.checked=enabled;
  const gift=row.querySelector('.gift-quantity');
  gift.required=enabled;
  gift.min=enabled?'0.0001':'0';
  for(const field of row.querySelectorAll('.quantity,.price,.line-total,.vat')){
    if(enabled)field.value='0';
    else if(field.readOnly)field.value='';
    field.readOnly=enabled;
  }
}
$('#add-item').onclick=()=>{
  const row=document.createElement('div');row.className='item-row';
  row.innerHTML='<label class="item-product">Ürün adı<input class="product" aria-label="Ürün adı" placeholder="Marka, ürün ve gramaj" required maxlength="500"></label><label>Faturadaki sıra no<input class="row-number" aria-label="Faturadaki sıra no" type="number" min="1" max="500" step="1" placeholder="Örn. 1"></label><label>Ürün kodu<input class="code" aria-label="Ürün kodu" placeholder="Faturadaki kod" maxlength="100"></label><label>Ücretli miktar<input class="quantity" aria-label="Ücretli miktar" type="number" step="0.0001" min="0.0001" placeholder="Örn. 18" required></label><label>Bedelsiz miktar<input class="gift-quantity" type="number" step="0.0001" min="0" max="99999999" placeholder="0"></label><label class="gift-only-label"><input class="gift-only" type="checkbox"> Bu satır tamamen bedelsiz</label><label>Birim / ambalaj<input class="unit" aria-label="Ambalaj ve fiyat birimi" placeholder="Adet / koli / kg" required maxlength="200"></label><label>Ücretli birim fiyatı (KDV dahil TL)<input class="price" aria-label="Ücretli birim fiyatı KDV dahil TL" type="number" step="0.0001" min="0.0001" placeholder="Otomatik hesaplanır" required></label><label>Faturada yazan Tutarı (TL)<input class="line-total" aria-label="Faturada yazan satır tutarı TL" type="number" step="0.01" min="0.01" placeholder="Örn. 473,49"></label><label>Faturada yazan KDV T. (TL)<input class="vat" aria-label="Faturada yazan KDV tutarı TL" type="number" step="0.01" min="0" placeholder="Örn. 4,72"></label><button type="button" aria-label="Ürün satırını sil">Sil</button>';
  function recalculatePrice(){
    const quantity=Number(row.querySelector('.quantity').value),net=Number(row.querySelector('.line-total').value),vat=Number(row.querySelector('.vat').value),gross=net+vat;
    if(Number.isFinite(quantity)&&quantity>0&&Number.isFinite(net)&&net>0&&Number.isFinite(vat)&&vat>=0){
      const paidTicks=Math.round(quantity*10000),grossCents=Math.round(gross*100);
      if(!Number.isSafeInteger(paidTicks)||paidTicks<=0||!Number.isSafeInteger(grossCents))return;
      const denominator=BigInt(paidTicks);
      const priceTicks=(BigInt(grossCents)*1000000n+denominator/2n)/denominator;
      row.querySelector('.price').value=(Number(priceTicks)/10000).toFixed(4);
      const evidence=row.querySelector('.item-evidence');
      if(evidence)evidence.textContent='Miktar veya satır toplamı değişti. Son birim fiyat yeniden hesaplandı; kontrol edin.';
    }
  }
  row.querySelector('.gift-only').oninput=event=>setGiftOnly(row,event.target.checked);
  row.querySelector('.quantity').oninput=recalculatePrice;
  row.querySelector('.line-total').oninput=recalculatePrice;
  row.querySelector('.vat').oninput=recalculatePrice;


  row.querySelector('.row-number').required=Boolean(activeImportCheck);row.querySelector('.line-total').required=Boolean(activeImportCheck);
  row.querySelector('button').onclick=()=>{row.remove();renderImportCheck();scheduleFormDraft($('#invoice-form'));};$('#item-rows').append(row);scheduleFormDraft($('#invoice-form'));
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
function isHeicFile(file){return ['image/heic','image/heif'].includes(file.type)||/\.hei[cf]$/i.test(file.name);}
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
      // Read the cleaned document first, then retain the original as a safety
      // copy. Cropping can occasionally miss a header or a faint table edge.
      const combined=[...prepared,...photos.filter(photo=>!prepared.includes(photo))];
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
  $('#invoice-document').hidden=!selectedPhotos.length;
  $('#photo-previews').innerHTML=selectedPhotos.map((photo,index)=>`<article class="photo-page"><img src="${esc(photo)}" alt="Fatura sayfa ${index+1}"><div><strong>Sayfa ${index+1}</strong><button type="button" data-remove-photo="${index}" aria-label="Sayfa ${index+1} fotoğrafını kaldır" class="delete-button">Kaldır</button></div></article>`).join('');
  $('#photo-count').textContent=selectedPhotos.length?selectedPhotos.length+' sayfa eklendi · En fazla 6 sayfa, toplam 8 MB.':'';
}
function photoSelectionChanged(){
  ocrFirmHint={};
  $('.confirm input').checked=false;
  $('#ocr-text').hidden=true;$('#copy-ocr').hidden=true;$('#ocr-review').hidden=true;
  renderSelectedPhotos();
  $('#ocr-status').textContent=selectedPhotos.length+' sayfa eklendi. Tüm sayfaları okumak için “Faturayı oku ve alanları doldur” düğmesine basın. Önceden doldurulmuş alanları yeniden kontrol edin.';scheduleFormDraft($('#invoice-form'));
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
  if(existingBytes+files.reduce((sum,f)=>sum+f.size,0)>8*1024*1024||files.some(file=>!isPdfFile(file)&&!isHeicFile(file)&&!imageTypes.includes(file.type))){$('#ocr-status').textContent='Eklenemedi. PDF, HEIC, JPEG, PNG veya WebP seçin; toplam dosya boyutu en fazla 8 MB olabilir. Mevcut sayfalarınız korundu.';return;}
  const generation=ocrGeneration;
  photoLoading=true;$('#add-photo').disabled=true;$('#read-photo').disabled=true;
  try{
    const added=[];const addedPdfPages=new Set();let pdfPages=0;
    for(const file of files){
      if(isPdfFile(file)){
        $('#ocr-status').textContent=`${file.name} PDF sayfaları hazırlanıyor…`;
        const rendered=await pdfPageImages(file,6-selectedPhotos.length-added.length),pages=rendered.photos;
        if(generation!==ocrGeneration||!$('#invoice-dialog').open)return;
        added.push(...pages);pages.forEach((page,index)=>{addedPdfPages.add(page);pdfTextLayers.set(page,rendered.layers[index]);});pdfPages+=pages.length;
      }else{
        if(selectedPhotos.length+added.length>=6)throw new Error('Toplam en fazla 6 sayfa ekleyebilirsiniz.');
        added.push(await fileDataUrl(file));
        if(generation!==ocrGeneration||!$('#invoice-dialog').open)return;
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
  }catch(error){if(generation===ocrGeneration&&$('#invoice-dialog').open)$('#ocr-status').textContent=(error?.message||'Dosya yüklenemedi.')+' Mevcut sayfalarınız korundu.';}
  finally{if(generation===ocrGeneration){photoLoading=false;$('#add-photo').disabled=false;$('#read-photo').disabled=false;}}
};

let ocrGeneration=0;
$('#read-photo').onclick=async()=>{
  if(photoLoading)return;
  if(!selectedPhoto){$('#ocr-status').textContent='Önce PDF veya fotoğraf ekleyin.';return;}
  const form=$('#invoice-form');
  if((form.elements.number.value||form.elements.ettn.value||form.elements.total.value||$('#item-rows').children.length)&&!window.confirm('Dosyadan okunan bilgiler bu formdaki tarih, fatura numarası, ETTN, toplam, firma ve ürünlerin yerine yazılacak. Kaydettiğiniz kayıt değişmez; önce kontrol edeceksiniz. Devam edilsin mi?'))return;
  const generation=++ocrGeneration, requestedPhoto=selectedPhoto;
  $('#read-photo').disabled=true;$('#add-photo').disabled=true;$('#ocr-status').textContent='Fatura okunuyor ve alanlar hazırlanıyor…';
  form.querySelector('button.primary').disabled=true;
  try{
    const originalPhotos=selectedPhotos.length?selectedPhotos:[requestedPhoto];
    // Camera photos need both the cleaned image and the untouched original.
    // This also protects screenshots whose browser bars confuse auto-cropping.
    const ocrPhotos=await prepareOcrPhotos(originalPhotos,true);
    if(generation!==ocrGeneration||selectedPhoto!==requestedPhoto||!$('#invoice-dialog').open)return;
    const textLayers=originalPhotos.map(photo=>pdfTextLayers.get(photo)).filter(Boolean);
    const result=await api('ocr',{photos:ocrPhotos,pdf_text_layers:textLayers,source_page_count:originalPhotos.length,layout_firm_id:$('#layout-firm').value});
    if(generation!==ocrGeneration||selectedPhoto!==requestedPhoto||!$('#invoice-dialog').open)return;
    const draft=result.draft;
    if(!draft.items.length&&!draft.number&&!draft.ettn&&!draft.issued&&!draft.total&&!draft.supplier&&!draft.supplier_tax_numbers?.length){
      throw new Error('Fotoğraftan fatura bilgisi okunamadı. Faturayı dik, net ve yakından çekip yeniden deneyin.');
    }
    lastOcrSupplier=draft.supplier_tax_numbers?.length?'':draft.supplier||'';
    ocrFirmHint={
      name:draft.seller_name_hint||'',
      taxNumber:draft.supplier_tax_numbers?.length===1?draft.supplier_tax_numbers[0]:''
    };
    $('#ocr-text').value=result.text;$('#ocr-text').hidden=false;$('#copy-ocr').hidden=false;
    for(const key of ['number','ettn','issued','total']){
      if(draft[key])form.elements[key].value=draft[key];
      else if(key==='issued'&&!form.dataset.editId&&!form.elements.issued.dataset.userEdited)form.elements.issued.value='';
      form.elements[key].title=draft.sources?.[key]?'Fotoğraftaki kaynak: '+draft.sources[key]:'Fotoğraftan okunamadı; alanı kontrol edin.';
    }
    if(draft.firm_id&&[...form.elements.firm_id.options].some(option=>Number(option.value)===draft.firm_id)){
      const previous=form.elements.firm_id.value;
      form.elements.firm_id.value=draft.firm_id;
      if(previous!==form.elements.firm_id.value&&!form.elements.days.dataset.userEdited&&!$('#due-preview').dataset.userEdited)applyDefaultDue();
    }else if(!form.dataset.editId&&(draft.supplier_tax_numbers?.length||!form.elements.firm_id.dataset.userEdited)){
      const select=form.elements.firm_id;
      if(![...select.options].some(option=>option.value===''))select.prepend(new Option('Firmayı seçin',''));
      select.value='';
      if(!form.elements.days.dataset.userEdited&&!$('#due-preview').dataset.userEdited)applyDefaultDue();
    }
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
      row.querySelector('.price').value=item.price;row.querySelector('.row-number').value=item.row_number||'';row.querySelector('.line-total').value=item.line_total||'';
      row.querySelector('.vat').value=item.vat||'0';row.querySelector('.quantity').value=item.quantity||'';row.querySelector('.code').value=item.code||'';
      row.dataset.sourceNet=item.net;row.dataset.sourceVat=item.vat;
      const evidence=document.createElement('small');evidence.className='item-evidence';evidence.textContent=`Kod: ${item.code} · Miktar: ${item.quantity} · Satır: ${item.net} + KDV ${item.vat} TL`;row.append(evidence);
      markItemReview(row,item.review);
      row.title=`Ürün kodu: ${item.code} · Miktar: ${item.quantity} · Faturadaki birim fiyat: ${item.source_price} · KDV hariç satır: ${item.net} · KDV: ${item.vat}`;
    });
    renderImportCheck();$('.confirm input').checked=false;
    $('#ocr-review').hidden=false;
    $('#ocr-review').textContent=(draft.supplier?'Okunan firma: '+draft.supplier+'\n':'')+draft.warnings.join('\n');
    $('#ocr-status').textContent=`${result.language==='PDF'?'PDF metni doğrulandı.':'Türkçe okuma motoru çalıştı.'} ${draft.items.length} ürün satırı ve okunabilen fatura alanları dolduruldu. Henüz kaydedilmedi. Tüm sayfaların eksiksiz okunduğunu kontrol edin; boş alanları tamamlayın. Birim fiyatlar satır tutarı + KDV üzerinden hesaplandı.`;scheduleFormDraft(form);
  }catch(e){if(generation===ocrGeneration&&$('#invoice-dialog').open)$('#ocr-status').textContent=e.message;}
  finally{if(generation===ocrGeneration){$('#read-photo').disabled=false;$('#add-photo').disabled=false;form.querySelector('button.primary').disabled=false;}}
};

document.addEventListener('click',async e=>{
  const pay=e.target.closest('[data-pay-firm]'),photo=e.target.closest('[data-photo]');
  if(pay)openPayment(Number(pay.dataset.payFirm));
  if(photo){try{const r=await api('photo/'+photo.dataset.photo);$('#photo-pages').innerHTML=(r.photos||[r.photo]).map((p,index)=>`<p>Sayfa ${index+1}</p><img src="${esc(p)}" alt="Fatura sayfa ${index+1}" style="max-width:100%">`).join('');$('#photo-dialog').showModal();}catch(e){toast(e.message);}}
});
for(const [id,path] of [['firm','firms'],['invoice','invoices'],['payment','payments']]){
  $('#'+id+'-form').onsubmit=async e=>{
    e.preventDefault();const form=e.target,button=form.querySelector('button.primary'),error=form.querySelector('.form-error');error.textContent='';
    const savedDraftKey=formDraftKey(form);
    if((photoLoading||$('#read-photo').disabled)&&id==='invoice'){error.textContent='Fatura dosyasının hazırlanmasını bekleyin.';return;}
    const data=Object.fromEntries(new FormData(form));
    if(id==='payment'){
      data.target_type=form.dataset.paymentType;
      if(data.target_type==='opening')data.target_id=Number(form.dataset.targetId);
      if(data.target_type==='invoice')data.invoice_id=Number(form.dataset.targetId);
    }
    if(id==='invoice'){data.photo=selectedPhotos.length?null:selectedPhoto;data.photos=selectedPhotos;data.ocr_supplier=lastOcrSupplier;data.items=[...document.querySelectorAll('.item-row')].map(r=>({product:r.querySelector('.product').value,unit:r.querySelector('.unit').value,price:r.querySelector('.price').value,quantity:r.querySelector('.quantity').value,gift_quantity:r.querySelector('.gift-quantity').value,gift_only:r.querySelector('.gift-only').checked,code:r.querySelector('.code').value,net:r.querySelector('.line-total').value,vat:r.querySelector('.vat').value,row_number:r.querySelector('.row-number').value,line_total:r.querySelector('.line-total').value}));}
    if(id==='invoice'){const errors=renderImportCheck();data.import_check=activeImportCheck;if(errors.length){error.textContent='Eksik veya tutarsız ürünlerle kaydedilemez. '+errors.join(' ');return;}}
    const editing=form.dataset.editId;
    if(editing)data.id=Number(editing);
    if(id==='invoice'&&editing)data.invoice_identity=form.dataset.invoiceIdentity;
    button.disabled=true;
    try{
      if(id==='invoice'&&state.desktop_invoice_confirmation){
        const dialog=form.closest('dialog');
        const preventCancel=event=>event.preventDefault();
        dialog.addEventListener('cancel',preventCancel);
        form.inert=true;
        error.textContent='Sancaklı Market penceresinde fatura onayı bekleniyor.';
        try{
          const firm=state.firms.find(f=>f.id===Number(data.firm_id));
          const {confirmed}=await api('invoice-confirmation',{firm:firm?.name,number:data.number,total:data.total});
          if(!confirmed){error.textContent='Fatura onayı iptal edildi.';return;}
          error.textContent='';
        }finally{
          form.inert=false;
          dialog.removeEventListener('cancel',preventCancel);
        }
      }
      let saved;
      try{saved=await api(path+(editing?'/edit':''),data);}
      catch(saveError){
        if(id!=='invoice'||!saveError.message.startsWith('MÜKERRER_ONAYI:'))throw saveError;
        const warning=saveError.message.slice('MÜKERRER_ONAYI:'.length);
        if(!window.confirm(warning))throw new Error('Mükerrer olabilecek fatura kaydedilmedi.');
        data.duplicate_confirmed=true;saved=await api(path+(editing?'/edit':''),data);
      }
      await clearFormDraft(form,savedDraftKey);
      if(id==='firm'&&form.dataset.fromInvoice==='1'&&saved?.firm&&$('#invoice-dialog').open){
        const firm=saved.firm,invoice=$('#invoice-form'),select=$('#firm-select');
        if(!state.firms.some(existing=>existing.id===firm.id))state.firms.push(firm);
        if(![...select.options].some(option=>Number(option.value)===firm.id))select.add(new Option(firm.name,String(firm.id)));
        select.value=String(firm.id);
        const dueEdited=invoice.elements.days.dataset.userEdited||$('#due-preview').dataset.userEdited;
        if(!invoice.elements.days.value||(!invoice.dataset.editId&&!dueEdited))applyDefaultDue();
        form.closest('dialog').close();
        scheduleFormDraft(invoice);
        try{await refresh();}
        catch(error){toast('Firma kaydedildi. '+error.message);return;}
        toast('Yeni firma seçildi. Fatura formu açık.');
        return;
      }
      form.closest('dialog').close();
      if(id==='invoice'){
        showInvoicePriceNotice(null,0,false);
        const inBatch=!editing&&batchActive&&form.dataset.batchJob;
        if(inBatch)batchIndex++;
        const hasNext=inBatch&&batchIndex<batchQueue.length;
        if(inBatch){
          if(!hasNext)batchActive=false;
          renderBatchQueue();
        }
        const results=await Promise.allSettled([refresh(),api('invoice-price-count/'+saved.invoice_id)]);
        const count=results[1].status==='fulfilled'?results[1].value.count:0;
        showInvoicePriceNotice(saved.invoice_id,count,hasNext);
        if(results.some(result=>result.status==='rejected'))toast('Fatura kaydedildi. Fiyat listesini yenileyerek kontrol edin.');
        else toast(editing?'Fatura düzeltildi.':inBatch&&!hasNext?'Toplu PDF sırası tamamlandı.':'Fatura kaydedildi.');
        if(hasNext&&!count)setTimeout(openBatchInvoice,250);
        return;
      }
      await refresh();
      toast(editing?'Düzeltme kaydedildi; bakiyeler ve fiyat değişimleri güncellendi.':'Kaydınız oluşturuldu.');
    }
    catch(e){error.textContent=e.message;}finally{button.disabled=false;}
  };
}
$('#account-entry-form').onsubmit=async event=>{
  event.preventDefault();const form=event.target,button=form.querySelector('button.primary'),error=form.querySelector('.form-error');error.textContent='';
  const data=Object.fromEntries(new FormData(form)),editing=form.dataset.editId,savedDraftKey=formDraftKey(form);if(editing)data.id=Number(editing);
  button.disabled=true;
  try{await api('account-entries'+(editing?'/edit':''),data);await clearFormDraft(form,savedDraftKey);form.closest('dialog').close();await refresh();toast(editing?'Cari hareket düzeltildi; tüm bakiyeler güncellendi.':'Cari hareket kaydedildi; tüm bakiyeler güncellendi.');}
  catch(value){error.textContent=value.message;}finally{button.disabled=false;}
};
refresh().catch(e=>toast('Veriler yüklenemedi: '+e.message));
setInterval(()=>{if(!document.querySelector('dialog[open]'))refresh().catch(()=>{});},60000);

function setMode(kind,id){
  const form=$('#'+kind+'-form');
  form.dataset.editId=id||'';
  if(kind==='invoice')delete form.dataset.invoiceIdentity;
  const nouns={firm:'Firma',invoice:'Fatura',payment:'Ödeme'};
  form.querySelector('h2').textContent=nouns[kind]+(id?' düzelt':kind==='payment'?' kaydet':' ekle');
  form.querySelector('button.primary').textContent=id?'Düzeltmeyi kaydet':kind==='invoice'?'Onayla ve faturayı kaydet':kind==='firm'?'Firmayı kaydet':'Ödemeyi kaydet';
}
function updatePaymentLimit(){
  const form=$('#payment-form'),firm=state.firms.find(f=>f.id===Number(form.elements.firm_id.value));
  const old=state.payments.find(p=>p.id===Number(form.dataset.editId)&&p.target_type===form.dataset.paymentType);
  let limit=firm?.balance||0;
  if(old?.firm_id===firm?.id&&old)limit=Math.max(old.amount,limit+old.amount);
  if(old?.target_type==='invoice'||old?.target_type==='opening'){
    const entries=old.target_type==='invoice'?state.invoices:state.openings;
    const entry=entries.find(value=>value.id===old.target_id);
    limit=Math.min(limit,(entry?.document_remaining||0)+old.amount);
  }
  form.elements.amount.max=(Math.max(0,limit)/100).toFixed(2);
  $('#payment-description').textContent=firm?`Cari bakiye: ${currency(firm.balance)}`:'Ödeme yapılacak firmayı seçin.';
}
function openPayment(firmId=null,payment=null){
  if(!state)return;
  const form=$('#payment-form'),kind=payment?.target_type||'firm';
  const firms=state.firms.filter(f=>kind==='firm'?(f.balance>0||f.id===payment?.firm_id):f.id===payment.firm_id);
  if(!firms.length){toast('Ödeme kaydedilecek açık cari borç yok.');return;}
  form.reset();
  setMode('payment',payment?.id);
  form.dataset.paymentType=kind;
  form.dataset.targetId=payment?.target_id||'';
  form.querySelector('.form-error').textContent='';
  form.elements.firm_id.innerHTML=(firms.length>1?'<option value="">Firma seçin</option>':'')+
    firms.map(f=>`<option value="${f.id}">${esc(f.name)}</option>`).join('');
  if(firms.some(f=>f.id===firmId))form.elements.firm_id.value=String(firmId);
  form.elements.amount.value=payment?(payment.amount/100).toFixed(2):'';
  form.elements.paid_on.value=payment?.paid_on||state.today;
  form.elements.paid_on.max=state.today;
  updatePaymentLimit();
  $('#payment-dialog').showModal();
  restoreFormDraft(form);
}
$('#new-payment').onclick=()=>openPayment();
$('#payment-form [name=firm_id]').onchange=updatePaymentLimit;
document.addEventListener('click',async e=>{
  const firm=e.target.closest('[data-edit-firm]'),invoice=e.target.closest('[data-edit-invoice]'),entry=e.target.closest('[data-edit-entry]'),payment=e.target.closest('[data-edit-payment]'),approve=e.target.closest('[data-approve]');
  if(firm){
    $('#new-firm').click();const f=state.firms.find(f=>f.id===Number(firm.dataset.editFirm));setMode('firm',f.id);$('#firm-form [name=name]').value=f.name;$('#firm-form [name=default_due_days]').value=f.default_due_days??'';$('#firm-form [name=phone]').value=f.phone||'';$('#firm-form [name=tax_number]').value=f.tax_number||'';restoreFormDraft($('#firm-form'));
  }
  if(invoice){
    invoice.closest('#firm-history-dialog,#price-history-dialog')?.close();
    const i=state.invoices.find(i=>i.id===Number(invoice.dataset.editInvoice));openInvoiceForm(i.firm_id);setMode('invoice',i.id);
    const form=$('#invoice-form');form.dataset.invoiceIdentity=i.identity;const metadata=JSON.parse(i.import_meta||'{}'),savedItems=state.items.filter(t=>t.invoice_id===i.id);if(metadata.expected_rows&&!metadata.line_total_kind&&savedItems.length&&savedItems.every(t=>t.source_net!==''&&t.source_vat!==''))metadata.line_total_kind='net';activeImportCheck=metadata.expected_rows?metadata:null;form.elements.firm_id.value=i.firm_id;form.elements.number.value=i.number;form.elements.ettn.value=i.ettn||'';form.elements.issued.value=i.issued;form.elements.days.value=Math.round((new Date(i.due)-new Date(i.issued))/86400000);form.elements.total.value=(i.total/100).toFixed(2);$('#expected-item-count').value=metadata.expected_rows?.length?Math.max(...metadata.expected_rows):'';$('#expected-item-count').required=Boolean(metadata.expected_rows);updateDue();
    savedItems.forEach(t=>{$('#add-item').click();const row=$('#item-rows').lastElementChild;row.querySelector('.product').value=t.product;row.querySelector('.unit').value=t.unit;row.querySelector('.price').value=t.unit_price||String(t.price/100);row.querySelector('.row-number').value=t.row_number||'';row.querySelector('.line-total').value=metadata.line_total_kind==='net'?(t.source_net||t.line_total):(t.line_total||'');row.querySelector('.vat').value=t.source_vat||'0';row.querySelector('.quantity').value=t.quantity||'';row.querySelector('.gift-quantity').value=Number(t.gift_quantity)?t.gift_quantity:'';row.querySelector('.code').value=t.code||'';setGiftOnly(row,Boolean(t.gift_only));if(t.source_net!==''&&t.source_vat!==''){row.dataset.sourceNet=t.source_net;row.dataset.sourceVat=t.source_vat;const evidence=document.createElement('small');evidence.className='item-evidence';evidence.textContent='Faturada yazan: '+t.source_net+' TL · KDV: '+t.source_vat+' TL';row.append(evidence);}});
    renderImportCheck();
    if(i.has_photo){
      const generation=ocrGeneration;
      photoLoading=true;form.querySelector('button.primary').disabled=true;
      try{const r=await api('photo/'+i.id);if(generation!==ocrGeneration||!form.closest('dialog').open)return;selectedPhotos=r.photos||[r.photo];pdfOcrPhotos.clear();pdfTextLayers.clear();renderSelectedPhotos();}
      catch(error){if(generation===ocrGeneration&&form.closest('dialog').open){form.closest('dialog').close();toast('Fatura sayfaları yüklenemedi. Kaydı yeniden açın.');}return;}
      finally{if(generation===ocrGeneration){photoLoading=false;form.querySelector('button.primary').disabled=false;}}
    }
    restoreFormDraft(form);
  }
  if(entry){entry.closest('#firm-history-dialog')?.close();openAccountEntry(null,Number(entry.dataset.editEntry));}
  if(payment){
    payment.closest('#firm-history-dialog')?.close();
    const type=payment.dataset.paymentType;
    const record=state.payments.find(p=>p.id===Number(payment.dataset.editPayment)&&p.target_type===type);
    if(record)openPayment(record.firm_id,record);
  }
  if(approve){
    approve.disabled=true;
    try{await api('prices/approve',{fingerprint:approve.dataset.approve});await refresh();toast('Fiyat değişimi onaylandı.');}
    catch(error){toast(error.message);approve.disabled=false;}
  }
});

document.addEventListener('click',async e=>{
  const merge=e.target.closest('[data-merge-firm]');
  if(merge){
    const source=state.firms.find(f=>f.id===Number(merge.dataset.mergeFirm));
    if(source)openFirmMerge(source);
    return;
  }
  const archive=e.target.closest('[data-archive-firm]');
  if(archive){
    const firm=state.firms.find(f=>f.id===Number(archive.dataset.archiveFirm));
    if(!firm)return;
    archive.disabled=true;
    try{await api('firms/archive',{id:firm.id,archived:!firm.archived});await refresh();}
    catch(error){toast(error.message);archive.disabled=false;}
    return;
  }
  const button=e.target.closest('[data-delete-invoice],[data-delete-payment],[data-delete-entry]');
  if(!button)return;
  let path, data, message;
  if(button.dataset.deletePayment!==undefined){
    const type=button.dataset.paymentType||'invoice';const payment=state.payments.find(p=>p.id===Number(button.dataset.deletePayment)&&p.target_type===type);
    if(!payment)return;
    path='payments/delete';data={id:payment.id,target_type:type};
    message=currency(payment.amount)+' tutarındaki ödeme silinecek. Firmanın cari borcu bu kadar artacak. Onaylıyor musunuz?';
  }else if(button.dataset.deleteEntry!==undefined){
    const entry=[...state.openings,...state.credits].find(value=>value.id===Number(button.dataset.deleteEntry));if(!entry)return;
    path='account-entries/delete';data={id:entry.id};
    message=(entry.kind==='opening'?'Açılış bakiyesi':'İade / alacak')+' kaydı silinecek.'+(entry.kind==='opening'&&entry.paid?' Bağlı ödemeler de silinecek.':'')+' Cari ve ödeme planı yeniden hesaplanacak. Onaylıyor musunuz?';
  }else{
    const id=Number(button.dataset.deleteInvoice);
    const record=state.invoices.find(invoice=>invoice.id===id);
    if(!record)return;
    const payments=state.payments.filter(p=>p.invoice_id===id);
    const items=state.items.filter(t=>t.invoice_id===id);
    const entries=state.credits.filter(entry=>entry.invoice_id===id);
    path='invoices/delete';
    data={id,expected:{invoices:1,payments:payments.length,items:items.length,entries:entries.length,opening_payments:0}};
    message=record.number+' faturası silinecek.\nBağlı '+payments.length+' ödeme, '+entries.length+' iade kaydı ve '+items.length+' ürün satırı da silinecek.\nCari toplamlar ve fiyat karşılaştırmaları yeniden hesaplanacak. Onaylıyor musunuz?';
  }
  if(!window.confirm(message))return;
  button.disabled=true;
  try{button.closest('#firm-history-dialog')?.close();const result=await api(path,data);showUndo(result.undo_id);await refresh();toast('Kayıt silindi; ödeme ve cari toplamları güncellendi.');}
  catch(error){toast(error.message);button.disabled=false;}
});

function showUndo(id){
  const panel=$('#deletion-undo');
  panel.hidden=!id;
  panel.querySelector('button').dataset.undoId=id||'';
}
$('#deletion-undo button').onclick=async event=>{
  const button=event.currentTarget,id=Number(button.dataset.undoId);
  button.disabled=true;
  try{await api('deletions/restore',{id});if(Number(button.dataset.undoId)===id)showUndo(null);await refresh();toast('Silinen kayıt geri alındı.');}
  catch(error){toast(error.message);}
  finally{button.disabled=false;}
};
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
    if(row.querySelector('.gift-only').checked){
      if(!(Number(row.querySelector('.gift-quantity').value)>0))errors.push((index+1)+'. satırın bedelsiz miktarını girin.');
      return;
    }
    const quantity=Number(row.querySelector('.quantity').value),price=Number(row.querySelector('.price').value),line=Number(row.querySelector('.line-total').value),vat=Number(row.querySelector('.vat').value),gross=line+vat;
    if(!Number.isFinite(quantity)||quantity<=0||!Number.isFinite(price)||price<=0||!Number.isFinite(line)||line<=0||!Number.isFinite(vat)||vat<0)errors.push((index+1)+'. ürünün miktar, fiyat, faturadaki tutar ve KDV alanlarını doldurun.');
    else{total+=Math.round(gross*100);if(Math.abs(quantity*price-gross)>quantity*.005+.020001)errors.push((index+1)+'. ürünün ücretli miktarı × KDV dahil birim fiyatı, faturadaki tutar + KDV ile uyuşmuyor.');}
  });
  const target=Math.round(Number($('#invoice-form [name=total]').value)*100);
  if(!target||Math.abs(total-target)>2)errors.push('Ürün satır toplamları ile fatura toplamı uyuşmuyor.');
  panel.classList.toggle('check-error',errors.length>0);
  panel.textContent='Faturada beklenen: '+expected.length+' kalem · Listedeki: '+rows.length+' kalem · Tutar + KDV toplamı: '+currency(total)+'\n'+(countNote?countNote+'\n':'')+(errors.length?errors.join('\n'):'Kalem sayısı ile faturada yazan tutar ve KDV toplamları uyumlu. Ürün adlarını, kodlarını ve miktarları ayrıca fotoğraftan doğrulayın.');
  return errors;
}

function renderProductSuggestions(firmId,invoiceOnly){
  let container=$('#product-matches');
  if(!container){container=document.createElement('div');container.id='product-matches';container.className='panel';$('#price-list').before(container);}
  if(invoiceOnly){container.hidden=true;return;}
  const suggestions=state.product_suggestions.filter(item=>!firmId||item.current.firm_id===firmId);
  const unmatched=state.unmatched_products.filter(item=>!firmId||item.firm_id===firmId);
  const history=state.product_match_history.filter(item=>!firmId||JSON.parse(item.left_key)[0]===firmId||JSON.parse(item.right_key)[0]===firmId);
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
