let state, selectedPhoto = null, selectedPhotos = [], photoLoading = false;
const $ = s => document.querySelector(s);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const currency = cents => new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY'}).format(cents/100);
const unitCurrency = cents => new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY',minimumFractionDigits:2,maximumFractionDigits:4}).format(cents/100);
const niceDate = s => new Date(s+'T12:00:00').toLocaleDateString('tr-TR',{day:'numeric',month:'short',year:'numeric'});
async function api(path, data) {
  const r = await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const value = await r.json();
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
    return `<tr><td><strong>${esc(i.firm)}</strong><small>${esc(i.number)} · ${niceDate(i.issued)}</small></td><td>${niceDate(i.due)}</td><td><strong>${currency(i.remaining)}</strong><small>Toplam ${currency(i.total)}</small></td><td><span class="badge ${badge[0]}">${badge[1]}</span></td><td><button data-edit-invoice="${i.id}">Düzelt</button> <button data-delete-invoice="${i.id}" class="delete-button">Sil</button> ${i.has_photo?`<button data-photo="${i.id}" aria-label="Fatura fotoğrafını aç">▧</button> `:''}${i.remaining?`<button data-pay="${i.id}">Ödeme kaydet</button>`:''}</td></tr>`;
  }));
}
function render(){
  let banner=$('#price-notification');
  if(!banner){banner=document.createElement('div');banner.id='price-notification';banner.className='tip';$('#overview').before(banner);}
  const pending=state.alerts.filter(a=>!a.approved).length;
  banner.hidden=!pending;
  banner.innerHTML=`<b>${pending} fiyat değişimi onayınızı bekliyor.</b><span>Artış ve düşüşlerin tamamını kontrol edin. Yanlış okunan fiyatı faturadan düzeltebilirsiniz.</span><button id="review-prices">Fiyat değişimlerini incele</button>`;
  banner.querySelector('button').onclick=()=>$('[data-tab=prices]').click();

  $('#date').textContent=new Date(state.today+'T12:00:00').toLocaleDateString('tr-TR',{weekday:'long',day:'numeric',month:'long',year:'numeric'});
  Object.entries(state.totals).forEach(([k,v])=>$('#'+k+'-total').textContent=currency(v));
  const filter=$('#agenda-filter').value;
  const end=new Date(state.today+'T12:00:00');end.setDate(end.getDate()+7);
  $('#agenda').innerHTML=invoiceTable(state.invoices.filter(i=>i.remaining>0 && (filter==='open'||filter==='today'&&i.due===state.today||filter==='late'&&i.late_days>0||filter==='week'&&i.due>state.today&&new Date(i.due+'T12:00:00')<=end)));
  const daily = {};
  state.invoices.filter(i=>i.remaining>0).forEach(i=>daily[i.due]=(daily[i.due]||0)+i.remaining);
  let dailySection=$('#daily-summary');
  if(!dailySection){dailySection=document.createElement('div');dailySection.id='daily-summary';$('#agenda').before(dailySection);}
  dailySection.innerHTML=Object.keys(daily).length?`<div class="daily-totals">${Object.entries(daily).sort(([a],[b])=>a.localeCompare(b)).map(([d,total])=>`<div><small>${niceDate(d)}${d<state.today?' · Gecikti':''}</small><strong>${currency(total)}</strong></div>`).join('')}</div>`:'';
  const search=$('#search').value.toLocaleLowerCase('tr');
  $('#invoice-list').innerHTML=invoiceTable(state.invoices.filter(i=>(i.firm+' '+i.number).toLocaleLowerCase('tr').includes(search)));
  $('#firm-list').innerHTML=state.firms.length?state.firms.map(f=>`<article class="firm-card"><h2>${esc(f.name)}</h2><p>Kalan cari borç</p><strong>${currency(f.balance)}</strong><p><button data-edit-firm="${f.id}">Düzelt</button> <button data-delete-firm="${f.id}" class="delete-button">Sil</button></p></article>`).join(''):empty('İlk firmanızı ekleyin.','Faturalarını takip edeceğiniz tedarikçilerinizi buradan kaydedin.');
  $('#price-list').innerHTML=state.alerts.length?table(['Ürün / Firma','Önceki fiyat','Yeni fiyat','Değişim','Onay'],state.alerts.map(a=>`<tr><td><strong>${esc(a.product)}</strong><small>${esc(a.unit)} · ${esc(a.firm)} · ${niceDate(a.issued)} · ${esc(a.number)}</small></td><td>${unitCurrency(a.old_price)}</td><td>${unitCurrency(a.price)}</td><td><span class="badge ${a.change>0?'late':''}">${a.change>0?'↑':'↓'} %${Math.abs(a.change).toLocaleString('tr-TR')}</span></td><td>${a.approved?'<span class="badge">Onaylandı</span>':`<button data-approve="${a.fingerprint}" class="primary">Değişimi onayla</button> <button data-edit-invoice="${a.invoice_id}">Faturayı düzelt</button>`}</td></tr>`)):empty('Henüz fiyat değişimi yok.','Aynı firmanın aynı ürün ve ambalajını iki faturada kaydettiğinizde değişim burada görünür.');
  $('#payment-list').innerHTML=state.payments.length?table(['Firma / Fatura','Ödeme tarihi','Ödenen',''],state.payments.map(p=>{const i=state.invoices.find(i=>i.id===p.invoice_id);return `<tr><td>${esc(i.firm)}<small>${esc(i.number)}</small></td><td>${niceDate(p.paid_on)}</td><td>${currency(p.amount)}</td><td><button data-edit-payment="${p.id}">Düzelt</button> <button data-delete-payment="${p.id}" class="delete-button">Sil</button></td></tr>`;})):empty('Henüz ödeme kaydı yok.','Fatura satırındaki “Ödeme kaydet” düğmesini kullanın.');
}
async function refresh(){state=await api('state');render();}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-tab]').forEach(t=>t.classList.toggle('active',t===b));
  ['overview','invoices','firms','prices'].forEach(id=>$('#'+id).hidden=id!==b.dataset.tab);
  const titles={overview:['Hesabınız net olsun.','Ödemelerinizi planlayın, işinize odaklanın.'],invoices:['Faturalarınız.','Her faturanın vadesi, her ödemenin kaydı.'],firms:['İş ortaklarınız.','Firma firma, tüm cari hesaplarınız.'],prices:['Fiyatları gözden kaçırmayın.','Faturalarınız arasındaki birim fiyat değişimleri.']};
  $('#title').textContent=titles[b.dataset.tab][0];$('#subtitle').textContent=titles[b.dataset.tab][1];
});
document.querySelectorAll('.close').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('#agenda-filter').onchange=render;$('#search').oninput=render;
$('#new-firm').onclick=()=>{setMode('firm',null);$('#firm-form').reset();$('#firm-form .form-error').textContent='';$('#firm-dialog').showModal();};
$('#new-invoice').onclick=()=>{
  if(!state)return;
  if(!state.firms.length){toast('Önce faturanın ait olduğu firmayı ekleyin.');$('[data-tab="firms"]').click();$('#new-firm').click();return;}
  ocrGeneration++;setMode('invoice',null);$('#invoice-form').reset();$('#invoice-form .form-error').textContent='';selectedPhoto=null;selectedPhotos=[];
  renderSelectedPhotos();$('#ocr-text').hidden=true;$('#ocr-review').hidden=true;$('#ocr-status').textContent='Fotoğraf isteğe bağlıdır. Okunan yazılar otomatik kaydedilmez.';
  $('#item-rows').innerHTML='';$('#due-preview').value='';
  $('#firm-select').innerHTML=state.firms.map(f=>`<option value="${f.id}">${esc(f.name)}</option>`).join('');
  $('#invoice-form [name=issued]').value=state.today;$('#invoice-dialog').showModal();
};
function updateDue(){const form=$('#invoice-form'),issued=form.elements.issued.value,days=form.elements.days.value;if(!issued||days==='')return;const d=new Date(issued+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+Number(days));if(Number.isNaN(d.getTime()))return;$('#due-preview').value=d.toISOString().slice(0,10);}
$('#invoice-form [name=issued]').oninput=updateDue;$('#invoice-form [name=days]').oninput=updateDue;
$('#due-preview').oninput=()=>{const issued=$('#invoice-form [name=issued]').value,due=$('#due-preview').value;if(issued&&due)$('#invoice-form [name=days]').value=Math.round((new Date(due)-new Date(issued))/86400000);};
$('#add-item').onclick=()=>{
  const row=document.createElement('div');row.className='item-row';
  row.innerHTML='<input class="product" aria-label="Ürün adı" placeholder="Marka ve ürün adı" required maxlength="200" list="products"><input class="unit" aria-label="Ambalaj ve fiyat birimi" placeholder="1 litre / adet" required maxlength="200"><input class="price" aria-label="Son birim fiyat TL" type="number" step="0.0001" min="0.0001" placeholder="Son fiyat TL" required><button type="button" aria-label="Ürün satırını sil">Sil</button>';
  row.querySelector('button').onclick=()=>row.remove();$('#item-rows').append(row);
};
function photoBytes(value){
  const base64=value.split(',')[1]||'';
  return Math.floor(base64.length*3/4)-(base64.endsWith('==')?2:base64.endsWith('=')?1:0);
}
function renderSelectedPhotos(){
  selectedPhoto=selectedPhotos[0]||null;
  $('#photo-previews').innerHTML=selectedPhotos.map((photo,index)=>`<article class="photo-page"><img src="${esc(photo)}" alt="Fatura sayfa ${index+1}"><div><strong>Sayfa ${index+1}</strong><button type="button" data-remove-photo="${index}" aria-label="Sayfa ${index+1} fotoğrafını kaldır" class="delete-button">Kaldır</button></div></article>`).join('');
  $('#photo-count').textContent=selectedPhotos.length?selectedPhotos.length+' fotoğraf eklendi · En fazla 6 sayfa, toplam 8 MB.':'';
}
function photoSelectionChanged(){
  ocrGeneration++;
  $('.confirm input').checked=false;
  $('#ocr-text').hidden=true;$('#ocr-review').hidden=true;
  renderSelectedPhotos();
  $('#ocr-status').textContent=selectedPhotos.length+' fotoğraf eklendi. Tüm sayfaları okumak için “Fotoğrafı oku ve alanları doldur” düğmesine basın. Önceden doldurulmuş alanları yeniden kontrol edin.';
}
$('#add-photo').onclick=()=>$('#photo').click();
$('#photo-previews').onclick=e=>{
  const button=e.target.closest('[data-remove-photo]');
  if(!button||photoLoading||$('#read-photo').disabled)return;
  selectedPhotos.splice(Number(button.dataset.removePhoto),1);
  photoSelectionChanged();
};
$('#photo').onchange=async e=>{
  const files=[...e.target.files];e.target.value='';
  if(!files.length)return;
  if(photoLoading||$('#read-photo').disabled){$('#ocr-status').textContent='Devam eden fotoğraf işleminin bitmesini bekleyin.';return;}
  const existingBytes=selectedPhotos.reduce((sum,p)=>sum+photoBytes(p),0);
  if(selectedPhotos.length+files.length>6||existingBytes+files.reduce((sum,f)=>sum+f.size,0)>8*1024*1024||files.some(f=>!['image/jpeg','image/png','image/webp'].includes(f.type))){$('#ocr-status').textContent='Eklenemedi. Toplam en fazla 6 JPEG/PNG/WebP ve 8 MB olabilir. Mevcut fotoğraflarınız korundu.';return;}
  const generation=ocrGeneration;
  photoLoading=true;$('#add-photo').disabled=true;$('#read-photo').disabled=true;
  try{
    const added=await Promise.all(files.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);})));
    if(generation!==ocrGeneration||!$('#invoice-dialog').open)return;
    let duplicate=false;
    for(const photo of added){if(selectedPhotos.includes(photo)){duplicate=true;}else{selectedPhotos.push(photo);}}
    photoSelectionChanged();
    if(duplicate)$('#ocr-status').textContent+=' Aynı fotoğraf tekrar eklenmedi.';
  }catch{$('#ocr-status').textContent='Fotoğraf yüklenemedi. Mevcut fotoğraflarınız korundu.';}
  finally{photoLoading=false;$('#add-photo').disabled=false;$('#read-photo').disabled=false;}
};

