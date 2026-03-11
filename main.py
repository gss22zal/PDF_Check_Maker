#!/usr/bin/env python3
"""
PDF Check Maker - CLI приложение для генерации PDF-чеков из данных CSV/JSON.
Поддерживает GTK3 Runtime (gtk3-runtime-*-ts-win64.exe) на Windows.
"""

import csv
import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

# --- Подавление GLib stderr через перенаправление fd 2 (Windows) ---
def _install_stderr_filter():
    """Перенаправляет stderr в pipe и фильтрует GLib-предупреждения."""
    try:
        r, w = os.pipe()
        saved = os.dup(2)
        os.dup2(w, 2)
        os.close(w)

        def _filter_reader():
            buf = b""
            skip = (b"GLib-", b"GObject-", b"(process:")
            with os.fdopen(r, "rb") as f:
                while True:
                    chunk = f.read(4096)
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not any(p in line for p in skip):
                            try:
                                os.write(saved, line + b"\n")
                            except OSError:
                                pass
                    if not chunk:
                        break

        t = threading.Thread(target=_filter_reader, daemon=True)
        t.start()
    except Exception:
        pass

if platform.system() == "Windows":
    _install_stderr_filter()

# --- Настройка GTK3 Runtime на Windows (до импорта WeasyPrint) ---
if platform.system() == "Windows":
    _gtk_bin_paths = [
        Path(os.environ.get("GTK3_RUNTIME", r"C:\Program Files\GTK3-Runtime Win64\bin")),
        Path(r"C:\Program Files\GTK3-Runtime Win64\bin"),
        Path(r"C:\Program Files (x86)\GTK3-Runtime Win64\bin"),
        Path(r"C:\msys64\mingw64\bin"),
    ]
    _dll_dirs = [
        p for p in _gtk_bin_paths
        if p.exists() and (
            (p / "libgobject-2.0-0.dll").exists() or
            (p / "pango-1.0-0.dll").exists()
        )
    ]
    if _dll_dirs:
        _dll_path = str(_dll_dirs[0])
        os.environ.setdefault("WEASYPRINT_DLL_DIRECTORIES", _dll_path)
        if sys.version_info >= (3, 8):
            os.add_dll_directory(_dll_path)

# Опциональные зависимости - pandas для CSV
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration

# Базовые пути
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"
FONTS_DIR = BASE_DIR / "fonts"


def ensure_directories():
    """Создаёт необходимые директории, если их нет."""
    DATA_DIR.mkdir(exist_ok=True)
    TEMPLATES_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)


def get_data_files():
    """Возвращает список CSV и JSON файлов из /data."""
    files = []
    if not DATA_DIR.exists():
        return files
    for ext in ("*.csv", "*.json"):
        files.extend(DATA_DIR.glob(ext))
    return sorted(files, key=lambda p: p.name.lower())


def get_template_files():
    """Возвращает список HTML-шаблонов из /templates."""
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(TEMPLATES_DIR.glob("*.html"), key=lambda p: p.name.lower())


