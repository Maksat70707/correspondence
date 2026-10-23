# -*- coding: utf-8 -*-
"""Пометка «Предпросмотр» на страницах PDF.

Копия document_flow/models/doc_preview.py: модули друг от друга не
зависят, а общего места, кроме appstream_approval, у них нет. Поменяли
здесь — поменяйте и там.

Предпросмотр строится той же конвертацией, что и печатная версия, и
выглядит как готовый документ. Чтобы его не распечатали и не отдали
вместо подписанного, на каждой странице — бледная надпись по диагонали
и строка сверху. Шрифт — DejaVu, его регистрирует печатная версия
appstream_approval: кириллица и казахские буквы в нём есть.
"""

import io
import math

from PyPDF2 import PdfReader, PdfWriter, Transformation
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from odoo.addons.appstream_approval.models import esp_copy

MARK_COLOR = colors.Color(0.75, 0.1, 0.1)


def _overlay(width, height, mark, line):
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=(width, height))
    # по диагонали — на три четверти её длины
    diagonal = math.hypot(width, height)
    size = 0.75 * diagonal / max(pdfmetrics.stringWidth(mark, esp_copy.FONT_BOLD, 1), 1)
    page.saveState()
    page.setFillColor(MARK_COLOR, alpha=0.08)
    page.setFont(esp_copy.FONT_BOLD, size)
    page.translate(width / 2, height / 2)
    page.rotate(math.degrees(math.atan2(height, width)))
    page.drawCentredString(0, -size / 3, mark)
    page.restoreState()
    page.setFillColor(MARK_COLOR, alpha=0.9)
    page.setFont(esp_copy.FONT, 7)
    page.drawCentredString(width / 2, height - 5 * mm, line)
    page.save()
    return buffer.getvalue()


def stamp_preview(pdfs, mark, line):
    """Склеить PDF по порядку и пометить каждую страницу.

    Поворот страницы переносится в содержимое, как в печатной версии:
    надпись ложится так, как страницу видит читатель, и у альбомных тоже.
    """
    esp_copy._register_fonts()
    writer = PdfWriter()
    for pdf in pdfs:
        reader = PdfReader(io.BytesIO(pdf), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("The PDF is password-protected")
        for page in reader.pages:
            if page.get("/Rotate", 0) % 360:
                page.transfer_rotation_to_content()
            box = page.cropbox
            left, bottom = float(box.left), float(box.bottom)
            overlay = PdfReader(io.BytesIO(
                _overlay(float(box.width), float(box.height), mark, line)),
                strict=False).pages[0]
            if left or bottom:
                overlay.add_transformation(Transformation().translate(left, bottom))
            page.merge_page(overlay)
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