let ocrGeneration=0;
$('#read-photo').onclick=async()=>{
  if(photoLoading)return;
  if(!selectedPhoto){$('#ocr-status').textContent='Önce bir fotoğraf seçin.';return;}
  const form=$('#invoice-form');
  if((form.elements.number.value||form.elements.total.value||$('#item-rows').children.length)&&!window.confirm('Fotoğraftan okunan bilgiler bu formdaki tarih, numara, toplam, firma ve ürünlerin yerine yazılacak. Kaydettiğiniz kayıt değişmez; önce kontrol edeceksiniz. Devam edilsin mi?'))return;
  const generation=++ocrGeneration, requestedPhoto=selectedPhoto;
  $('#read-photo').disabled=true;$('#add-photo').disabled=true;$('#ocr-status').textContent='Fotoğraf okunuyor ve fatura alanları hazırlanıyor…';
  form.querySelector('button.primary').disabled=true;
  try{
    const result=await api('ocr',{photos:selectedPhotos.length?selectedPhotos:[requestedPhoto]});
    if(generation!==ocrGeneration||selectedPhoto!==requestedPhoto||!$('#invoice-dialog').open)return;
    const draft=result.draft;
    $('#ocr-text').value=result.text;$('#ocr-text').hidden=false;
    form.elements.number.value=draft.number||'';
    form.elements.issued.value=draft.issued||'';
    form.elements.total.value=draft.total||'';
    form.elements.firm_id.value=draft.firm_id||'';
    $('#due-preview').value='';updateDue();
    $('#item-rows').innerHTML='';
    draft.items.forEach(item=>{
      $('#add-item').click();const row=$('#item-rows').lastElementChild;
      row.querySelector('.product').value=item.product;
      row.querySelector('.unit').value=item.unit;
      row.querySelector('.price').value=item.price;
      const evidence=document.createElement('small');evidence.className='item-evidence';evidence.textContent=`Kod: ${item.code} · Miktar: ${item.quantity} · Satır: ${item.net} + KDV ${item.vat} TL`;row.append(evidence);
      row.title=`Ürün kodu: ${item.code} · Miktar: ${item.quantity} · Faturadaki birim fiyat: ${item.source_price} · KDV hariç satır: ${item.net} · KDV: ${item.vat}`;
    });
    $('.confirm input').checked=false;
    $('#ocr-review').hidden=false;
    $('#ocr-review').textContent=(draft.supplier?'Okunan firma: '+draft.supplier+'\n':'')+draft.warnings.join('\n');
    $('#ocr-status').textContent=`${draft.items.length} ürün satırı ve okunabilen fatura alanları dolduruldu. Henüz kaydedilmedi. Tüm sayfaların eksiksiz okunduğunu kontrol edin; boş alanları tamamlayın. Birim fiyatlar satır tutarı + KDV üzerinden hesaplandı.`;
  }catch(e){$('#ocr-status').textContent=e.message;}
  finally{$('#read-photo').disabled=false;$('#add-photo').disabled=false;form.querySelector('button.primary').disabled=false;}
};

