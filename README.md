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

## Çift tıklamayla masaüstünden açma

Mac’te `Mac-Uygulamasini-Olustur.command` dosyasına çift tıklayın. Kurulum Homebrew yoksa sabitlenmiş resmi kurulum dosyasını güvenlik özetiyle doğrulayıp kurar; ardından Python 3.13, Tk masaüstü desteği, Tesseract, Türkçe OCR paketi, HEIC fotoğraf desteği ve PyInstaller eksiklerini kendisi indirir. Uygulamayı bu Mac üzerinde oluşturur, yerel olarak imzalar, `~/Applications/Sancakli Market.app` konumuna kurar ve masaüstünde çift tıklanabilen **Sancakli Market** kısayolunu oluşturur. İlk kurulumda internet bağlantısı ve macOS’un standart parola/yönetici onayı gerekebilir; ayrıca program arayıp indirmeniz gerekmez. Küçük kontrol penceresi tarayıcıyı otomatik açar; işi bitirince **Uygulamayı Kapat** düğmesini kullanın. Güncelleme sonrası oluşturma dosyasını yeniden çalıştırmak mevcut kayıtları silmez.

Windows bilgisayarda önce indirilen ZIP dosyasına sağ tıklayıp **Tümünü Ayıkla** seçeneğini kullanın; kurulum ZIP’in içinden çalıştırılamaz. Çıkarılan depo klasöründeki `Windows-Kurulum.cmd` dosyasına çift tıklayın. Kurulum Python 3.12 yoksa Windows Paket Yöneticisi ile otomatik kurar; Tesseract belge okuma motorunu, doğrulanmış Türkçe ve İngilizce dil dosyalarını ve uygulama paketleme araçlarını kendisi indirir. Ardından tek bir `Sancakli-Market.exe` üretir, `%LOCALAPPDATA%\Programs\Sancakli Market` içine kurar ve masaüstü ile Başlat menüsüne **Sancaklı Market** kısayolu ekler. İlk kurulumda internet bağlantısı ve Windows’un standart yönetici onayı gerekebilir; ayrıca program arayıp indirmeniz gerekmez. Sonraki normal açılışlarda internet, Python komutu veya PowerShell kullanılmaz.

Masaüstü paketleri veriyi uygulama dosyasının içinde tutmaz. Mac verisi `~/Library/Application Support/Sancakli Market`, Windows verisi `%LOCALAPPDATA%\Sancakli Market` altında kalır. Uygulamayı güncellemek kayıtları değiştirmez. Mac ve Windows kurulum dosyalarının ikisi de fotoğraf ve taranmış PDF okumasını hazırlar; yazı katmanlı dijital PDF okuması ve diğer cari işlemler de uygulama paketinde çalışır.

## Kullanım

