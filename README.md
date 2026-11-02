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
4. İsterseniz fotoğraf yükleyin ve yazı okuma düğmesini kullanın. OCR metni yardımcıdır: alanlara elle aktarılır, ürün satırları otomatik ayrıştırılmaz. Bilgileri kontrol edip onaylayın.
5. Fiyat takibi için aynı marka/ürün adı ve ambalaj/fiyat birimini kullanın. KDV ve iskonto sonrası birim fiyatı girin. Karşılaştırma aynı firma içinde yapılır; farklı adlar ve birimler otomatik eşleştirilmez.
6. Fatura üzerinden tam veya kısmi ödeme kaydedin. Ödeme ilgili faturadan düşer; fazla ödeme kabul edilmez.

Günlük özet İstanbul tarihini kullanır. Gelecek 7 gün yarın başlar. Gecikenler yalnızca kalan borcu içerir.

## Fotoğraf okuma

Tesseract komutu PATH üzerinde olmalıdır. Türkçe için `tur` dil verisi önerilir; yoksa İngilizce motor kullanılır ve Türkçe harflerde doğruluk düşer. Debian/Ubuntu üzerinde sistem paketleri: `tesseract-ocr`, `tesseract-ocr-tur`. Motor yoksa elle kayıt kullanılabilir.

Fotoğraflar dış servise gönderilmez. JPEG, PNG, WebP ve en fazla 8 MB desteklenir.

## Veri ve yedekleme

Kayıtlar ve fotoğraflar `data/market.sqlite3` dosyasında kalıcı tutulur. Bu klasör Git'e eklenmez.
Sunucuyu durdurduktan sonra bu dosyayı güvenli bir yere kopyalayarak yedekleyin. `MARKET_DB` ile başka dosya kullanılabilir.
Firma adı, faturanın tüm alanları ve ürün satırları, ödeme tutarı/tarihi/ilgili faturası Düzelt düğmeleriyle değiştirilebilir. Önceki kayıtlar edits tablosunda saklanır. Fatura toplamı yapılmış ödemelerin altına indirilemez; önce yanlış ödeme düzeltilmelidir. Firmalar kartındaki Sil düğmesi yalnızca faturası olmayan firmaları onay sonrası siler; ödenmiş faturası olan firmalar da korunur. Fatura/ödeme silme, yedek yükleme arayüzü ve dışarıya bildirim gönderme henüz yoktur. Kullanıcının tercihiyle şifreli giriş eklenmemiştir.

Fiyat artış ve düşüşlerinin tamamı (1 kuruş fark dahil) ana ekranda onay bekleyen bildirim olarak gösterilir. Değişimi onayla kontrol edildiğini kaydeder; fatura tutarını veya ödemeleri değiştirmez. Onaylanmış karşılaştırmanın ürün/birim/firma/tarih/fiyat bilgisi değişirse yeniden onay gerekir. Yanlış bilgi Faturayı düzelt ile düzeltilir. Bildirimler uygulama içindedir.

## Doğrulama

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
```

Testler geçici veritabanıyla vade, kısmi ödeme, özetler, fiyat karşılaştırması ve hatalı kayıtları doğrular. Gerçek fatura fotoğrafıyla OCR doğruluğu ayrıca kontrol edilmelidir.
