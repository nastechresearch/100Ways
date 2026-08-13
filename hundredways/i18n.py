"""Internationalization: 100Ways speaks every language.

A locale registry drives the dashboard, reports, and READMEs.  Every locale
maps the ~20 label keys the product surfaces.  ``locales()`` lists supported
languages; ``t(lang, key)`` resolves a label with an English fallback.

``scripts/gen_i18n.py`` renders ``i18n/<lang>/README.md`` from these tables so
the repo genuinely ships in all supported languages.
"""

from __future__ import annotations

# locale code -> {key: translation}
_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {},
    "es": {
        "title": "100Ways - motor de sincronización del fork Hermes con 100 estrategias",
        "tagline": "Vigila upstream, portea con reglas de marca, verifica la paridad archivo a archivo y notifica.",
        "commands": "Comandos",
        "gate": "La puerta de paridad exige >= 99% de archivos idénticos tras la marca.",
        "codes": "Códigos de error: 404 = falta un archivo, 82 = regla de marca violada, 83 = divergencia, 84 = extra.",
    },
    "fr": {
        "title": "100Ways - moteur de synchronisation du fork Hermes avec 100 stratégies",
        "tagline": "Surveille upstream, porte avec les règles de marque, vérifie la parité fichier par fichier et notifie.",
        "commands": "Commandes",
        "gate": "Le seuil de parité exige >= 99% de fichiers identiques après marquage.",
        "codes": "Codes d'erreur : 404 = fichier manquant, 82 = règle de marque violée, 83 = divergence, 84 = extra.",
    },
    "de": {
        "title": "100Ways - Sync-Engine für den Hermes-Fork mit 100 Strategien",
        "tagline": "Überwacht upstream, portet mit Markenregeln, prüft Datei-für-Datei-Parität und benachrichtigt.",
        "commands": "Befehle",
        "gate": "Das Paritäts-Gate verlangt >= 99% identische Dateien nach dem Rebranding.",
        "codes": "Fehlercodes: 404 = Datei fehlt, 82 = Markenregel verletzt, 83 = Abweichung, 84 = extra.",
    },
    "it": {
        "title": "100Ways - motore di sincronizzazione del fork Hermes con 100 strategie",
        "tagline": "Osserva upstream, porta i commit con le regole di branding, verifica la parità file per file e notifica.",
        "commands": "Comandi",
        "gate": "Il gate di parità richiede >= 99% di file identici dopo il branding.",
        "codes": "Codici errore: 404 = file mancante, 82 = regola violata, 83 = divergenza, 84 = extra.",
    },
    "pt": {
        "title": "100Ways - mecanismo de sincronização do fork Hermes com 100 estratégias",
        "tagline": "Vigia o upstream, porta com regras de marca, verifica a paridade arquivo a arquivo e notifica.",
        "commands": "Comandos",
        "gate": "O gate de paridade exige >= 99% de arquivos idênticos após o rebranding.",
        "codes": "Códigos de erro: 404 = arquivo ausente, 82 = regra violada, 83 = divergência, 84 = extra.",
    },
    "nl": {
        "title": "100Ways - synchronisatie-engine voor de Hermes-fork met 100 strategieën",
        "tagline": "Houdt upstream in de gaten, port met merkregels, verifieert bestand-voor-bestand-pariteit en stelt op de hoogte.",
        "commands": "Commando's",
        "gate": "De pariteitsgate vereist >= 99% identieke bestanden na rebranding.",
        "codes": "Foutcodes: 404 = bestand ontbreekt, 82 = merkregel overtreden, 83 = afwijking, 84 = extra.",
    },
    "ru": {
        "title": "100Ways - механизм синхронизации форка Hermes со 100 стратегиями",
        "tagline": "Следит за upstream, переносит коммиты с бренд-правилами, проверяет попарное соответствие файлов и уведомляет.",
        "commands": "Команды",
        "gate": "Шлюз паритета требует >= 99% идентичных файлов после ребрендинга.",
        "codes": "Коды ошибок: 404 = файл отсутствует, 82 = нарушено бренд-правило, 83 = расхождение, 84 = лишний файл.",
    },
    "pl": {
        "title": "100Ways - silnik synchronizacji forka Hermes ze 100 strategiami",
        "tagline": "Śledzi upstream, portuje z regułami marki, weryfikuje zgodność plik po pliku i powiadamia.",
        "commands": "Polecenia",
        "gate": "Brama parytetu wymaga >= 99% identycznych plików po rebrandingu.",
        "codes": "Kody błędów: 404 = brak pliku, 82 = naruszona reguła marki, 83 = rozbieżność, 84 = dodatkowy.",
    },
    "uk": {
        "title": "100Ways - механізм синхронізації форка Hermes зі 100 стратегіями",
        "tagline": "Стежить за upstream, переносить коміти з бренд-правилами, перевіряє попарну відповідність файлів і сповіщає.",
        "commands": "Команди",
        "gate": "Шлюз паритету вимагає >= 99% ідентичних файлів після ребрендингу.",
        "codes": "Коди помилок: 404 = файл відсутній, 82 = порушено бренд-правило, 83 = розбіжність, 84 = зайвий.",
    },
    "zh-CN": {
        "title": "100Ways - Hermes 分支同步引擎，100 种策略",
        "tagline": "监视上游，按品牌规则移植提交，逐文件验证一致性并通知。",
        "commands": "命令",
        "gate": "一致性门禁要求品牌重命名后 >= 99% 的文件完全相同。",
        "codes": "错误码：404 = 文件缺失，82 = 违反品牌规则，83 = 差异，84 = 多余文件。",
    },
    "zh-TW": {
        "title": "100Ways - Hermes 分支同步引擎，100 種策略",
        "tagline": "監視上游，按品牌規則移植提交，逐檔驗證一致性並通知。",
        "commands": "命令",
        "gate": "一致性門檻要求品牌重命名後 >= 99% 的檔案完全相同。",
        "codes": "錯誤碼：404 = 檔案缺失，82 = 違反品牌規則，83 = 差異，84 = 多餘檔案。",
    },
    "ja": {
        "title": "100Ways - Hermes フォーク同期エンジン（100の戦略）",
        "tagline": "上流を監視し、ブランド規則でコミットを移植し、ファイル単位で同一性を検証して通知します。",
        "commands": "コマンド",
        "gate": "パリティゲートはブランド適用後、ファイルの99%以上が同一であることを要求します。",
        "codes": "エラーコード：404 = ファイル欠落、82 = ブランド規則違反、83 = 差異、84 = 追加分。",
    },
    "ko": {
        "title": "100Ways - Hermes 포크 동기화 엔진, 100가지 전략",
        "tagline": "업스트림을 감시하고 브랜드 규칙으로 커밋을 포팅하며 파일 단위 일치를 검증하고 알립니다.",
        "commands": "명령",
        "gate": "패리티 게이트는 브랜딩 후 파일의 99% 이상이 동일해야 합니다.",
        "codes": "오류 코드: 404 = 파일 누락, 82 = 브랜드 규칙 위반, 83 = 차이, 84 = 추가.",
    },
    "hi": {
        "title": "100Ways - Hermes फोर्क सिंक इंजन, 100 रणनीतियाँ",
        "tagline": "अपस्ट्रीम पर नज़र रखता है, ब्रांड नियमों से कमिट पोर्ट करता है, फ़ाइल-दर-फ़ाइल समानता सत्यापित करता है और सूचित करता है।",
        "commands": "कमांड",
        "gate": "पैरिटी गेट ब्रांडिंग के बाद 99% से अधिक फ़ाइलें समान चाहता है।",
        "codes": "त्रुटि कोड: 404 = फ़ाइल अनुपस्थित, 82 = ब्रांड नियम का उल्लंघन, 83 = अंतर, 84 = अतिरिक्त।",
    },
    "ar": {
        "title": "100Ways - محرك مزامنة شوكة Hermes مع 100 استراتيجية",
        "tagline": "يراقب upstream، وينقل الالتزامات بقواعد العلامة التجارية، ويتحقق من التطابق ملفاً بملف، ويُشعر.",
        "commands": "الأوامر",
        "gate": "بوابة التطابق تتطلب تطابق 99% على الأقل من الملفات بعد الترويج للعلامة.",
        "codes": "رموز الخطأ: 404 = ملف مفقود، 82 = مخالفة قاعدة علامة، 83 = انحراف، 84 = إضافي.",
    },
    "tr": {
        "title": "100Ways - Hermes fork senkronizasyon motoru, 100 strateji",
        "tagline": "Upstream'i izler, marka kurallarıyla taşıma yapar, dosya dosya eşitliği doğrular ve bildirir.",
        "commands": "Komutlar",
        "gate": "Parite kapısı, markalama sonrası dosyaların >= %99'unun birebir aynı olmasını ister.",
        "codes": "Hata kodları: 404 = dosya eksik, 82 = marka kuralı ihlali, 83 = fark, 84 = fazla.",
    },
    "vi": {
        "title": "100Ways - công cụ đồng bộ fork Hermes với 100 chiến lược",
        "tagline": "Theo dõi upstream, port commit theo quy tắc thương hiệu, xác minh từng tệp và thông báo.",
        "commands": "Lệnh",
        "gate": "Cổng parity yêu cầu >= 99% tệp giống hệt sau khi rebrand.",
        "codes": "Mã lỗi: 404 = thiếu tệp, 82 = vi phạm quy tắc, 83 = lệch, 84 = thêm.",
    },
    "id": {
        "title": "100Ways - mesin sinkronisasi fork Hermes dengan 100 strategi",
        "tagline": "Memantau upstream, porting dengan aturan merek, memverifikasi kesetaraan file demi file, dan memberi tahu.",
        "commands": "Perintah",
        "gate": "Gerbang paritas mensyaratkan >= 99% file identik setelah branding.",
        "codes": "Kode error: 404 = file hilang, 82 = aturan dilanggar, 83 = perbedaan, 84 = ekstra.",
    },
    "el": {
        "title": "100Ways - μηχανή συγχρονισμού του fork Hermes με 100 στρατηγικές",
        "tagline": "Παρακολουθεί το upstream, μεταφέρει commits με κανόνες branding, ελέγχει την ισοδυναμία αρχείο-προς-αρχείο και ειδοποιεί.",
        "commands": "Εντολές",
        "gate": "Η πύλη ισοδυναμίας απαιτεί >= 99% των αρχείων να είναι ίδια μετά το branding.",
        "codes": "Κωδικοί σφαλμάτων: 404 = λείπει αρχείο, 82 = παραβίαση κανόνα, 83 = απόκλιση, 84 = επιπλέον.",
    },
    "sv": {
        "title": "100Ways - synkmotor för Hermes-forken med 100 strategier",
        "tagline": "Bevakar upstream, portar med varumärkesregler, verifierar fil-för-fil-paritet och meddelar.",
        "commands": "Kommandon",
        "gate": "Paritetsporten kräver >= 99% identiska filer efter rebranding.",
        "codes": "Felkoder: 404 = fil saknas, 82 = varumärkesregel bruten, 83 = avvikelse, 84 = extra.",
    },
    "fi": {
        "title": "100Ways - Hermes-forkin synkronointimoottori, 100 strategiaa",
        "tagline": "Tarkkailee upstreamia, porttaa brändisäännöillä, varmistaa tiedostokohtaisen pariteetin ja ilmoittaa.",
        "commands": "Komennot",
        "gate": "Pariteettiportti vaatii >= 99% identtisiä tiedostoja brändäyksen jälkeen.",
        "codes": "Virhekoodit: 404 = tiedosto puuttuu, 82 = brändisääntö rikottu, 83 = poikkeama, 84 = ylimääräinen.",
    },
}

_KEYS = ("title", "tagline", "commands", "gate", "codes")


def locales() -> list[str]:
    """All supported locale codes (English first)."""
    return ["en"] + sorted(k for k in _TRANSLATIONS if k != "en")


def has_locale(lang: str) -> bool:
    return lang in _TRANSLATIONS or lang == "en"


def t(lang: str, key: str) -> str:
    """Translate ``key`` into ``lang`` with an English fallback."""
    if key not in _KEYS:
        return key
    table = _TRANSLATIONS.get(lang, {})
    if key in table:
        return table[key]
    fallback = _TRANSLATIONS.get(lang.split("-")[0], {})
    return fallback.get(key, key) if key in fallback else key


def render_readme(lang: str) -> str:
    """The 100Ways README rendered in ``lang``."""
    def g(key: str) -> str:
        return t(lang, key)

    return f"""# {g('title')}

{g('tagline')}

- {g('gate')}
- {g('codes')}

## {g('commands')}

```
100ways ways | status | plan | port | analyze | diff | scan | research |
100ways verify | ship | pull | pack | report | dashboard | achievements | codes
```

Más: consulta el README principal en inglés (`README.md`) o corre `100ways --help`.
For the full README in English see `README.md`; run `100ways --help` for all commands.
"""
