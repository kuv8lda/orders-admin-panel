"""Переводы интерфейса: словари RU/EN, использование ключей, поведение i18n.js и common.js."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
I18N_JS = (ROOT / "js" / "i18n.js").read_text(encoding="utf-8")
PLURAL_SUFFIXES = (".one", ".few", ".many", ".other")
# Ключи, которые собираются в коде динамически: status.${s}, action.${s}, API_MESSAGES.
DYNAMIC_PREFIXES = ("status.", "action.", "api.")
PLURAL_FORMS = {"ru": {"one", "few", "many", "other"}, "en": {"one", "other"}}


def _parse_dicts() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for lang in ("ru", "en"):
        match = re.search(rf"\n  {lang}: \{{\n(.*?)\n  \}},", I18N_JS, re.S)
        assert match, f"нет блока {lang} в js/i18n.js"
        entries: dict[str, str] = {}
        for line in match.group(1).splitlines():
            m = re.fullmatch(r'    ("[\w.]+"): (".*"),', line)
            assert m, f"строка словаря {lang} не в строгом формате: {line!r}"
            key = json.loads(m.group(1))
            assert key not in entries, f"повтор ключа {key} в {lang}"
            entries[key] = json.loads(m.group(2))
        result[lang] = entries
    return result


DICTS = _parse_dicts()


def _base(key: str) -> str:
    for suffix in PLURAL_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


class _Collector(HTMLParser):
    """Собирает data-i18n (с текстом элемента) и data-i18n-attr (с текущими значениями)."""

    def __init__(self) -> None:
        super().__init__()
        self.text_keys: list[tuple[str, str]] = []
        self.attr_keys: list[tuple[str, str, str]] = []
        self.title_keys: list[str] = []
        self._stack: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for frame in self._stack:
            frame["has_child"] = True
        if "data-title-key" in a:
            self.title_keys.append(a["data-title-key"])
        if "data-i18n-attr" in a:
            for pair in a["data-i18n-attr"].split(";"):
                attr, key = pair.split(":")
                self.attr_keys.append((key, attr, a.get(attr, "")))
        if "data-i18n" in a:
            self._stack.append({"key": a["data-i18n"], "text": "", "has_child": False, "tag": tag})

    def handle_data(self, data):
        if self._stack:
            self._stack[-1]["text"] += data

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1]["tag"] == tag:
            frame = self._stack.pop()
            assert not frame["has_child"], f"data-i18n={frame['key']} содержит вложенные теги"
            self.text_keys.append((frame["key"], frame["text"].strip()))


def _collect(path: str) -> _Collector:
    parser = _Collector()
    text = (ROOT / path).read_text(encoding="utf-8")
    # Jinja-блоки не нужны парсеру
    text = re.sub(r"\{%.*?%\}", "", text)
    text = re.sub(r"\{\{.*?\}\}", "", text)
    parser.feed(text)
    return parser


def _used_keys() -> set[str]:
    used: set[str] = set()
    for page in ("admin.html", "dashboard.html"):
        c = _collect(page)
        used.update(k for k, _ in c.text_keys)
        used.update(k for k, _, _ in c.attr_keys)
        used.update(c.title_keys)
    chrome = (ROOT / "js" / "common.js").read_text(encoding="utf-8")
    sources = [chrome]
    sources += [(ROOT / "js" / n).read_text(encoding="utf-8") for n in ("dashboard.js", "admin.js")]
    for src in sources:
        used.update(re.findall(r'\bt\(\s*["`]([\w.]+)["`]', src))
        used.update(re.findall(r'\btn\(\s*["`]([\w.]+)["`]', src))
        used.update(re.findall(r'data-i18n(?:-attr)?="[^"]*?([\w.]+)"', src))
        used.update(re.findall(r'dataset\.i18n = "([\w.]+)"', src))
        for pair in re.findall(r'data-i18n-attr="([^"]+)"', src):
            used.update(p.split(":")[1] for p in pair.split(";"))
    return used


# ---- словари ------------------------------------------------------------------


def test_ru_and_en_have_same_keys():
    ru = {_base(k) for k in DICTS["ru"]}
    en = {_base(k) for k in DICTS["en"]}
    assert ru == en, (sorted(ru - en), sorted(en - ru))


def test_plural_forms_are_complete():
    for lang, forms in PLURAL_FORMS.items():
        bases = {_base(k) for k in DICTS[lang] if k.endswith(PLURAL_SUFFIXES)}
        assert bases, "ожидается хотя бы один ключ во множественном числе"
        for base in bases:
            have = {k[len(base) + 1 :] for k in DICTS[lang] if _base(k) == base and k != base}
            assert have == forms, (lang, base, have)


def test_placeholders_match_between_languages():
    for key, ru_text in DICTS["ru"].items():
        base = _base(key)
        en_key = key if key in DICTS["en"] else f"{base}.other"
        ru_ph = set(re.findall(r"\{(\w+)\}", ru_text))
        en_ph = set(re.findall(r"\{(\w+)\}", DICTS["en"][en_key]))
        assert ru_ph == en_ph, (key, ru_ph, en_ph)


def test_no_empty_translations():
    for lang, d in DICTS.items():
        for key, text in d.items():
            assert text.strip(), (lang, key)


# ---- использование ключей ---------------------------------------------------


def test_every_used_key_exists_in_both_languages():
    for key in _used_keys():
        for lang in ("ru", "en"):
            present = key in DICTS[lang] or f"{key}.other" in DICTS[lang]
            assert present, f"ключа {key!r} нет в словаре {lang}"


def test_no_unused_keys():
    used = _used_keys()
    unused = [
        key
        for key in DICTS["ru"]
        if _base(key) not in used and not key.startswith(DYNAMIC_PREFIXES)
    ]
    assert not unused, unused


def test_dynamic_keys_for_every_status_exist():
    for status in ("pending", "paid", "shipped", "completed", "cancelled"):
        for lang in ("ru", "en"):
            assert f"status.{status}" in DICTS[lang]
    for status in ("paid", "shipped", "completed", "cancelled"):
        for lang in ("ru", "en"):
            assert f"action.{status}" in DICTS[lang]


# ---- разметка -----------------------------------------------------------------


@pytest.mark.parametrize("page", ["admin.html", "dashboard.html"])
def test_static_markup_matches_russian_dictionary(page):
    c = _collect(page)
    assert c.text_keys, page
    for key, text in c.text_keys:
        assert text == DICTS["ru"][key], (page, key, text)
    for key, attr, value in c.attr_keys:
        assert value == DICTS["ru"][key], (page, key, attr, value)
    for key in c.title_keys:
        title = re.search(r"<title>(.*?)</title>", (ROOT / page).read_text(encoding="utf-8"))
        assert title and title.group(1) == DICTS["ru"][key], (page, key)


@pytest.mark.parametrize("page", ["admin.html", "dashboard.html"])
def test_pages_declare_russian_default_and_load_i18n_first(page):
    html = (ROOT / page).read_text(encoding="utf-8")
    assert '<html lang="ru">' in html
    assert html.index("js/i18n.js") < html.index("js/common.js")


def test_product_datalist_comes_from_catalogue(authed):
    client, _ = authed
    html = client.get("/dashboard").get_data(as_text=True)
    assert '<option value="Медовик, 1 кг">' in html
    assert "Landing kit" not in html


# ---- сообщения API --------------------------------------------------------------


def test_every_server_message_has_a_translation():
    app_src = (ROOT / "app.py").read_text(encoding="utf-8")
    messages = set(re.findall(r'errors\["\w+"\] = "([^"]+)"', app_src))
    messages |= set(re.findall(r'return None, "([^"]+)"', app_src))
    messages |= set(re.findall(r'_json_validation_errors\(\{"status": "([^"]+)"\}', app_src))
    messages.add("Unknown sort column")
    codes = set(re.findall(r'_raise\(\d+, "([a-z_]+)"', app_src))
    codes |= set(re.findall(r'"error": "([a-z_]+)"', app_src))
    codes |= set(re.findall(r'_json_error\("([a-z_]+)"', app_src))
    assert "Amount is required" in messages and "invalid_transition" in codes
    mapped = dict(re.findall(r'^\s+"([^"]+)": "(api\.[\w.]+)",$', I18N_JS, re.M))
    for item in messages | codes:
        assert item in mapped, f"нет перевода для сообщения сервера {item!r}"
    for message in messages:
        assert DICTS["en"][mapped[message]] == message, message
    for key in mapped.values():
        assert key in DICTS["ru"] and key in DICTS["en"], key


# ---- поведение в node (пропускается, если node нет) -------------------------------

NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const store = {};
const stubDoc = {
  documentElement: {}, body: null,
  getElementById: () => null, querySelectorAll: () => [],
  addEventListener: () => {},
};
const sandbox = {
  window: {}, document: stubDoc, Intl, console,
  localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
};
sandbox.window.window = sandbox.window;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync("js/i18n.js", "utf8"), sandbox);
vm.runInContext(fs.readFileSync("js/common.js", "utf8"), sandbox);
const I = sandbox.window.I18N;
const out = {};
out.ruPlural = [0, 1, 2, 5, 11, 21, 22, 25].map((n) => I.tn("top.orders", n));
out.ruDefault = I.getLang();
out.ruPager = I.t("pager.info", { from: 1, to: 10, total: 60 });
out.ruApi = I.apiMessage("Amount is required");
out.ruHttp = I.apiMessage("HTTP 500");
out.ruInvalid = I.apiMessage("Invalid value for status");
out.unknownKey = I.t("no.such.key");
out.rawMessage = I.apiMessage("Something else");
I.setLang("en");
out.enPlural = [1, 2].map((n) => I.tn("top.orders", n));
out.enApi = I.apiMessage("invalid_transition");
out.saved = store.lang;
I.setLang("xx");
out.afterBad = I.getLang();
const safe = sandbox.window.OrdersApp.safeNextPath;
out.next = ["/dashboard?days=7", "/orders", "//evil.example", "/\\evil.example", "https://evil.example", "javascript:alert(1)", "", null]
  .map((v) => safe(v));
console.log(JSON.stringify(out));
"""


def test_i18n_runtime_behaviour_in_node():
    if not shutil.which("node"):
        pytest.skip("node не установлен")
    proc = subprocess.run(
        ["node", "-e", NODE_HARNESS], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ruDefault"] == "ru"
    assert out["ruPlural"] == [
        "0 заказов", "1 заказ", "2 заказа", "5 заказов",
        "11 заказов", "21 заказ", "22 заказа", "25 заказов",
    ]
    assert out["ruPager"] == "1–10 из 60"
    assert out["ruApi"] == "Укажите сумму"
    assert out["ruHttp"] == "Ошибка сервера (HTTP 500)"
    assert out["ruInvalid"] == "Недопустимое значение: status"
    assert out["unknownKey"] == "no.such.key"
    assert out["rawMessage"] == "Something else"
    assert out["enPlural"] == ["1 order", "2 orders"]
    assert out["enApi"] == "This status change is not allowed"
    assert out["saved"] == "en"
    assert out["afterBad"] == "en"
    assert out["next"] == [
        "/dashboard?days=7", "/orders", "/dashboard", "/dashboard",
        "/dashboard", "/dashboard", "/dashboard", "/dashboard",
    ]