1. Firmalar bölümünde tedarikçiyi kaydedin.
2. Fatura ekle ile firma, benzersiz fatura numarası, tarih ve toplamı girin.
3. Firma ekleme veya düzeltme ekranında varsayılan vade belirleyin. Yeni faturada firma seçilince bu gün sayısı otomatik gelir. Faturaya özel vade gününü veya ödeme tarihini değiştirebilirsiniz. Vade fatura tarihinden hesaplanır; günler takvim günüdür, hafta sonu kaydırılmaz.
4. İsterseniz PDF veya fotoğraf yükleyin ve yazı okuma düğmesini kullanın. Faturayı oku ve alanları doldur düğmesi, okunabilen tarih, numara, ödenecek toplam, mevcut firma eşleşmesi ve ürün satırlarını taslak olarak doldurur. “Faturadaki toplam ürün kalemi” alanını faturanın son sıra numarasıyla karşılaştırın; tek bir sıra eksikken kayıt engellenir. Vade elle girilir. Bilgileri kontrol edip onaylayın.
5. Fiyat takibi için ürün kodu, ad, gramaj/ambalaj, miktar ve fiyat birimini kontrol edin. Karşılaştırma aynı firma içinde önce ürün koduyla yapılır; kod yoksa normalleştirilmiş ad ve ambalaj kullanılır. Belirsiz benzer adlar Fiyat değişimleri bölümünde Aynı ürün/Farklı ürün onayına sunulur. Onaylı eşleştirmeler hatırlanır ve Kararı kaldır ile geri alınabilir. Farklı gramaj veya fiyat birimleri sessizce birleştirilmez.
6. **Ödeme kaydet** düğmesiyle firmayı, tutarı ve tarihi seçin. Ödeme firmanın toplam cari borcundan düşer. Takvim, kalan borcu hesaplarken ödeme tutarını en eski vadeden başlayarak hesaba katar. Daha önce faturaya veya açılış bakiyesine bağlanmış ödemeler kendi kayıtlarında kalır.
7. Genel bakıştaki ödeme takviminde **Günlük**, **Haftalık** veya **Aylık** görünümü seçin. Takvim seçili dönemin kalan borcunu, geciken tutarı ve firma toplamlarını gösterir. Firma filtresi üç görünümde de çalışır. Haftalar pazartesi başlar. Haftalık veya aylık takvimde bir güne tıklayarak o günün ödeme ayrıntılarını açabilirsiniz.
8. Firmalar bölümündeki Hesap geçmişi düğmesiyle o firmaya ait faturaları, ödemeleri ve kalan cariyi birlikte görüntüleyin. Fiyat değişimleri bölümündeki Fiyat geçmişi düğmesi ürünün tarih sıralı fiyatlarını ve yüzde değişimlerini gösterir. Ürün fiyat aramada **Tedarikçileri karşılaştır** görünümü her firmanın son karşılaştırılabilir alışını fiyat sırasıyla gösterir. Ürün adları ve ölçü birimleri eşleştiğinde, koli adedi ürün adında veya birimde belirliyse fiyat adet başına hesaplanır. Örneğin `Süt 1 L x12` ve `Süt 24x1 L` kolileri karşılaştırılabilir. Sonuçlarda alış tarihi, ambalaj, fatura fiyatı ve kaynak fatura bulunur.
9. Ürün arama bölümünde ad veya ürün koduyla geçmiş alış fiyatlarını bulun. Kontrol bekleyenler bölümü onaysız fiyat değişimlerini, belirsiz eşleştirmeleri ve geçmişi bulunamayan ürünleri tek listede toplar.
10. Toplu PDF ile en fazla 10 ayrı faturayı sıraya alın. Her PDF bir faturadır; uygulama sıradaki dosyayı okur, siz kontrol edip kaydedince sonraki dosyayı açar.
11. Eski dönemden kalan firma borcu için Firmalar bölümündeki Açılış bakiyesi düğmesini kullanın. Kayıt ve vade tarihini girin; bakiye ödeme ajandasında görünür ve ödeme kaydedildikçe azalır.
12. Mal iadesi veya alacak belgesi için İade / alacak düğmesini kullanıp ilgili faturayı seçin. Tutar faturanın kalan borcundan düşer; günlük ödeme planı ve firma carisi aynı anda güncellenir.

Mac ve Windows masaüstü sürümlerinde fatura kaydetme onayı Sancaklı Market’in kendi penceresinde açılır. Pencerede firma, fatura numarası ve toplam gösterilir. Bilgileri karşılaştırdığınızı belirten kutuyu işaretleyip **Onayla ve kaydet** düğmesine basın. **Vazgeç** ile fatura formuna dönebilirsiniz.

Firmalar bölümünde **Arşivle** ile bir firmayı yeni fatura listesinden kaldırabilirsiniz. **Arşivdeki firmalar** altında geçmiş hesap ve açık borçlar erişilebilir. Bir fatura, ödeme veya cari hareket silindiğinde **Silmeyi geri al** düğmesi son silme işlemini geri getirir. Bağlı kayıtlar geri almayı engelleyecek şekilde değişmişse uygulama bunu bildirir. Aynı tedarikçi için iki kart açılmışsa taşınacak kartta **Birleştir** düğmesine basıp kullanılacak kartı seçin. Onaydan önce bakiyeleri, taşınacak kayıtları ve varsa çakışan fatura numaralarını görürsünüz. İşlemden önce yerel yedek alınır. Kullanılacak kartın vade ve okuma ayarları geçerli olur.