document.addEventListener('click',async e=>{
  const pay=e.target.closest('[data-pay]'),photo=e.target.closest('[data-photo]');
  if(pay){setMode('payment',null);const i=state.invoices.find(i=>i.id===Number(pay.dataset.pay));const f=$('#payment-form');f.reset();f.querySelector('.form-error').textContent='';paymentOptions(f);f.elements.invoice_id.value=i.id;f.elements.amount.value=(i.remaining/100).toFixed(2);f.elements.amount.max=(i.remaining/100).toFixed(2);f.elements.paid_on.value=state.today;f.elements.paid_on.max=state.today;$('#payment-description').textContent=`${i.firm} · ${i.number} · Kalan ${currency(i.remaining)}`;$('#payment-dialog').showModal();}
  if(photo){try{const r=await api('photo/'+photo.dataset.photo);$('#photo-pages').innerHTML=(r.photos||[r.photo]).map((p,index)=>`<p>Sayfa ${index+1}</p><img src="${esc(p)}" alt="Fatura sayfa ${index+1}" style="max-width:100%">`).join('');$('#photo-dialog').showModal();}catch(e){toast(e.message);}}
});
for(const [id,path] of [['firm','firms'],['invoice','invoices'],['payment','payments']]){
  $('#'+id+'-form').onsubmit=async e=>{
    e.preventDefault();const form=e.target,button=form.querySelector('button.primary'),error=form.querySelector('.form-error');error.textContent='';
    if((photoLoading||$('#read-photo').disabled)&&id==='invoice'){error.textContent='Fotoğrafın yüklenmesini bekleyin.';return;}
    const data=Object.fromEntries(new FormData(form));
    if(id==='invoice'){data.photo=selectedPhotos.length?null:selectedPhoto;data.photos=selectedPhotos;data.items=[...document.querySelectorAll('.item-row')].map(r=>({product:r.querySelector('.product').value,unit:r.querySelector('.unit').value,price:r.querySelector('.price').value}));}
    const editing=form.dataset.editId;
    if(editing)data.id=Number(editing);
    button.disabled=true;
    try{await api(path+(editing?'/edit':''),data);form.closest('dialog').close();await refresh();toast(editing?'Düzeltme kaydedildi; bakiyeler ve fiyat değişimleri güncellendi.':'Kaydınız oluşturuldu.');}
    catch(e){error.textContent=e.message;}finally{button.disabled=false;}
  };
}
refresh().catch(e=>toast('Veriler yüklenemedi: '+e.message));
setInterval(()=>{if(!document.querySelector('dialog[open]'))refresh().catch(()=>{});},60000);

