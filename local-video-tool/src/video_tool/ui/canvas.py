from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from video_tool.crop.geometry import geometry
from video_tool.crop.keyframes import Project
from video_tool.crop.preview import PreviewFrame


def _fit(outer: QRectF, width: float, height: float) -> QRectF:
    scale = min(outer.width() / width, outer.height() / height)
    result = QRectF(0, 0, width * scale, height * scale)
    result.moveCenter(outer.center())
    return result


class FramingCanvas(QWidget):
    dragged = Signal(float, float)
    drag_started = Signal()

    def __init__(self, source_view: bool):
        super().__init__()
        self.source_view = source_view
        self.setMinimumSize(250, 200)
        self.pixmap = QPixmap()
        self.project: Project | None = None
        self.source_size = (0, 0)
        self.center = (0.5, 0.5)
        self.boundary = True
        self.center_line = True
        self.thirds = False
        self.safe = False
        self._drag: tuple[QPointF, float, float] | None = None
        self._blurred = QPixmap()
        self._blur_key: tuple | None = None
        self.setAccessibleName("Source framing" if source_view else "Output framing")

    def set_frame(self, frame: PreviewFrame | None):
        if frame is None:
            self.pixmap = QPixmap()
        else:
            image = QImage(frame.rgb, frame.width, frame.height, frame.width * 3, QImage.Format.Format_RGB888)
            self.pixmap = QPixmap.fromImage(image)
        self._blurred = QPixmap()
        self._blur_key = None
        self.update()

    def _crop(self) -> QRectF:
        assert self.project is not None
        width, height = self.source_size
        crop = geometry(width, height, self.project.output_width, self.project.output_height, *self.center)
        return QRectF(crop.x, crop.y, crop.width, crop.height)

    def _source_rect(self) -> QRectF:
        return _fit(QRectF(self.rect()).adjusted(5, 5, -5, -5), *self.source_size)

    def _source_overlay(self) -> QRectF:
        source = self._source_rect()
        crop = self._crop()
        width, height = self.source_size
        sx, sy = source.width() / width, source.height() / height
        return QRectF(source.x() + crop.x() * sx, source.y() + crop.y() * sy, crop.width() * sx, crop.height() * sy)

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#191c20"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.pixmap.isNull() or self.project is None:
            painter.setPen(QColor("#d0d0d0"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Source preview" if self.source_view else "Output preview")
        elif self.source_view:
            self._paint_source(painter)
        else:
            self._paint_output(painter, self.project)
        painter.end()

    def _paint_source(self, painter: QPainter):
        assert self.project is not None
        source = self._source_rect()
        painter.drawPixmap(source, self.pixmap, QRectF(self.pixmap.rect()))
        if self.project.mode == "blurred_background":
            return
        crop = self._source_overlay()
        if self.boundary:
            outside = QPainterPath()
            outside.setFillRule(Qt.FillRule.OddEvenFill)
            outside.addRect(source)
            outside.addRect(crop)
            painter.fillPath(outside, QColor(0, 0, 0, 125))
            painter.setPen(QPen(QColor("#53d4ff"), 2))
            painter.drawRect(crop)
            handle = QRectF(0, 0, 21, 10)
            handle.moveCenter(QPointF(crop.center().x(), crop.top() + 9))
            painter.fillRect(handle, QColor("#53d4ff"))
            center = crop.center()
            painter.drawEllipse(center, 4, 4)
            painter.save()
            painter.setClipRect(crop)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(crop.adjusted(5, 0, -4, -4), Qt.AlignmentFlag.AlignBottom,
                             f"X {self.center[0]:.3f}   Y {self.center[1]:.3f}")
            painter.restore()
        self._guides(painter, crop)

    def _guides(self, painter: QPainter, bounds: QRectF):
        painter.setPen(QPen(QColor(255, 255, 255, 170), 1, Qt.PenStyle.DashLine))
        if self.center_line:
            x = bounds.center().x()
            painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.bottom()))
        if self.thirds:
            for part in (1 / 3, 2 / 3):
                x = bounds.left() + bounds.width() * part
                y = bounds.top() + bounds.height() * part
                painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.bottom()))
                painter.drawLine(QPointF(bounds.left(), y), QPointF(bounds.right(), y))
        if self.safe:
            painter.setPen(QPen(QColor("#ffe093"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(bounds.adjusted(bounds.width() * 0.1, bounds.height() * 0.7,
                                             -bounds.width() * 0.1, -bounds.height() * 0.1))

    def _paint_output(self, painter: QPainter, project: Project):
        width, height = project.output_width, project.output_height
        canvas = _fit(QRectF(self.rect()).adjusted(5, 5, -5, -5), width, height)
        full = QRectF(self.pixmap.rect())
        painter.fillRect(canvas, Qt.GlobalColor.black)
        if project.mode != "dynamic_crop":
            background = _fit(full, width, height)
            radius = project.blur
            key = (width, height, radius)
            if self._blur_key != key:
                # Reduced-resolution resampling approximates the export blur for framing.
                approx_width = max(2, min(round(background.width()), round(1080 / max(radius, 1))))
                approx_height = max(2, round(approx_width * background.height() / background.width()))
                self._blurred = self.pixmap.copy(background.toAlignedRect()).scaled(
                    approx_width, approx_height, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ) if radius else self.pixmap.copy(background.toAlignedRect())
                self._blur_key = key
            painter.drawPixmap(canvas, self._blurred, QRectF(self._blurred.rect()))
        if project.mode == "blurred_background":
            foreground = full
            fg_width, fg_height = self.source_size
        else:
            crop = self._crop()
            sx, sy = full.width() / self.source_size[0], full.height() / self.source_size[1]
            foreground = QRectF(crop.x() * sx, crop.y() * sy, crop.width() * sx, crop.height() * sy)
            fg_width, fg_height = crop.width(), crop.height()
        if project.mode == "dynamic_crop":
            target = canvas
        else:
            inset = project.inset
            if inset is None:
                inset = 1.0 if project.mode == "blurred_background" else 0.85
            margin_x, margin_y = canvas.width() * (1 - inset) / 2, canvas.height() * (1 - inset) / 2
            target = _fit(canvas.adjusted(margin_x, margin_y, -margin_x, -margin_y), fg_width, fg_height)
        painter.drawPixmap(target, self.pixmap, foreground)
        if project.mode != "dynamic_crop" and project.dark_overlay:
            painter.fillRect(canvas.adjusted(0, canvas.height() * 0.75, 0, 0), QColor(0, 0, 0, round(255 * project.dark_overlay)))
        self._guides(painter, canvas)

    def mousePressEvent(self, event: QMouseEvent):
        if (event.button() == Qt.MouseButton.LeftButton and self.source_view and self.boundary and
                self.project is not None and self.project.mode != "blurred_background" and not self.pixmap.isNull()):
            crop = self._source_overlay()
            if crop.contains(event.position()):
                self.drag_started.emit()
                source = self._source_rect()
                self._drag = (event.position(), (crop.center().x() - source.left()) / source.width(),
                              (crop.center().y() - source.top()) / source.height())
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag is not None:
            start, x, y = self._drag
            source = self._source_rect()
            crop = self._crop()
            delta = event.position() - start
            x = x + delta.x() / source.width() if crop.width() < self.source_size[0] - 1 else self.center[0]
            y = y + delta.y() / source.height() if crop.height() < self.source_size[1] - 1 else self.center[1]
            self.dragged.emit(max(0.0, min(1.0, x)), max(0.0, min(1.0, y)))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._drag is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)
