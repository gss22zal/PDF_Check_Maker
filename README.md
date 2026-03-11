# PDF Check Maker

CLI для генерации PDF-чеков из CSV/JSON данных.

## Установка

```bash
pip install -r requirements.txt
```

## Шрифты для кириллицы

Для корректной поддержки кириллицы поместите `DejaVuSans.ttf` в папку `fonts/`.  
Скачать: https://dejavu-fonts.github.io/Download.html

Если шрифт не указан, используются системные (Arial, Helvetica).

## Запуск

```bash
python main.py
```

Структура: `data/` — CSV/JSON, `templates/` — HTML-шаблоны, `output/` — готовые PDF.

