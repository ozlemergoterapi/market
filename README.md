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
4. İsterseniz fotoğraf yükleyin ve yazı okuma düğmesini kullanın. Fotoğrafı oku ve alanları doldur düğmesi, okunabilen tarih, numara, ödenecek toplam, mevcut firma eşleşmesi ve ürün satırlarını taslak olarak doldurur. Vade elle girilir. Bilgileri kontrol edip onaylayın.
5. Fiyat takibi için aynı marka/ürün adı ve ambalaj/fiyat birimini kullanın. KDV ve iskonto sonrası birim fiyatı girin. Karşılaştırma aynı firma içinde yapılır; farklı adlar ve birimler otomatik eşleştirilmez.
6. Fatura üzerinden tam veya kısmi ödeme kaydedin. Ödeme ilgili faturadan düşer; fazla ödeme kabul edilmez.

Günlük özet İstanbul tarihini kullanır. Gelecek 7 gün yarın başlar. Gecikenler yalnızca kalan borcu içerir.

## Fotoğraf okuma

Tesseract komutu PATH üzerinde olmalıdır. Türkçe için `tur` dil verisi önerilir; yoksa İngilizce motor kullanılır ve Türkçe harflerde doğruluk düşer. Debian/Ubuntu üzerinde sistem paketleri: `tesseract-ocr`, `tesseract-ocr-tur`. Motor yoksa elle kayıt kullanılabilir.

Fotoğraflar dış servise gönderilmez. En fazla 6 JPEG/PNG/WebP sayfa, toplam 8 MB desteklenir.

Çevrimdışı ayrıştırıcı Türkçe etiketli (Fatura Tarihi, Belge/Fatura No, Ödenecek Tutar) alanları ve sıra no, ürün kodu, ürün adı, miktar, birim, birim fiyat, KDV oranı, KDV tutarı, satır net tutarı sırasındaki tabloları destekler. Farklı düzenler ve bulanık fotoğraflar eksik ayrıştırılabilir; eksikler ve toplam uyuşmazlığı ekranda belirtilir. Birim fiyat (satır neti + satır KDV) / miktar olarak dört ondalıkla hesaplanır; bu, satır tutarına yansıtılmış iskontoyu ikinci kez düşmez. Sonradan ayrıca uygulanan indirim/masraf varsa elle kontrol gerekir. Fotoğraf ekle düğmesi mevcut sayfaları koruyarak yeni fotoğraf ekler; sayfalar tek tek veya birlikte seçilebilir. Her sayfa önizlemede görünür ve Kaldır ile ayrı silinebilir. Dosya seçimini iptal etmek veya sınırı aşmak önceki sayfaları silmez. Aynı fotoğraf tekrar seçildiğinde çoğaltılmaz. Okutmadan önce tüm sayfalar eklenmiş olmalıdır. Örtüşen aynı numaralı satırlar tekilleştirilir. Kayıtlar yalnızca kullanıcı kontrol kutusuyla onaylayıp kaydettiğinde oluşturulur. Mevcut kayıt okunarak açılırsa otomatik doldurma öncesi değiştirme onayı istenir.

## Veri ve yedekleme

Kayıtlar ve fotoğraflar `data/market.sqlite3` dosyasında kalıcı tutulur. Bu klasör Git'e eklenmez.
Sunucuyu durdurduktan sonra bu dosyayı güvenli bir yere kopyalayarak yedekleyin. `MARKET_DB` ile başka dosya kullanılabilir.
Firma adı, faturanın tüm alanları ve ürün satırları, ödeme tutarı/tarihi/ilgili faturası Düzelt düğmeleriyle değiştirilebilir. Önceki kayıtlar edits tablosunda saklanır. Fatura toplamı yapılmış ödemelerin altına indirilemez; önce yanlış ödeme düzeltilmelidir. Firma, fatura ve ödemeler Sil düğmesiyle onay sonrası silinir. Firma/fatura silerken bağlı fatura, ödeme ve ürün satırı sayıları gösterilir ve birlikte silinir. Ödeme silinince borç geri açılır. Silinen kayıtlar denetim geçmişinde tutulur; arayüzden geri alma yoktur. Ürün satırları fatura düzeltme ekranındaki Sil düğmesiyle kaldırılır; fatura toplamı kullanıcı tarafından ayrıca kontrol edilir. Yedek yükleme arayüzü ve dışarıya bildirim gönderme henüz yoktur. Kullanıcının tercihiyle şifreli giriş eklenmemiştir.

Fiyat artış ve düşüşlerinin tamamı (1 kuruş fark dahil) ana ekranda onay bekleyen bildirim olarak gösterilir. Değişimi onayla kontrol edildiğini kaydeder; fatura tutarını veya ödemeleri değiştirmez. Onaylanmış karşılaştırmanın ürün/birim/firma/tarih/fiyat bilgisi değişirse yeniden onay gerekir. Yanlış bilgi Faturayı düzelt ile düzeltilir. Bildirimler uygulama içindedir.

## Doğrulama

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
```

Testler geçici veritabanıyla vade, kısmi ödeme, özetler, fiyat karşılaştırması ve hatalı kayıtları doğrular. Gerçek fatura fotoğrafıyla OCR doğruluğu ayrıca kontrol edilmelidir.