def load_csv_data(filepath):
    """Загружает CSV через pandas или csv."""
    if HAS_PANDAS:
        df = pd.read_csv(filepath, encoding="utf-8")
        return df.to_dict(orient="records")
    with open(filepath, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_json_data(filepath):
    """Загружает JSON."""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Если один объект — оборачиваем в список
        for key in ("invoices", "data", "items", "records"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return [data]
    return []


def load_data(filepath):
    """Загружает данные из CSV или JSON."""
    ext = filepath.suffix.lower()
    if ext == ".csv":
        return load_csv_data(filepath)
    if ext == ".json":
        return load_json_data(filepath)
    raise ValueError(f"Неподдерживаемый формат: {ext}")


def get_invoice_ids(records):
    """Извлекает список invoice_id из записей."""
    ids = []
    seen = set()
    for r in records:
        uid = r.get("invoice_id") or r.get("invoiceId") or r.get("id") or r.get("invoice")
        if uid is not None and str(uid) not in seen:
            ids.append((str(uid), r))
            seen.add(str(uid))
    return ids


def find_record_by_invoice_id(records, invoice_id):
    """Находит запись по invoice_id."""
    for r in records:
        uid = r.get("invoice_id") or r.get("invoiceId") or r.get("id") or r.get("invoice")
        if str(uid) == str(invoice_id):
            return r
    return None


def get_cyrillic_css():
    """CSS для поддержки кириллицы (DejaVu Sans / системные шрифты)."""
    fonts_css = []
    # Пробуем подключить DejaVu Sans из ./fonts
    font_paths = [
        FONTS_DIR / "DejaVuSans.ttf",
        FONTS_DIR / "DejaVuSans-Bold.ttf",
    ]
    for fp in font_paths:
        if fp.exists():
            uri = Path(fp).resolve().as_uri()
            fonts_css.append(f"""
@font-face {{
    font-family: 'DejaVu Sans';
    src: url('{uri}');
    font-weight: {'bold' if 'Bold' in fp.name else 'normal'};
}}
""")
    base = """
* {
    font-family: 'DejaVu Sans', 'DejaVu Sans Condensed', Arial, Helvetica, sans-serif !important;
}
"""
    return "\n".join(fonts_css) + base if fonts_css else base


def _build_items_rows(record):
    """Строит строки таблицы для items. Поддерживает список items или одиночные item/amount."""
    items = record.get("items")
    if isinstance(items, list) and items:
        rows = []
        for i, row in enumerate(items, 1):
            name = row.get("name", row.get("item", row.get("product", "")))
            qty = row.get("qty", row.get("quantity", 1))
            price = row.get("price", row.get("amount", ""))
            s = row.get("sum", row.get("total", row.get("amount", "")))
            rows.append(f'<tr><td class="num">{i}</td><td>{name}</td><td class="qty">{qty}</td><td class="price">{price}</td><td class="sum">{s}</td></tr>')
        return "\n      ".join(rows)
    # Одиночная позиция (старый формат)
    item = record.get("item", record.get("product", ""))
    amount = record.get("amount", record.get("total", ""))
    total = record.get("total", amount)
    return f'<tr><td class="num">1</td><td>{item}</td><td class="qty">1</td><td class="price">{amount}</td><td class="sum">{total}</td></tr>'


def render_html(template_path, record):
    """Подставляет данные в HTML-шаблон."""
    with open(template_path, encoding="utf-8") as f:
        html = f.read()
    rec = dict(record)
    rec["items_rows"] = _build_items_rows(record)
    if "total" not in rec:
        rec["total"] = rec.get("amount", rec.get("sum", ""))
    for key, value in rec.items():
        if isinstance(value, (list, dict)):
            continue
        placeholder = "{{" + str(key) + "}}"
        html = html.replace(placeholder, str(value) if value is not None else "")
    return html


def generate_pdf(html_content, output_path):
    """Генерирует PDF через WeasyPrint с поддержкой кириллицы."""
    font_config = FontConfiguration()
    cyrillic_css = CSS(string=get_cyrillic_css(), font_config=font_config)
    html_obj = HTML(string=html_content, base_url=str(TEMPLATES_DIR))
    html_obj.write_pdf(
        str(output_path),
        stylesheets=[cyrillic_css],
        font_config=font_config,
    )


def open_pdf(filepath):
    """Открывает PDF в системной программе."""
    path = Path(filepath).resolve()
    system = platform.system()
    if system == "Windows":
        os.startfile(str(path))
    elif system == "Darwin":
        subprocess.run(["open", str(path)], check=True)
    else:
        subprocess.run(["xdg-open", str(path)], check=True)


def select_from_menu(items, prompt, item_label="файл"):
    """Меню выбора с нумерацией. items: list of (name, value)."""
    if not items:
        return None
    print(f"\n  {prompt}")
    print("  " + "─" * 40)
    for i, (name, _) in enumerate(items, 1):
        print(f"  {i}. {name}")
    print("  " + "─" * 40)
    while True:
        try:
            s = input(f"  Выберите {item_label} (1–{len(items)}): ").strip()
            idx = int(s)
            if 1 <= idx <= len(items):
                return items[idx - 1][1]
        except (ValueError, KeyboardInterrupt):
            pass
        print("  Неверный ввод. Попробуйте снова.")


def main():
    ensure_directories()
    data_files = get_data_files()
    template_files = get_template_files()

    print("\n" + "═" * 50)
    print("  PDF Check Maker")
    print("═" * 50)

    # Список файлов данных
    print("\n  Доступные файлы с данными:")
    print("  " + "─" * 40)
    if not data_files:
        print("  (нет файлов — добавьте CSV или JSON в /data)")
    else:
        for i, f in enumerate(data_files, 1):
            print(f"  {i}. {f.name}")

    # Список шаблонов
    print("\n  Доступные HTML-шаблоны:")
    print("  " + "─" * 40)
    if not template_files:
        print("  (нет шаблонов — добавьте .html в /templates)")
    else:
        for i, f in enumerate(template_files, 1):
            print(f"  {i}. {f.name}")

    if not data_files or not template_files:
        print("\n  Добавьте данные и шаблоны, затем запустите снова.")
        return

    # Выбор файла данных
    data_items = [(f.name, f) for f in data_files]
    selected_data_file = select_from_menu(
        data_items, "Выберите файл с данными:", "файл данных"
    )
    if not selected_data_file:
        return

    # Выбор шаблона
    template_items = [(f.name, f) for f in template_files]
    selected_template = select_from_menu(
        template_items, "Выберите HTML-шаблон:", "шаблон"
    )
    if not selected_template:
        return

    # Загрузка данных
    try:
        records = load_data(selected_data_file)
    except Exception as e:
        print(f"\n  Ошибка загрузки данных: {e}")
        return

    if not records:
        print("\n  В файле нет записей.")
        return

    # Список чеков по invoice_id
    invoice_list = get_invoice_ids(records)
    if not invoice_list:
        print("\n  Не найдено полей invoice_id / id. Используется первая запись.")
        invoice_list = [(str(i), r) for i, r in enumerate(records, 1)]

    invoice_items = [(f"Invoice #{vid}", vid) for vid, _ in invoice_list]
    selected_id = select_from_menu(
        invoice_items, "Выберите чек (invoice id):", "чек"
    )
    if selected_id is None:
        return

    record = next(
        (r for vid, r in invoice_list if str(vid) == str(selected_id)),
        find_record_by_invoice_id(records, selected_id),
    )

    # Генерация PDF
    safe_id = str(selected_id).replace("/", "-").replace("\\", "-")
    output_path = OUTPUT_DIR / f"check_{safe_id}.pdf"

    try:
        html_content = render_html(selected_template, record)
        generate_pdf(html_content, output_path)
        print(f"\n  ✓ PDF сохранён: {output_path}")
        open_pdf(output_path)
        print("  ✓ PDF открыт в системной программе.")
    except Exception as e:
        print(f"\n  Ошибка генерации PDF: {e}")
        raise


if __name__ == "__main__":
    main()