Günlük özet İstanbul tarihini kullanır. Gelecek 7 gün yarın başlar. Gecikenler yalnızca kalan borcu içerir.

Firma kartına satıcının 10 haneli **Vergi kimlik numarasını** kaydedebilirsiniz. Okuma satıcıya ait numarayı belirlerse eşleşen firma seçilir. Alıcıya ait numara eşleştirmede kullanılmaz. Satıcı numarası belirsizse mevcut firma tanıma kuralları kullanılır. Okunan satıcı numarası kayıtlı firmayla uyuşmazsa firma seçimini kontrol edin.

E-faturada **ETTN** okunursa formda otomatik görünür. Faturadaki ETTN’yi elle de girebilirsiniz. Aynı ETTN kayıtlıysa farklı bir dosya veya firma seçilse bile ikinci kayıt engellenir. Önceden kaydedilen faturaların ETTN’sini düzenleme ekranından ekleyebilirsiniz.

## PDF ve fotoğraf okuma

Tesseract komutu PATH üzerinde olmalıdır. Türkçe için `tur` dil verisi önerilir; yoksa İngilizce motor kullanılır ve Türkçe harflerde doğruluk düşer. Debian/Ubuntu üzerinde sistem paketleri: `tesseract-ocr`, `tesseract-ocr-tur`. Motor yoksa elle kayıt kullanılabilir.

Dosyalar dış servise gönderilmez. PDF sayfaları uygulamanın içinde, bilgisayarınızda kayıpsız görüntülere çevrilir. PDF, HEIC/HEIF, JPEG, PNG ve WebP biçimleri; en fazla 6 sayfa ve toplam 8 MB desteklenir. Şifreli PDF açılamaz; önce şifresiz bir kopyası kaydedilmelidir. Telefon ekran görüntülerinde ve dar fiş fotoğraflarında açık belge bölgesi otomatik kırpılır. Yan çekilmiş dar belgeler iki yönde çevrilerek de okunur; kayıtta eklenen asıl sayfalar korunur.

Çevrimdışı ayrıştırıcı Türkçe etiketli (Fatura Tarihi, Belge/Fatura No, Ödenecek Tutar) alanları ve sıra no, ürün kodu, ürün adı, miktar, birim, birim fiyat, KDV oranı, KDV tutarı, satır net tutarı sırasındaki tabloları destekler. Yazı katmanı bulunan dijital PDF’lerde ürün adı, ürün kodu ve hücre değerleri PDF’nin orijinal metninden alınır; görsel OCR yalnızca eksik alanları tamamlar. Fotoğraf ve taranmış PDF’lerde Tesseract’ın kelime konumları kullanılarak çizgili tablodaki ayrı sütunlar tekrar ürün satırlarına dönüştürülür. Farklı düzenler ve bulanık belgeler eksik ayrıştırılabilir; sıra numarası, satır toplamı ve fatura toplamı kontrolleri eksik kaydı engeller. Birim fiyat (faturada yazan Tutarı + KDV T.) / ücretli miktar olarak dört ondalıkla gösterilir ve taslağa aktarılır; iki fatura değeri ayrı saklanır. Bu, satır tutarına yansıtılmış iskontoyu ikinci kez düşmez. Sonradan ayrıca uygulanan indirim/masraf varsa elle kontrol gerekir. Fatura dosyası ekle düğmesi mevcut sayfaları koruyarak yeni PDF veya fotoğraf ekler; sayfalar tek tek veya birlikte seçilebilir. Her sayfa önizlemede görünür ve Kaldır ile ayrı silinebilir. Dosya seçimini iptal etmek veya sınırı aşmak önceki sayfaları silmez. Aynı sayfa tekrar seçildiğinde çoğaltılmaz. Okutmadan önce tüm sayfalar eklenmiş olmalıdır. Örtüşen aynı numaralı satırlar kod, miktar ve tutarları uyuşuyorsa tekilleştirilir. Farklı OCR düzenlerinde birden fazla kez aynı çıkan satır, tek bir bozuk okumaya tercih edilir ve kullanıcıya kontrol uyarısı gösterilir; eşit çelişkiler otomatik seçilmez. Kayıtlar yalnızca kullanıcı kontrol kutusuyla onaylayıp kaydettiğinde oluşturulur. Mevcut kayıt okunarak açılırsa otomatik doldurma öncesi değiştirme onayı istenir.