function setMode(kind,id){
  const form=$('#'+kind+'-form');
  form.dataset.editId=id||'';
  const nouns={firm:'Firma',invoice:'Fatura',payment:'Ödeme'};
  form.querySelector('h2').textContent=nouns[kind]+(id?' düzelt':kind==='payment'?' kaydet':' ekle');
  form.querySelector('button.primary').textContent=id?'Düzeltmeyi kaydet':kind==='invoice'?'Onayla ve faturayı kaydet':kind==='firm'?'Firmayı kaydet':'Ödemeyi kaydet';
}
function paymentOptions(form){
  form.elements.invoice_id.innerHTML=state.invoices.map(i=>`<option value="${i.id}">${esc(i.firm)} · ${esc(i.number)}</option>`).join('');
}
document.addEventListener('click',async e=>{
  const firm=e.target.closest('[data-edit-firm]'),invoice=e.target.closest('[data-edit-invoice]'),payment=e.target.closest('[data-edit-payment]'),approve=e.target.closest('[data-approve]');
  if(firm){
    $('#new-firm').click();const f=state.firms.find(f=>f.id===Number(firm.dataset.editFirm));setMode('firm',f.id);$('#firm-form [name=name]').value=f.name;
  }
  if(invoice){
    const i=state.invoices.find(i=>i.id===Number(invoice.dataset.editInvoice));$('#new-invoice').click();setMode('invoice',i.id);
    const form=$('#invoice-form');form.elements.firm_id.value=i.firm_id;form.elements.number.value=i.number;form.elements.issued.value=i.issued;form.elements.days.value=Math.round((new Date(i.due)-new Date(i.issued))/86400000);form.elements.total.value=(i.total/100).toFixed(2);updateDue();
    state.items.filter(t=>t.invoice_id===i.id).forEach(t=>{$('#add-item').click();const row=$('#item-rows').lastElementChild;row.querySelector('.product').value=t.product;row.querySelector('.unit').value=t.unit;row.querySelector('.price').value=t.unit_price||(t.price/100).toFixed(4);});
    if(i.has_photo){
      photoLoading=true;form.querySelector('button.primary').disabled=true;
      try{const r=await api('photo/'+i.id);selectedPhotos=r.photos||[r.photo];renderSelectedPhotos();}
      catch(error){form.closest('dialog').close();toast('Fotoğraf yüklenemedi. Kaydı yeniden açın.');}
      finally{photoLoading=false;form.querySelector('button.primary').disabled=false;}
    }
  }
  if(payment){
    const p=state.payments.find(p=>p.id===Number(payment.dataset.editPayment));const form=$('#payment-form');form.reset();setMode('payment',p.id);paymentOptions(form);form.elements.invoice_id.value=p.invoice_id;form.elements.amount.value=(p.amount/100).toFixed(2);form.elements.amount.removeAttribute('max');form.elements.paid_on.value=p.paid_on;form.elements.paid_on.max=state.today;form.querySelector('.form-error').textContent='';$('#payment-description').textContent='Tutarı, tarihi veya yanlış seçilen faturayı düzeltebilirsiniz.';$('#payment-dialog').showModal();
  }
  if(approve){
    approve.disabled=true;
    try{await api('prices/approve',{fingerprint:approve.dataset.approve});await refresh();toast('Fiyat değişimi onaylandı.');}
    catch(error){toast(error.message);approve.disabled=false;}
  }
});

