"""Generate the project technical specification as an editable Word document."""

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


OUTPUT = Path(__file__).resolve().with_name("ТЗ_AI_система_мониторинга_конкурентов.docx")

AUDIENCE = (
    ("Ритейлеры", "Ручной обход магазинов отнимает время. Резкие изменения цен легко пропустить.",
     "Регулярно собирает цены и акции. Сообщает о существенных снижениях в Telegram."),
    ("Категорийные менеджеры", "Трудно восстановить динамику цен по товарам и оценить глубину скидок.",
     "Показывает историю товара, последнюю и предыдущую цены, абсолютную и процентную дельты."),
    ("Директора по маркетингу", "Акции конкурентов разбросаны по сайтам и каналам. На сводку уходит время.",
     "Объединяет акции в ленте и готовит отчёт с трендами, приоритетами и целевыми метриками."),
)

REQUIREMENTS = (
    ("Сбор данных", "Извлекать поля товаров и цены из JSON-LD, Microdata и HTML-карточек веб-каталогов. "
     "В публичных каналах Telegram (t.me/s/) извлекать дату, текст и тип публикации."),
    ("Хранение и расчёт цен", "Сохранять срезы в гипертаблице TimescaleDB. Через LATERAL JOIN выбирать "
     "последнюю и предыдущую сопоставимые цены по товару и рассчитывать дельты в сумме и процентах."),
    ("Фоновые задачи", "Передавать задачи Celery через Redis. Celery Beat запускает сбор каждые 30 минут. "
     "PostgreSQL Advisory Locks защищают сидирование и полный цикл от параллельного запуска."),
    ("AI-анализ", "Формировать отчёт раз в сутки по UTC и по ручному запуску. Проверять ответ OpenAI "
     "по JSON Schema с полями summary, trends и recommendations. Для рекомендации сохранять действие, "
     "причину, приоритет и целевую метрику. Без ключа формировать детерминированный отчёт по наблюдениям."),
    ("Панель и уведомления", "Показывать в Streamlit сводку, график цен, таблицу дельт, ленту акций и AI-отчёт. "
     "Поддерживать ручной запуск и просмотр статуса. Отправлять Telegram-алерт после сохранения среза, "
     "если цена упала более чем на 10% относительно предыдущей реальной цены."),
)

RISKS = (
    ("Антифрод и блокировки", "Источник возвращает ошибку или ограничивает частоту запросов.",
     "Снижать частоту запросов, фиксировать ошибки и явно помечать демо-наблюдения."),
    ("Лимиты LLM API", "Квоты и ошибки API задерживают формирование отчёта.",
     "Ограничивать объём входных данных и частоту вызовов. При работе без ключа использовать локальный отчёт."),
    ("robots.txt и персональные данные", "Для отдельных разделов или полей могут действовать ограничения на сбор.",
     "До подключения проверять robots.txt и условия источника. Собирать необходимые данные из разрешённых "
     "публичных разделов и исключать персональные сведения из отчётов."),
    ("Смена вёрстки сайтов", "Поля могут исчезнуть или начать извлекаться с ошибками.",
     "Проверять полноту обязательных полей, хранить HTML-примеры для тестов и обновлять адаптеры источников."),
)


