"""Native crop editor with background proxy preparation, decoding, and export."""

from bisect import bisect_left, bisect_right
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QColor, QKeySequence, QPaintEvent, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSlider, QSpinBox,
    QSplitter, QStyle, QStyleOptionSlider, QVBoxLayout, QWidget,
)

from video_tool.crop.interpolation import position
from video_tool.crop.keyframes import METHODS, MODES, Keyframe, Project
from video_tool.crop.preview import PreviewCancelled, PreviewFrame, Proxy, build_proxy
from video_tool.media.probe import MediaInfo, probe
from video_tool.ui.canvas import FramingCanvas


_ASPECTS = (("9:16", 1080, 1920), ("9:16 · 720p", 720, 1280), ("1:1", 1080, 1080), ("16:9", 1920, 1080))
_MODE_LABELS = ("Dynamic crop", "Blurred background", "Dynamic crop + blurred background")


def _stamp(seconds: float) -> str:
    total = max(0, round(seconds * 1000))
    minutes, millis = divmod(total, 60_000)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{millis // 1000:02}.{millis % 1000:03}"


class _Timeline(QSlider):
    marks: tuple[int, ...] = ()

    def paintEvent(self, event: QPaintEvent):
        super().paintEvent(event)
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        cc = QStyle.ComplexControl.CC_Slider
        groove = style.subControlRect(cc, option, QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(cc, option, QStyle.SubControl.SC_SliderHandle, self)
        span = max(0, groove.width() - handle.width())
        painter = QPainter(self)
        painter.setPen(QColor("#1c9dcd"))
        for value in self.marks:
            x = groove.left() + handle.width() // 2 + style.sliderPositionFromValue(
                self.minimum(), self.maximum(), value, span, option.upsideDown,
            )
            painter.drawLine(x, max(0, groove.top() - 5), x, max(1, groove.top()))
        painter.end()


class _Signals(QObject):
    finished = Signal(object)
    progress = Signal(float)


class _Job(QRunnable):
    def __init__(self, kind: str, generation: int, operation: Callable, cancel: Event):
        super().__init__()
        self.kind, self.generation, self.operation, self.cancel = kind, generation, operation, cancel
        self.signals = _Signals()

    def run(self):
        value = None
        error = None
        try:
            if self.cancel.is_set():
                raise PreviewCancelled
            value = self.operation(self.signals.progress.emit, self.cancel.is_set)
        except Exception as exception:
            error = exception
        self.signals.finished.emit((self.kind, self.generation, value, error))


class MainWindow(QMainWindow):
    def __init__(self, source: Path | None = None):
        super().__init__()
        self.setWindowTitle("Local Video Tool — Crop editor")
        self.resize(1180, 820)
        self.project: Project | None = None
        self.info: MediaInfo | None = None
        self.proxy: Proxy | None = None
        self.project_path: Path | None = None
        self._proxy_paths: set[Path] = set()
        self._generation = 0
        self._preview_cancel = Event()
        self._export_cancel: Event | None = None
        self._jobs: dict[tuple[str, int], _Job] = {}
        self._preview_pool = QThreadPool(self)
        self._preview_pool.setMaxThreadCount(1)
        self._export_pool = QThreadPool(self)
        self._export_pool.setMaxThreadCount(1)
        self._closing = False
        self._time = 0.0
        self._want_frame = 0
        self._shown_frame: int | None = None
        self._draft: tuple[float, float] | None = None
        self._key_selected = False
        self._clock = 0.0
        self._play_from = 0.0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._tick)
        self._build()
        self._enable()
        if source is not None:
            self.open_source(source)

    def _build(self):
        files = self.menuBar().addMenu("&File")
        self.open_video_action = self._action(files, "Open &video…", self._choose_video, "Ctrl+O")
        self.open_project_action = self._action(files, "Open &project…", self._choose_project, "Ctrl+Shift+O")
        self.save_action = self._action(files, "&Save project", self._save, "Ctrl+S")
        self.save_as_action = self._action(files, "Save project &as…", self._save_as, "Ctrl+Shift+S")
        self.export_action = self._action(files, "&Export crop…", self._choose_export, "Ctrl+E")

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        self.source_label = QLabel("Open a video or crop project to begin.")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        self.framing_label = QLabel("Framing preview only — low-resolution; not color-accurate for HDR.")
        self.framing_label.setStyleSheet("font-weight: bold")
        self.framing_label.setWordWrap(True)
        layout.addWidget(self.framing_label)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.source_canvas = FramingCanvas(True)
        self.output_canvas = FramingCanvas(False)
        for label, canvas in (("Source and crop overlay", self.source_canvas), ("Output framing", self.output_canvas)):
            pane = QWidget()
            column = QVBoxLayout(pane)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(QLabel(label))
            column.addWidget(canvas, 1)
            split.addWidget(pane)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        self.source_canvas.drag_started.connect(self._pause)
        self.source_canvas.dragged.connect(self._dragged)

        self.controls = QWidget()
        controls = QVBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.controls)
        self.timeline = _Timeline(Qt.Orientation.Horizontal)
        self.timeline.setMinimumHeight(23)
        self.timeline.setAccessibleName("Timeline playhead")
        self.timeline.sliderPressed.connect(self._pause)
        self.timeline.valueChanged.connect(self._scrub)
        controls.addWidget(self.timeline)
        transport = QHBoxLayout()
        controls.addLayout(transport)
        self.play = self._button(transport, "Play", self._toggle_play)
        self._button(transport, "Previous frame", lambda: self._step(-1))
        self._button(transport, "Next frame", lambda: self._step(1))
        self.time_label = QLabel()
        transport.addWidget(self.time_label, 1)
        self._button(transport, "Previous keyframe", lambda: self._navigate_key(-1))
        self._button(transport, "Next keyframe", lambda: self._navigate_key(1))

        settings = QHBoxLayout()
        controls.addLayout(settings)
        shape = QFormLayout()
        framing = QFormLayout()
        keys = QVBoxLayout()
        for row in (shape, framing, keys):
            settings.addLayout(row, 1)

        self.aspect = QComboBox()
        self.aspect.addItems([entry[0] for entry in _ASPECTS] + ["Custom"])
        self.aspect.currentIndexChanged.connect(self._change_aspect)
        shape.addRow("Aspect / preset", self.aspect)
        dimensions = QHBoxLayout()
        self.width = self._integer(2, 2_147_483_646, 2)
        self.height = self._integer(2, 2_147_483_646, 2)
        self.width.setAccessibleName("Output width")
        self.height.setAccessibleName("Output height")
        for field in (self.width, self.height):
            field.editingFinished.connect(self._change_dimensions)
            dimensions.addWidget(field)
        shape.addRow("Output pixels", dimensions)
        self.mode = QComboBox()
        self.mode.addItems(_MODE_LABELS)
        self.mode.currentIndexChanged.connect(self._change_settings)
        shape.addRow("Mode", self.mode)
        self.blur = self._integer(0, 2_147_483_647, 1)
        self.blur.valueChanged.connect(self._change_settings)
        shape.addRow("Blur radius", self.blur)
        self.dark = self._decimal(0.01)
        self.dark.valueChanged.connect(self._change_overlay)
        shape.addRow("Caption shading", self.dark)

        self.x = self._decimal(0.01)
        self.y = self._decimal(0.01)
        self.x.valueChanged.connect(self._edit_position)
        self.y.valueChanged.connect(self._edit_position)
        framing.addRow("Center X", self.x)
        framing.addRow("Center Y", self.y)
        self.interpolation = QComboBox()
        self.interpolation.addItems([method.title() for method in METHODS])
        self.interpolation.currentIndexChanged.connect(self._change_settings)
        framing.addRow("Interpolation", self.interpolation)
        guides = QVBoxLayout()
        for label, attr, checked in (("Crop boundary", "boundary", True), ("Centre line", "center_line", True),
                                     ("Thirds grid", "thirds", False), ("Caption safe zones", "safe", False)):
            check = QCheckBox(label)
            check.setChecked(checked)
            check.toggled.connect(lambda value, name=attr: self._guide(name, value))
            guides.addWidget(check)
        framing.addRow("Guides", guides)

        self.key_list = QListWidget()
        self.key_list.setMaximumHeight(115)
        self.key_list.setAccessibleName("Crop keyframes")
        self.key_list.itemSelectionChanged.connect(self._select_key)
        keys.addWidget(self.key_list, 1)
        key_buttons = QHBoxLayout()
        keys.addLayout(key_buttons)
        self.add_key = self._button(key_buttons, "Add keyframe", lambda: self._write_key(False))
        self.update_key = self._button(key_buttons, "Update keyframe", lambda: self._write_key(True))
        self.delete_key = self._button(key_buttons, "Delete keyframe", self._delete_key)

        export = QHBoxLayout()
        layout.addLayout(export)
        self.preset = QComboBox()
        self.preset.addItems(["Automatic preset", "hdr10_hevc", "youtube_hdr10", "instagram_hdr", "sdr_h264"])
        self.preset.setAccessibleName("Export preset")
        export.addWidget(self.preset)
        self.export = self._button(export, "Export crop…", self._choose_export)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setAccessibleName("Export progress")
        export.addWidget(self.progress, 1)
        self.cancel_export = self._button(export, "Cancel export", self._cancel_encoding)
        self.statusBar().showMessage("Ready")

    def _action(self, menu, name, callback, shortcut):
        action = QAction(name, self)
        action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    @staticmethod
    def _button(layout, name, callback):
        button = QPushButton(name)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    @staticmethod
    def _integer(minimum: int, maximum: int, step: int) -> QSpinBox:
        field = QSpinBox()
        field.setRange(minimum, maximum)
        field.setSingleStep(step)
        field.setKeyboardTracking(False)
        return field

    @staticmethod
    def _decimal(step: float) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(0, 1)
        field.setDecimals(4)
        field.setSingleStep(step)
        field.setKeyboardTracking(False)
        return field

    def _enable(self):
        ready = self.project is not None and self.proxy is not None
        exporting = self._export_cancel is not None
        self.controls.setEnabled(ready)
        for action in (self.save_action, self.save_as_action):
            action.setEnabled(ready)
        self.export_action.setEnabled(ready and not exporting)
        self.export.setEnabled(ready and not exporting)
        self.preset.setEnabled(ready and not exporting)
        self.cancel_export.setEnabled(exporting and self._export_cancel is not None and not self._export_cancel.is_set())
        self._key_buttons()

    def _choose_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open source video", "", "Video files (*.mp4 *.mkv *.mov *.m4v *.webm *.avi *.mts *.m2ts *.ts);;All files (*)")
        if path:
            self.open_source(Path(path))

    def _choose_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open crop project", "", "Crop project (*.json)")
        if path:
            self.open_project(Path(path))

    def open_project(self, path: Path):
        try:
            project = Project.load(path)
        except (OSError, ValueError) as error:
            self._error("Could not open project", error)
            return
        self.open_source(Path(project.source), project, path)

    def open_source(self, path: Path, project: Project | None = None, project_path: Path | None = None):
        self._pause()
        self._preview_cancel.set()
        self._preview_cancel = Event()
        self._generation += 1
        self.info = self.proxy = self.project = None
        self.project_path = project_path
        self._time, self._want_frame, self._shown_frame = 0.0, 0, None
        self._draft, self._key_selected = None, False
        for canvas in (self.source_canvas, self.output_canvas):
            canvas.project = None
            canvas.set_frame(None)
        self._sync_keys()
        self._enable()
        self.source_label.setText(f"Preparing framing proxy for {path.name}…")
        self.statusBar().showMessage("Probing and generating low-resolution proxy…")
        previews = Path(__file__).resolve().parents[3] / "previews"
        target = previews / f"proxy-{uuid4().hex}.mp4"
        previous_paths = tuple(self._proxy_paths)
        self._proxy_paths.add(target)

        def prepare(_progress, cancel):
            for previous in previous_paths:
                previous.unlink(missing_ok=True)
            previews.mkdir(parents=True, exist_ok=True)
            info = probe(path)
            if min(info.width, info.height) < 2:
                raise ValueError("Video dimensions are too small for a crop")
            active = project if project is not None else Project(source=info.path, source_metadata=asdict(info))
            if not active.source_metadata:
                active.source_metadata = asdict(info)
            active.validate(info.duration)
            proxy = build_proxy(Path(info.path), target, cancel)
            return info, active, proxy

        self._start_job("prepare", prepare, self._preview_cancel, self._preview_pool)

    def _start_job(self, kind: str, operation: Callable, cancel: Event, pool: QThreadPool):
        job = _Job(kind, self._generation, operation, cancel)
        self._jobs[kind, self._generation] = job
        job.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        if kind == "export":
            job.signals.progress.connect(self._export_progress, Qt.ConnectionType.QueuedConnection)
        pool.start(job)

    @Slot(object)
    def _finished(self, result):
        kind, generation, value, error = result
        self._jobs.pop((kind, generation), None)
        if self._closing:
            return
        if kind == "export":
            cancelled = self._export_cancel is not None and self._export_cancel.is_set()
            self._export_cancel = None
            self._enable()
            if error is None:
                self.progress.setValue(100)
                self.statusBar().showMessage(f"Exported {value}")
            elif cancelled:
                self.progress.setValue(0)
                self.statusBar().showMessage("Export cancelled")
            else:
                self.progress.setValue(0)
                self._error("Export failed", error)
            return
        if generation != self._generation:
            return
        if error is not None:
            self._pause()
            if kind == "prepare":
                self.source_label.setText("Could not prepare source. Open another video or project.")
            if not isinstance(error, PreviewCancelled):
                self._error("Preview failed", error)
            return
        if kind == "prepare":
            self.info, self.project, self.proxy = value
            self._source_ready()
        else:
            index, frame = value
            if index == self._want_frame or self._timer.isActive() or self._shown_frame is None:
                self._show_frame(index, frame)
            self._request_frame()

    def _source_ready(self):
        assert self.info is not None and self.project is not None and self.proxy is not None
        self._proxy_paths.intersection_update((self.proxy.path,))
        info = self.info
        detail = f"{Path(info.path).name} · {info.width} × {info.height} · {info.fps} nominal FPS"
        if info.is_hdr:
            detail += f" · {info.hdr_format or 'HDR'} · uncalibrated proxy colors"
        warnings = getattr(info, "warnings", [])
        if info.hdr_format == "UNKNOWN" and not warnings:
            warnings = ["Unknown color metadata; HDR status is unverified."]
        if warnings:
            detail += "\n" + " ".join(warnings)
        self.source_label.setText(detail)
        for view in (self.source_canvas, self.output_canvas):
            view.project = self.project
            view.source_size = (info.width, info.height)
        self._sync_settings()
        self.timeline.setRange(0, round(self._duration() * 1000))
        self._sync_keys()
        self._seek(0.0)
        self._enable()
        self.statusBar().showMessage("Framing preview ready")

    def _duration(self) -> float:
        if self.info is None or self.proxy is None:
            return 0.0
        return max(self.info.duration, self.proxy.timestamps[-1])

    def _request_frame(self):
        if (self.proxy is None or self._shown_frame == self._want_frame or
                ("frame", self._generation) in self._jobs):
            return
        proxy, index = self.proxy, self._want_frame
        self._start_job("frame", lambda _progress, cancel: (index, proxy.frame(index, cancel)),
                        self._preview_cancel, self._preview_pool)

    def _show_frame(self, index: int, frame: PreviewFrame):
        self._shown_frame = index
        self.source_canvas.set_frame(frame)
        self.output_canvas.set_frame(frame)
        self._refresh_framing()

    def _seek(self, time: float, key_selected: bool = False):
        if self.proxy is None:
            return
        time = max(0.0, min(self._duration(), time))
        self._want_frame = self.proxy.frame_index(time)
        self._time = time if key_selected else self.proxy.timestamps[self._want_frame]
        self._key_selected = key_selected
        self._draft = None
        with QSignalBlocker(self.timeline):
            self.timeline.setValue(round(time * 1000))
        self.time_label.setText(f"{_stamp(self._time)} / {_stamp(self._duration())}")
        self._refresh_framing()
        self._request_frame()

    def _scrub(self, milliseconds: int):
        self._pause()
        self._seek(milliseconds / 1000)

    def _step(self, direction: int):
        self._pause()
        if self.proxy is not None:
            index = max(0, min(len(self.proxy.timestamps) - 1, self._want_frame + direction))
            self._seek(self.proxy.timestamps[index])

    def _toggle_play(self):
        if self._timer.isActive():
            self._pause()
        elif self.proxy is not None:
            if self._want_frame == len(self.proxy.timestamps) - 1:
                self._seek(0.0)
            self._draft = None
            self._clock, self._play_from = monotonic(), self._time
            self._timer.start()
            self.play.setText("Pause")

    def _pause(self):
        self._timer.stop()
        self.play.setText("Play")

    def _tick(self):
        time = self._play_from + monotonic() - self._clock
        self._seek(time)
        if time >= self._duration():
            self._pause()

    def _refresh_framing(self):
        if self.project is None:
            return
        keys = self.project.keyframes
        method = self.project.interpolation
        controls = self._draft or position(keys, self._time, method)
        with QSignalBlocker(self.x), QSignalBlocker(self.y):
            self.x.setValue(controls[0])
            self.y.setValue(controls[1])
        displayed = controls
        if self.proxy is not None and self._shown_frame is not None and self._shown_frame != self._want_frame:
            displayed = position(keys, self.proxy.timestamps[self._shown_frame], method)
        for canvas in (self.source_canvas, self.output_canvas):
            canvas.center = displayed
            canvas.update()
        with QSignalBlocker(self.key_list):
            self.key_list.setCurrentRow(self._current_key())
        self._key_buttons()

    def _current_key(self) -> int:
        if self.project is None:
            return -1
        keys = self.project.keyframes
        index = bisect_left(keys, self._time, key=lambda key: key.time)
        return index if index < len(keys) and keys[index].time == self._time else -1

    def _key_buttons(self):
        ready = self.project is not None and self.proxy is not None
        at_key = self._current_key() >= 0
        self.add_key.setEnabled(ready and not at_key)
        self.update_key.setEnabled(ready and at_key)
        self.delete_key.setEnabled(ready and at_key)

    def _sync_keys(self):
        keys = self.project.keyframes if self.project else []
        self.timeline.marks = tuple(round(key.time * 1000) for key in keys)
        self.timeline.update()
        with QSignalBlocker(self.key_list):
            self.key_list.clear()
            for key in keys:
                self.key_list.addItem(f"{_stamp(key.time)}   X {key.center_x:.3f}   Y {key.center_y:.3f}")
        self._refresh_framing()

    def _edit_position(self):
        self._dragged(self.x.value(), self.y.value())

    def _dragged(self, x: float, y: float):
        self._pause()
        self._draft = (x, y)
        self._refresh_framing()

    def _write_key(self, update: bool):
        if self.project is None or (self._current_key() >= 0) != update:
            return
        self._pause()
        center = self._draft or position(self.project.keyframes, self._time, self.project.interpolation)
        key = Keyframe(self._time, *center)
        keys = self.project.keyframes
        index = bisect_left(keys, key.time, key=lambda item: item.time)
        if update:
            keys[index] = key
        else:
            keys.insert(index, key)
        self._draft = None
        self._sync_keys()

    def _delete_key(self):
        if self.project is not None and (index := self._current_key()) >= 0:
            self._pause()
            del self.project.keyframes[index]
            self._draft = None
            self._sync_keys()

    def _select_key(self):
        if self.project is not None and (row := self.key_list.currentRow()) >= 0:
            self._pause()
            self._seek(self.project.keyframes[row].time, key_selected=True)

    def _navigate_key(self, direction: int):
        if self.project is None:
            return
        keys = self.project.keyframes
        time_of = lambda key: key.time
        index = (bisect_left(keys, self._time, key=time_of) - 1 if direction < 0
                 else bisect_right(keys, self._time, key=time_of))
        if 0 <= index < len(keys):
            self._pause()
            self._seek(keys[index].time, key_selected=True)

    def _sync_settings(self):
        assert self.project is not None
        project = self.project
        with (QSignalBlocker(self.width), QSignalBlocker(self.height), QSignalBlocker(self.aspect),
              QSignalBlocker(self.mode), QSignalBlocker(self.interpolation), QSignalBlocker(self.blur),
              QSignalBlocker(self.dark)):
            self.width.setValue(project.output_width)
            self.height.setValue(project.output_height)
            index = next((i for i, (_, w, h) in enumerate(_ASPECTS) if
                          (w, h) == (project.output_width, project.output_height)), len(_ASPECTS))
            self.aspect.setCurrentIndex(index)
            self.mode.setCurrentIndex(MODES.index(project.mode))
            self.interpolation.setCurrentIndex(METHODS.index(project.interpolation))
            self.blur.setValue(project.blur)
            self.dark.setValue(project.dark_overlay or 0)
        self._settings_enabled()

    def _settings_enabled(self):
        custom = self.aspect.currentIndex() == len(_ASPECTS)
        self.width.setEnabled(custom)
        self.height.setEnabled(custom)
        dynamic = self.mode.currentIndex() != 1
        self.x.setEnabled(dynamic)
        self.y.setEnabled(dynamic)
        self.blur.setEnabled(self.mode.currentIndex() != 0)
        self.dark.setEnabled(self.mode.currentIndex() != 0)

    def _change_aspect(self, index: int):
        if 0 <= index < len(_ASPECTS):
            _, width, height = _ASPECTS[index]
            self.width.setValue(width)
            self.height.setValue(height)
            self._change_dimensions()
        self._settings_enabled()

    def _change_dimensions(self):
        if self.project is None:
            return
        width, height = self.width.value(), self.height.value()
        width, height = width + width % 2, height + height % 2
        self.width.setValue(width)
        self.height.setValue(height)
        self.project.output_width, self.project.output_height = width, height
        self._refresh_framing()

    def _change_settings(self):
        if self.project is None:
            return
        self.project.mode = MODES[self.mode.currentIndex()]
        self.project.interpolation = METHODS[self.interpolation.currentIndex()]
        self.project.blur = self.blur.value()
        self._settings_enabled()
        self._refresh_framing()

    def _change_overlay(self, value: float):
        if self.project is not None:
            self.project.dark_overlay = value
            self.output_canvas.update()

    def _guide(self, name: str, value: bool):
        for canvas in (self.source_canvas, self.output_canvas):
            setattr(canvas, name, value)
            canvas.update()

    def _save(self):
        if self.project_path is not None:
            self.save_project(self.project_path)
        else:
            self._save_as()

    def _save_as(self):
        if self.project is None:
            return
        default = str(self.project_path or Path(self.project.source).with_suffix(".json"))
        path, _ = QFileDialog.getSaveFileName(self, "Save crop project", default, "Crop project (*.json)")
        if path:
            target = Path(path)
            self.save_project(target if target.suffix else target.with_suffix(".json"))

    def save_project(self, path: Path):
        if self.project is None or self.info is None:
            return
        try:
            if path.resolve() == Path(self.project.source).resolve():
                raise ValueError("The crop project must not overwrite its source video")
            self.project.validate(self.info.duration)
            self.project.save(path)
        except (OSError, ValueError) as error:
            self._error("Could not save project", error)
            return
        self.project_path = path
        self.statusBar().showMessage(f"Saved {path.name}")

    def _choose_export(self):
        if self.project is None or self._export_cancel is not None:
            return
        source = Path(self.project.source)
        suggested = source.with_name(f"{source.stem}-crop.mp4")
        path, _ = QFileDialog.getSaveFileName(self, "Export crop from original video", str(suggested), "MP4 video (*.mp4)")
        if path:
            output = Path(path)
            preset = self.preset.currentText() if self.preset.currentIndex() else None
            self.export_to(output if output.suffix else output.with_suffix(".mp4"), preset)

    def export_to(self, path: Path, preset: str | None = None):
        if self.project is None or self._export_cancel is not None:
            return
        if path.resolve() == Path(self.project.source).resolve():
            self._error("Could not export", ValueError("The export must not overwrite its source video"))
            return
        from video_tool.media.encode import export_crop

        snapshot = deepcopy(self.project)
        self._export_cancel = Event()
        self.progress.setValue(0)
        self.statusBar().showMessage("Exporting from the original source…")
        self._enable()

        def encode(progress, cancel):
            export_crop(snapshot, path, preset=preset, progress=progress, cancel=cancel)
            return path

        self._start_job("export", encode, self._export_cancel, self._export_pool)

    @Slot(float)
    def _export_progress(self, fraction: float):
        self.progress.setValue(round(100 * min(max(fraction, 0.0), 1.0)))

    def _cancel_encoding(self):
        if self._export_cancel is not None:
            self._export_cancel.set()
            self.cancel_export.setEnabled(False)
            self.statusBar().showMessage("Cancelling export…")

    def _error(self, title: str, error: Exception):
        self.statusBar().showMessage(title)
        QMessageBox.critical(self, title, str(error))

    def closeEvent(self, event: QCloseEvent):
        self._closing = True
        self._pause()
        self._preview_cancel.set()
        self._cancel_encoding()
        if self._preview_pool.activeThreadCount() or self._export_pool.activeThreadCount():
            event.ignore()
            self.setEnabled(False)
            QTimer.singleShot(80, self.close)
            return
        for path in self._proxy_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        event.accept()


def launch(source: Path | None = None) -> int:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    window = MainWindow(source)
    window.show()
    return app.exec()