Firmalar bölümündeki **Fatura sütun düzeni** alanında, ilgili tedarikçinin faturalarında KDV tutarı ile KDV hariç satır tutarının sırasını kaydedebilirsiniz. Okuma sırasında uygulama tedarikçiyi tanırsa ayarı otomatik kullanır. Tedarikçi tanınmazsa fatura formunda okuma düzeni için firmayı seçebilirsiniz. Özel düzenin satırları fatura toplamıyla uyuşmazsa uygulama genel okumayı ve kontrol uyarısını gösterir.

Bedelsiz gelen miktarı ilgili ücretli ürünün **Bedelsiz miktar** alanına elle girin. **Ücretli miktar** alanına yalnızca faturada bedeli olan miktarı yazın. Bedelsiz miktar fatura toplamını, karşılaştırılan birim fiyatı ve fiyat uyarılarını etkilemez. Belge bedelsiz kalemi ayrı gösteriyorsa kendi sıra numarasıyla yeni satır açın ve **Bu satır tamamen bedelsiz** kutusunu işaretleyin. Satır kalem sayısına girer ve tutarı sıfır olur.

## Veri ve yedekleme

Kayıtlar ve fotoğraflar `data/market.sqlite3` dosyasında kalıcı tutulur. Mac başlangıç dosyası kullanıldığında asıl veri `~/Library/Application Support/Sancakli Market/market.sqlite3` konumundadır. Bu klasörler Git'e eklenmez. `MARKET_DB` ile başka bir veri dosyası kullanılabilir.

Uygulama açılışta ve açık kaldığı sürece saatte bir SQLite yedeği oluşturur. Genel bakış ekranındaki **Şimdi yedekle** düğmesiyle o anki kayıtları da yedekleyebilirsiniz. Yedekler varsayılan olarak veritabanının yanındaki `backups` klasörüne yazılır ve en yeni 30 kopya saklanır. Genel bakış ekranı son başarılı yedeğin zamanını ve klasör konumunu gösterir. Klasörü değiştir düğmesi Mac veya Windows klasör seçicisini açar, seçimi `settings.json` içinde hatırlar ve yeni konuma hemen bir yedek oluşturur. Varsayılan klasöre dönmek de mümkündür. Teknik kurulumlarda `MARKET_BACKUP_DIR` aynı seçimi ortam değişkeniyle yapabilir. Yedekleme başarısız olursa hata Genel bakış ekranında görünür. Yedek klasörü olarak ayrı bir USB disk de seçilebilir.
Genel bakış ekranından eski bir yedek seçilip geri yüklenebilir. Geri yükleme başlamadan hemen önce mevcut veriler ayrıca yeni bir güvenlik yedeğine alınır. Seçilen dosyanın bütünlüğü ve uygulama tabloları doğrulanmadan asıl veritabanı değiştirilmez.
Firma adı, faturanın tüm alanları ve ürün satırları, açılış bakiyesi, iade/alacak kaydı ve ödeme bilgileri Düzelt düğmeleriyle değiştirilebilir. Önceki kayıtlar edits tablosunda saklanır. Fatura toplamı yapılmış ödeme ve iadelerin altına; açılış bakiyesi de yapılmış ödemelerin altına indirilemez. Firma, fatura, açılış/iade ve ödemeler Sil düğmesiyle onay sonrası silinir. Firma/fatura silerken bağlı fatura, ödeme, cari hareket ve ürün satırı sayıları gösterilir ve birlikte silinir. Ödeme silinince borç geri açılır; iade silinince ilgili fatura borcu tekrar artar. Silinen kayıtlar denetim geçmişinde tutulur. Son silinen cari hareket, bağlı kayıtlar izin verdiğinde arayüzden geri alınabilir. Ürün satırları fatura düzeltme ekranındaki Sil düğmesiyle kaldırılır; fatura toplamı kullanıcı tarafından ayrıca kontrol edilir. Dışarıya bildirim gönderme bulunmaz. Kullanıcının tercihiyle şifreli giriş eklenmemiştir.