def add_table(document, headers, rows, widths):
    table = document.add_table(rows=0, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for column, width in zip(table.columns, widths):
        column.width = Cm(width)

    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement(f"w:{edge}")
        for name, value in (("val", "single"), ("sz", "6"), ("color", "D9D9D9")):
            border.set(qn(f"w:{name}"), value)
        borders.append(border)
    table._tbl.tblPr.insert_element_before(borders, "w:tblLayout", "w:tblLook")

    padding = OxmlElement("w:tblCellMar")
    for edge, amount in (("top", "105"), ("bottom", "105"), ("left", "155"), ("right", "155")):
        margin = OxmlElement(f"w:{edge}")
        margin.set(qn("w:w"), amount)
        margin.set(qn("w:type"), "dxa")
        padding.append(margin)
    table._tbl.tblPr.insert_element_before(padding, "w:tblLook")

    for index, values in enumerate((headers, *rows)):
        row = table.add_row()
        row_properties = row._tr.get_or_add_trPr()
        row_properties.append(OxmlElement("w:cantSplit"))
        if index == 0:
            row_properties.append(OxmlElement("w:tblHeader"))

        for column_index, (cell, value, width) in enumerate(zip(row.cells, values, widths)):
            cell.width = Cm(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            fill = "DCE8F3" if index == 0 else ("F6F8FA" if index % 2 == 0 else "FFFFFF")
            shading = OxmlElement("w:shd")
            shading.set(qn("w:fill"), fill)
            cell._tc.get_or_add_tcPr().insert_element_before(shading, "w:vAlign")
            paragraph = cell.paragraphs[0]
            paragraph.style = document.styles["Текст таблицы"]
            paragraph.paragraph_format.keep_with_next = index == 0
            paragraph.add_run(value).bold = index == 0 or column_index == 0


def build_document():
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.2)
    section.top_margin = section.bottom_margin = Cm(2)
    document.core_properties.title = "AI-система мониторинга конкурентов"
    document.core_properties.language = "ru-RU"

    for name, size in (("Normal", 11), ("Title", 27), ("Heading 1", 16), ("Heading 2", 11)):
        style = document.styles[name]
        style.font.name = "Arial"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.line_spacing = 1.12

    title = document.styles["Title"]
    title.font.bold = True
    title.paragraph_format.keep_with_next = True
    title_properties = title.element.get_or_add_pPr()
    for border in title_properties.findall(qn("w:pBdr")):
        title_properties.remove(border)

    for name, before in (("Heading 1", 15), ("Heading 2", 5)):
        style = document.styles[name]
        style.font.bold = True
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(4)
        style.paragraph_format.keep_with_next = True

    cell_style = document.styles.add_style("Текст таблицы", WD_STYLE_TYPE.PARAGRAPH)
    cell_style.base_style = document.styles["Normal"]
    cell_style.font.size = Pt(10.5)
    cell_style.paragraph_format.space_after = Pt(0)

    document.add_paragraph("Техническое задание").runs[0].bold = True
    document.add_paragraph("AI-система мониторинга конкурентов", style="Title")
    document.add_paragraph(
        "Система собирает цены и акции конкурентов, отслеживает изменения и помогает "
        "командам розницы принимать решения по ассортименту, ценам и продвижению."
    )

    document.add_heading("Целевая аудитория и её задачи", level=1)
    add_table(document, ("Сегмент ЦА", "Боли", "Как решает система"), AUDIENCE, (3.55, 6.05, 7.0))

    document.add_heading("Функциональные требования", level=1)
    for heading, description in REQUIREMENTS:
        document.add_heading(heading, level=2)
        paragraph = document.add_paragraph(description)
        paragraph.paragraph_format.keep_together = True

    document.add_heading("Ограничения и риски", level=1)
    add_table(document, ("Фактор риска", "Влияние", "Меры минимизации"), RISKS, (3.55, 5.05, 8.0))

    document.add_heading("Стек технологий", level=1)
    for technology, purpose in (
        ("Python 3.12", "скрейперы и прикладная логика"),
        ("PostgreSQL + TimescaleDB", "данные конкурентов и временные ряды цен"),
        ("Redis и Celery с Beat", "брокер сообщений, исполнение задач и расписание"),
        ("Streamlit и Plotly", "интерактивная панель и графики"),
        ("OpenAI API", "анализ трендов и структурированные рекомендации"),
    ):
        paragraph = document.add_paragraph()
        paragraph.add_run(technology).bold = True
        paragraph.add_run(f" — {purpose}.")

    return document


if __name__ == "__main__":
    build_document().save(OUTPUT)
    print(OUTPUT)
