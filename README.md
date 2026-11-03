# Market Cari

Tek kullanıcılı, Türkçe cari takip uygulamasının yerel geliştirme sürümü.

## Çalıştırma

Python 3.11 veya üzeri gerekir. Python paket bağımlılığı yoktur.

```sh
cd /workspace/market
python3 app.py
```

Sunucu yalnızca `127.0.0.1:8000` üzerinde çalışır. `PORT` değişkeniyle port değiştirilebilir.
Kullanıcı girişi henüz bulunmadığından bu geliştirme sunucusunu internete açmayın.
Telefon üzerinden gerçek kullanım için kimlik doğrulama ve HTTPS ile yayınlama ayrıca hazırlanmalıdır.

## Kullanım

1. Firmalar bölümünde tedarikçiyi kaydedin.
2. Fatura ekle ile firma, benzersiz fatura numarası, tarih ve toplamı girin.
3. Her faturaya özel vade gününü veya ödeme tarihini girin. Vade fatura tarihinden hesaplanır; günler takvim günüdür, hafta sonu kaydırılmaz.
4. İsterseniz PDF veya fotoğraf yükleyin ve yazı okuma düğmesini kullanın. Faturayı oku ve alanları doldur düğmesi, okunabilen tarih, numara, ödenecek toplam, mevcut firma eşleşmesi ve ürün satırlarını taslak olarak doldurur. “Faturadaki toplam ürün kalemi” alanını faturanın son sıra numarasıyla karşılaştırın; tek bir sıra eksikken kayıt engellenir. Vade elle girilir. Bilgileri kontrol edip onaylayın.
5. Fiyat takibi için ürün kodu, ad, gramaj/ambalaj, miktar ve fiyat birimini kontrol edin. Karşılaştırma aynı firma içinde önce ürün koduyla yapılır; kod yoksa normalleştirilmiş ad ve ambalaj kullanılır. Belirsiz benzer adlar Fiyat değişimleri bölümünde Aynı ürün/Farklı ürün onayına sunulur. Onaylı eşleştirmeler hatırlanır ve Kararı kaldır ile geri alınabilir. Farklı gramaj veya fiyat birimleri sessizce birleştirilmez.
6. Fatura üzerinden tam veya kısmi ödeme kaydedin. Ödeme ilgili faturadan düşer; fazla ödeme kabul edilmez.
7. Genel bakıştaki aylık ödeme takviminde önceki ve sonraki ayları inceleyin. Takvim vade tarihindeki kalan borcu firma bazında gösterir ve ödeme kaydedildiğinde güncellenir.
8. Firmalar bölümündeki Hesap geçmişi düğmesiyle o firmaya ait faturaları, ödemeleri ve kalan cariyi birlikte görüntüleyin. Fiyat değişimleri bölümündeki Fiyat geçmişi düğmesi ürünün tarih sıralı fiyatlarını ve yüzde değişimlerini gösterir.

Günlük özet İstanbul tarihini kullanır. Gelecek 7 gün yarın başlar. Gecikenler yalnızca kalan borcu içerir.

## PDF ve fotoğraf okuma

Tesseract komutu PATH üzerinde olmalıdır. Türkçe için `tur` dil verisi önerilir; yoksa İngilizce motor kullanılır ve Türkçe harflerde doğruluk düşer. Debian/Ubuntu üzerinde sistem paketleri: `tesseract-ocr`, `tesseract-ocr-tur`. Motor yoksa elle kayıt kullanılabilir.

Dosyalar dış servise gönderilmez. PDF sayfaları uygulamanın içinde, bilgisayarınızda kayıpsız görüntülere çevrilir. PDF, JPEG, PNG ve WebP biçimleri; en fazla 6 sayfa ve toplam 8 MB desteklenir. Şifreli PDF açılamaz; önce şifresiz bir kopyası kaydedilmelidir. Telefon ekran görüntülerinde koyu PDF görüntüleyicisinin içindeki açık belge bölgeleri tarayıcıda otomatik kırpılır ve OCR için büyütülür; kayıtta eklenen sayfalar korunur.

Çevrimdışı ayrıştırıcı Türkçe etiketli (Fatura Tarihi, Belge/Fatura No, Ödenecek Tutar) alanları ve sıra no, ürün kodu, ürün adı, miktar, birim, birim fiyat, KDV oranı, KDV tutarı, satır net tutarı sırasındaki tabloları destekler. Tesseract’ın kelime konumları kullanılarak çizgili tablodaki ayrı sütunlar tekrar ürün satırlarına dönüştürülür. Farklı düzenler ve bulanık belgeler eksik ayrıştırılabilir; sıra numarası, satır toplamı ve fatura toplamı kontrolleri eksik kaydı engeller. Birim fiyat (satır neti + satır KDV) / miktar olarak iki ondalıkla gösterilir ve taslağa aktarılır; bu, satır tutarına yansıtılmış iskontoyu ikinci kez düşmez. Sonradan ayrıca uygulanan indirim/masraf varsa elle kontrol gerekir. Fatura dosyası ekle düğmesi mevcut sayfaları koruyarak yeni PDF veya fotoğraf ekler; sayfalar tek tek veya birlikte seçilebilir. Her sayfa önizlemede görünür ve Kaldır ile ayrı silinebilir. Dosya seçimini iptal etmek veya sınırı aşmak önceki sayfaları silmez. Aynı sayfa tekrar seçildiğinde çoğaltılmaz. Okutmadan önce tüm sayfalar eklenmiş olmalıdır. Örtüşen aynı numaralı satırlar kod, miktar ve tutarları uyuşuyorsa tekilleştirilir. Farklı OCR düzenlerinde birden fazla kez aynı çıkan satır, tek bir bozuk okumaya tercih edilir ve kullanıcıya kontrol uyarısı gösterilir; eşit çelişkiler otomatik seçilmez. Kayıtlar yalnızca kullanıcı kontrol kutusuyla onaylayıp kaydettiğinde oluşturulur. Mevcut kayıt okunarak açılırsa otomatik doldurma öncesi değiştirme onayı istenir.