Firma, fatura, ödeme, açılış bakiyesi ve iade/alacak formlarında yapılan değişiklikler kısa bir gecikmeyle yerel veritabanına taslak olarak kaydedilir. Uygulama kapanırsa aynı form yeniden açıldığında alanlar, fatura kalemleri ve eklenen sayfalar geri getirilir. Eksik taslaklar cari bakiyeyi değiştirmez; form eksiksiz biçimde kesin kayda dönüştüğünde ilgili taslak otomatik silinir.

Birim fiyatın tam TL kısmı değiştiğinde artış veya düşüş onay bekleyen bildirim olarak gösterilir. Örneğin 4,10 TL’den 4,70 TL’ye geçiş bildirim oluşturmaz. 4,90 TL’den 5,00 TL’ye geçiş bildirim oluşturur. Fiyat geçmişinde tam tutarlar görünür. Değişimi onayla kontrol edildiğini kaydeder; fatura tutarını veya ödemeleri değiştirmez. Onaylanmış karşılaştırmanın ürün/birim/firma/tarih/fiyat bilgisi değişirse yeniden onay gerekir. Yanlış bilgi Faturayı düzelt ile düzeltilir. Bildirimler uygulama içindedir.

Aynı firmada aynı fatura numarası ikinci kez kaydedilemez. Aynı fatura dosyasının tekrar eklenmesi engellenir. Firma, tarih ve toplam başka bir faturayla aynıysa kullanıcıya mükerrer uyarısı gösterilir ve açık onay istenir. OCR ile okunup kullanıcı tarafından firması onaylanan tedarikçi başlıkları firma tanıma örneği olarak saklanır; sonraki aynı başlıklı faturalar doğru firmayla daha kolay eşleştirilir. Yeni tablo düzenlerinin ürün sütunlarını iyileştirmek için yine o firmadan örnek fatura gerekir.

## Doğrulama

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
```

Testler geçici veritabanıyla vade, kısmi ödeme, özetler, otomatik yedekleme, fiyat karşılaştırması ve hatalı kayıtları doğrular. Gerçek fatura fotoğrafıyla OCR doğruluğu ayrıca kontrol edilmelidir.

## Ürün kalemlerini denetleme

Fotoğraftan aktarılan faturalarda tespit edilen sıra numaraları ve kesin KDV dahil satır toplamları saklanır. Eksik/tekrarlanan sıra no, miktar × birim fiyat uyumsuzluğu veya fatura toplamı uyuşmazlığı varsa kayıt engellenir; elle düzeltip kontrol tekrar geçmelidir. Eski iki ondalıklı kayıtların kalem kontrolünde önceki yuvarlama payı korunur. Miktarı veya satır toplamını değiştirince son birim fiyat yeniden hesaplanır. Eski kayıtların miktarları tahmin edilmez; boş kalabilir ve düzeltirken tamamlanır.

Bu kontroller görüntüde hiç algılanmayan ürünlerin adını doğrulayamaz; aynı toplamı veren yanlış ürün adı/miktarı da mümkündür. Kontrolün geçmesi OCR'nin yüzde yüz doğru olduğu anlamına gelmez. Gerçek fatura ve ham OCR metniyle doğrulama gerekir. Okunamayan satırlar başka ürünle otomatik doldurulmaz. Yeni faturalarda toplam kalem sayısı zorunludur. Önceki sürümde oluşturulan ve OCR kontrol bilgisi taşımayan eski kayıtlar, geriye dönük düzeltme yapılabilmesi için açılmaya devam eder.