$('#payment-form [name=invoice_id]').onchange=()=>{
  const form=$('#payment-form'),i=state.invoices.find(i=>i.id===Number(form.elements.invoice_id.value));
  const old=state.payments.find(p=>p.id===Number(form.dataset.editId));
  form.elements.amount.max=((i.remaining+(old&&old.invoice_id===i.id?old.amount:0))/100).toFixed(2);
};

document.addEventListener('click',async e=>{
  const button=e.target.closest('[data-delete-firm],[data-delete-invoice],[data-delete-payment]');
  if(!button)return;
  let path, data, message;
  if(button.dataset.deletePayment){
    const payment=state.payments.find(p=>p.id===Number(button.dataset.deletePayment));
    if(!payment)return;
    path='payments/delete';data={id:payment.id};
    message=currency(payment.amount)+' tutarındaki ödeme silinecek. İlgili faturanın kalan borcu bu tutar kadar artacak. Onaylıyor musunuz?';
  }else{
    const firmMode=button.dataset.deleteFirm!==undefined;
    const id=Number(firmMode?button.dataset.deleteFirm:button.dataset.deleteInvoice);
    const record=(firmMode?state.firms:state.invoices).find(r=>r.id===id);
    if(!record)return;
    const invoices=state.invoices.filter(i=>firmMode?i.firm_id===id:i.id===id);
    const ids=new Set(invoices.map(i=>i.id));
    const payments=state.payments.filter(p=>ids.has(p.invoice_id));
    const items=state.items.filter(t=>ids.has(t.invoice_id));
    path=firmMode?'firms/delete':'invoices/delete';
    data={id,expected:{invoices:invoices.length,payments:payments.length,items:items.length}};
    message=(firmMode?record.name+' firması':record.number+' faturası')+' silinecek.\nBağlı '+invoices.length+' fatura, '+payments.length+' ödeme ve '+items.length+' ürün satırı da silinecek.\nCari toplamlar ve fiyat karşılaştırmaları yeniden hesaplanacak. Bu işlem ekrandan geri alınamaz. Onaylıyor musunuz?';
  }
  if(!window.confirm(message))return;
  button.disabled=true;
  try{await api(path,data);await refresh();toast('Kayıt silindi; ödeme ve cari toplamları güncellendi.');}
  catch(error){toast(error.message);button.disabled=false;}
});