## Veri ve yedekleme

Kayıtlar ve fotoğraflar `data/market.sqlite3` dosyasında kalıcı tutulur. Mac başlangıç dosyası kullanıldığında asıl veri `~/Library/Application Support/Sancakli Market/market.sqlite3` konumundadır. Bu klasörler Git'e eklenmez. `MARKET_DB` ile başka bir veri dosyası kullanılabilir.

Uygulama her açılışta, mevcut veritabanını değiştirmeden önce SQLite’ın güvenli yedekleme yöntemiyle bir kopya oluşturur. Yedekler asıl veritabanının yanındaki `backups` klasörüne yazılır ve en yeni 30 açılış yedeği saklanır. Genel bakış ekranı son yedek dosyasını ve klasör konumunu gösterir. `MARKET_BACKUP_DIR` ile başka bir yedek klasörü seçilebilir. Yedekleme başarısız olursa uygulama terminalde açık bir hata gösterir ve veritabanını yine açmayı dener.
Firma adı, faturanın tüm alanları ve ürün satırları, ödeme tutarı/tarihi/ilgili faturası Düzelt düğmeleriyle değiştirilebilir. Önceki kayıtlar edits tablosunda saklanır. Fatura toplamı yapılmış ödemelerin altına indirilemez; önce yanlış ödeme düzeltilmelidir. Firma, fatura ve ödemeler Sil düğmesiyle onay sonrası silinir. Firma/fatura silerken bağlı fatura, ödeme ve ürün satırı sayıları gösterilir ve birlikte silinir. Ödeme silinince borç geri açılır. Silinen kayıtlar denetim geçmişinde tutulur; arayüzden geri alma yoktur. Ürün satırları fatura düzeltme ekranındaki Sil düğmesiyle kaldırılır; fatura toplamı kullanıcı tarafından ayrıca kontrol edilir. Yedek yükleme arayüzü ve dışarıya bildirim gönderme henüz yoktur. Kullanıcının tercihiyle şifreli giriş eklenmemiştir.

Fiyat artış ve düşüşlerinin tamamı (1 kuruş fark dahil) ana ekranda onay bekleyen bildirim olarak gösterilir. Değişimi onayla kontrol edildiğini kaydeder; fatura tutarını veya ödemeleri değiştirmez. Onaylanmış karşılaştırmanın ürün/birim/firma/tarih/fiyat bilgisi değişirse yeniden onay gerekir. Yanlış bilgi Faturayı düzelt ile düzeltilir. Bildirimler uygulama içindedir.

## Doğrulama

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
```

Testler geçici veritabanıyla vade, kısmi ödeme, özetler, otomatik yedekleme, fiyat karşılaştırması ve hatalı kayıtları doğrular. Gerçek fatura fotoğrafıyla OCR doğruluğu ayrıca kontrol edilmelidir.

## Ürün kalemlerini denetleme

Fotoğraftan aktarılan faturalarda tespit edilen sıra numaraları ve kesin KDV dahil satır toplamları saklanır. Eksik/tekrarlanan sıra no, miktar × birim fiyat uyumsuzluğu veya fatura toplamı uyuşmazlığı varsa kayıt engellenir; elle düzeltip kontrol tekrar geçmelidir. İki ondalıklı birim fiyatın miktarla çarpımında oluşabilen yuvarlama payı dikkate alınır. Miktarı veya satır toplamını değiştirince son birim fiyat yeniden hesaplanır. Eski kayıtların miktarları tahmin edilmez; boş kalabilir ve düzeltirken tamamlanır.

Bu kontroller görüntüde hiç algılanmayan ürünlerin adını doğrulayamaz; aynı toplamı veren yanlış ürün adı/miktarı da mümkündür. Kontrolün geçmesi OCR'nin yüzde yüz doğru olduğu anlamına gelmez. Gerçek fatura ve ham OCR metniyle doğrulama gerekir. Okunamayan satırlar başka ürünle otomatik doldurulmaz. Yeni faturalarda toplam kalem sayısı zorunludur. Önceki sürümde oluşturulan ve OCR kontrol bilgisi taşımayan eski kayıtlar, geriye dönük düzeltme yapılabilmesi için açılmaya devam eder.
