from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QPointF, QRunnable, QRectF, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QConicalGradient, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .config import BridgeConfig, load_config, save_config
from .hue_api import Bridge, HueBridgeClient, Light


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(result)


@dataclass
class LightWidgets:
    container: QFrame
    power_button: QPushButton
    brightness_slider: QSlider
    temperature_slider: QSlider | None
    color_button: QPushButton | None


class ColorWheelWidget(QWidget):
    colorChanged = Signal(QColor)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hue = 32
        self.setMinimumSize(220, 220)
        self.setMaximumSize(260, 260)

    def selected_color(self) -> QColor:
        return QColor.fromHsv(self._hue, 255, 255)

    def set_selected_color(self, color: QColor) -> None:
        hsv = color.toHsv()
        if hsv.hue() >= 0:
            self._hue = hsv.hue()
            self.colorChanged.emit(self.selected_color())
            self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.update_from_position(event.position())

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.buttons() & Qt.LeftButton:
            self.update_from_position(event.position())

    def update_from_position(self, position: QPointF) -> None:
        center = QPointF(self.width() / 2, self.height() / 2)
        dx = position.x() - center.x()
        dy = position.y() - center.y()
        angle = (math.degrees(math.atan2(-dy, dx)) + 360) % 360
        self._hue = round(angle)
        self.colorChanged.emit(self.selected_color())
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        size = min(self.width(), self.height()) - 16
        outer_rect = QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size)

        gradient = QConicalGradient(outer_rect.center(), -90)
        for step in range(0, 361, 30):
            gradient.setColorAt(step / 360, QColor.fromHsv(step % 360, 255, 255))

        pen = QPen()
        pen.setWidth(22)
        pen.setBrush(gradient)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(outer_rect)

        inner_size = size - 70
        inner_rect = QRectF(
            (self.width() - inner_size) / 2,
            (self.height() - inner_size) / 2,
            inner_size,
            inner_size,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(28, 28, 28))
        painter.drawEllipse(inner_rect)
        painter.setBrush(self.selected_color())
        painter.drawEllipse(inner_rect.adjusted(18, 18, -18, -18))

        radius = size / 2 - 11
        angle_rad = math.radians(self._hue)
        center = outer_rect.center()
        handle_x = center.x() + math.cos(angle_rad) * radius
        handle_y = center.y() - math.sin(angle_rad) * radius
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#101010"), 2))
        painter.drawEllipse(QPointF(handle_x, handle_y), 9, 9)


class ColorWheelDialog(QDialog):
    def __init__(self, start_color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick Light Color")
        self.setModal(True)
        self.resize(320, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Color Wheel")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        subtitle = QLabel("Pick a hue for this lamp.")
        subtitle.setStyleSheet("color: #9b978f;")

        self.wheel = ColorWheelWidget()
        self.preview = QLabel()
        self.preview.setFixedHeight(52)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("border-radius: 14px; font-weight: 700;")

        button_row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        apply = QPushButton("Apply")
        apply.clicked.connect(self.accept)
        button_row.addWidget(cancel)
        button_row.addWidget(apply)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.wheel, 0, Qt.AlignCenter)
        layout.addWidget(self.preview)
        layout.addLayout(button_row)

        self.wheel.colorChanged.connect(self.update_preview)
        self.wheel.set_selected_color(start_color)
        self.update_preview(self.wheel.selected_color())

    def update_preview(self, color: QColor) -> None:
        self.preview.setText(color.name().upper())
        self.preview.setStyleSheet(
            "border-radius: 14px; font-weight: 700;"
            f" background: {color.name()}; color: #101010;"
        )

    def selected_color(self) -> QColor:
        return self.wheel.selected_color()


class HueRemoteWindow(QMainWindow):
    HOME_PAGE = 0
    HUB_PAGE = 1

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("philips-hue-for-arch")
        self.resize(1120, 760)
        self.setMinimumSize(920, 640)

        self.thread_pool = QThreadPool()
        self.bridge_config = load_config()
        self.client = HueBridgeClient(
            bridge_ip=self.bridge_config.bridge_ip,
            username=self.bridge_config.username,
        )

        self.current_lights: dict[str, Light] = {}
        self.light_widgets: dict[str, LightWidgets] = {}
        self.discovered_bridges: list[Bridge] = []
        self.selected_bridge: Bridge | None = None
        self.pair_poll_attempts_remaining = 0
        self.active_workers: set[Worker] = set()
        self.light_operation_in_flight = False
        self.refresh_request_token = 0

        self.stack = QStackedWidget()
        self.home_page = self.build_home_page()
        self.hub_page = self.build_hub_page()
        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.hub_page)

        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self.build_sidebar())
        shell_layout.addWidget(self.stack, 1)
        self.setCentralWidget(shell)

        self.apply_styles()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_lights)
        self.refresh_timer.start(15000)

        self.pair_poll_timer = QTimer(self)
        self.pair_poll_timer.setSingleShot(True)
        self.pair_poll_timer.timeout.connect(self.try_pair_bridge)

        self.update_bridge_summary()
        if self.client.is_configured():
            self.show_home_page()
            self.set_status("Bridge paired. Loading lights...")
            self.refresh_lights()
        else:
            self.show_hub_page()
            self.set_hub_status("Add a hub to get started.")

    def build_sidebar(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("DeviceRail")
        rail.setMinimumWidth(210)
        rail.setMaximumWidth(240)

        layout = QVBoxLayout(rail)
        layout.setContentsMargins(20, 28, 20, 28)
        layout.setSpacing(24)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(14)
        brand_mark = QLabel("H")
        brand_mark.setObjectName("BrandMark")
        brand_mark.setAlignment(Qt.AlignCenter)
        brand_mark.setFixedSize(72, 72)

        brand_copy = QVBoxLayout()
        rail_kicker = QLabel("Connected")
        rail_kicker.setObjectName("RailKicker")
        rail_title = QLabel("Devices")
        rail_title.setObjectName("RailTitle")
        brand_copy.addWidget(rail_kicker)
        brand_copy.addWidget(rail_title)

        brand_row.addWidget(brand_mark)
        brand_row.addLayout(brand_copy, 1)

        self.sidebar_summary = QLabel("No Hue bridge paired yet.")
        self.sidebar_summary.setObjectName("SidebarSummary")
        self.sidebar_summary.setWordWrap(True)

        self.nav_lights_button = QPushButton("Lights")
        self.nav_lights_button.setObjectName("RailButton")
        self.nav_lights_button.setCheckable(True)
        self.nav_lights_button.clicked.connect(self.show_home_page)

        self.nav_add_hub_button = QPushButton("Add Hub")
        self.nav_add_hub_button.setObjectName("RailButton")
        self.nav_add_hub_button.setCheckable(True)
        self.nav_add_hub_button.clicked.connect(self.open_add_hub_flow)

        nav_stack = QVBoxLayout()
        nav_stack.setSpacing(10)
        nav_stack.addWidget(self.nav_lights_button)
        nav_stack.addWidget(self.nav_add_hub_button)

        self.sidebar_refresh_button = QPushButton("Refresh Lights")
        self.sidebar_refresh_button.setObjectName("GhostRailButton")
        self.sidebar_refresh_button.clicked.connect(self.refresh_lights)

        self.sidebar_forget_button = QPushButton("Forget Hub")
        self.sidebar_forget_button.setObjectName("GhostRailButton")
        self.sidebar_forget_button.clicked.connect(self.forget_hub)

        footer = QVBoxLayout()
        footer.setSpacing(10)
        footer.addWidget(self.sidebar_refresh_button)
        footer.addWidget(self.sidebar_forget_button)

        layout.addLayout(brand_row)
        layout.addWidget(self.sidebar_summary)
        layout.addLayout(nav_stack)
        layout.addStretch(1)
        layout.addLayout(footer)
        return rail

    def build_home_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 26, 26, 20)
        layout.setSpacing(18)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(18)

        title_stack = QVBoxLayout()
        kicker = QLabel("Lighting Studio")
        kicker.setObjectName("WorkspaceKicker")
        title = QLabel("Philips Hue Control")
        title.setObjectName("WorkspaceTitle")
        title.setWordWrap(True)
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("WorkspaceCopy")
        self.status_label.setWordWrap(True)
        title_stack.addWidget(kicker)
        title_stack.addWidget(title)
        title_stack.addWidget(self.status_label)

        badge_col = QVBoxLayout()
        badge_col.setSpacing(10)
        self.bridge_badge = QLabel("No hub")
        self.bridge_badge.setObjectName("MetaPill")
        self.light_count_badge = QLabel("0 lights")
        self.light_count_badge.setObjectName("StatusPill")
        badge_col.addWidget(self.bridge_badge, 0, Qt.AlignRight)
        badge_col.addWidget(self.light_count_badge, 0, Qt.AlignRight)

        header_layout.addLayout(title_stack, 1)
        header_layout.addLayout(badge_col)

        overview_panel = QFrame()
        overview_panel.setProperty("panel", True)
        overview_layout = QVBoxLayout(overview_panel)
        overview_layout.setContentsMargins(22, 22, 22, 22)
        overview_layout.setSpacing(14)

        overview_kicker = QLabel("Bridge Overview")
        overview_kicker.setObjectName("SurfaceKicker")
        overview_title = QLabel("All lights")
        overview_title.setObjectName("PanelTitle")

        self.home_meta = QLabel("No Hue bridge paired.")
        self.home_meta.setObjectName("MetaBlock")
        self.home_meta.setWordWrap(True)

        top_controls = QHBoxLayout()
        top_controls.setSpacing(10)
        self.add_hub_button = QPushButton("Add Hub")
        self.add_hub_button.setObjectName("SecondaryButton")
        self.add_hub_button.clicked.connect(self.open_add_hub_flow)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("AccentButton")
        self.refresh_button.clicked.connect(self.refresh_lights)

        self.forget_button = QPushButton("Forget")
        self.forget_button.setObjectName("SecondaryButton")
        self.forget_button.clicked.connect(self.forget_hub)

        top_controls.addStretch(1)
        top_controls.addWidget(self.add_hub_button)
        top_controls.addWidget(self.refresh_button)
        top_controls.addWidget(self.forget_button)

        overview_layout.addWidget(overview_kicker)
        overview_layout.addWidget(overview_title)
        overview_layout.addWidget(self.home_meta)
        overview_layout.addLayout(top_controls)

        control_panel = QFrame()
        control_panel.setProperty("panel", True)
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(22, 22, 22, 22)
        control_layout.setSpacing(14)

        lights_kicker = QLabel("Lighting Board")
        lights_kicker.setObjectName("SurfaceKicker")
        lights_title = QLabel("Every light visible")
        lights_title.setObjectName("PanelTitle")
        self.empty_lights_label = QLabel("No lights loaded yet.")
        self.empty_lights_label.setObjectName("WorkspaceCopy")
        self.empty_lights_label.setWordWrap(True)

        self.cards_layout = QGridLayout()
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(14)
        self.cards_layout.setVerticalSpacing(14)

        control_layout.addWidget(lights_kicker)
        control_layout.addWidget(lights_title)
        control_layout.addWidget(self.empty_lights_label)
        control_layout.addLayout(self.cards_layout)
        control_layout.addStretch(1)

        layout.addWidget(header)
        layout.addWidget(overview_panel)
        layout.addWidget(control_panel, 1)
        return page

    def build_hub_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 26, 26, 20)
        layout.setSpacing(18)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(18)

        title_stack = QVBoxLayout()
        kicker = QLabel("Bridge Setup")
        kicker.setObjectName("WorkspaceKicker")
        title = QLabel("Add Hub")
        title.setObjectName("WorkspaceTitle")
        title.setWordWrap(True)
        self.hub_status_label = QLabel("Ready")
        self.hub_status_label.setObjectName("WorkspaceCopy")
        self.hub_status_label.setWordWrap(True)
        title_stack.addWidget(kicker)
        title_stack.addWidget(title)
        title_stack.addWidget(self.hub_status_label)

        self.discovery_badge = QLabel("Waiting")
        self.discovery_badge.setObjectName("MetaPill")

        header_layout.addLayout(title_stack, 1)
        header_layout.addWidget(self.discovery_badge, 0, Qt.AlignTop)

        hub_panel = QFrame()
        hub_panel.setProperty("panel", True)
        panel_layout = QVBoxLayout(hub_panel)
        panel_layout.setContentsMargins(22, 22, 22, 22)
        panel_layout.setSpacing(16)

        subtitle = QLabel(
            "Discover your Philips Hue Bridge on the network, select it, then pair with the physical bridge button."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("HeroSubtitle")

        self.bridge_selector = QComboBox()
        self.bridge_selector.currentIndexChanged.connect(self.on_bridge_selected)
        self.bridge_details = QLabel("No bridge selected yet.")
        self.bridge_details.setObjectName("MetaBlock")
        self.bridge_details.setWordWrap(True)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.back_home_button = QPushButton("Back")
        self.back_home_button.setObjectName("SecondaryButton")
        self.back_home_button.clicked.connect(self.back_to_home)
        self.discover_button = QPushButton("Discover Bridge")
        self.discover_button.setObjectName("SecondaryButton")
        self.discover_button.clicked.connect(self.discover_bridges)
        self.pair_button = QPushButton("Pair Bridge")
        self.pair_button.setObjectName("AccentButton")
        self.pair_button.clicked.connect(self.pair_bridge)
        self.pair_button.setEnabled(False)
        actions.addWidget(self.back_home_button)
        actions.addStretch(1)
        actions.addWidget(self.discover_button)
        actions.addWidget(self.pair_button)

        panel_layout.addWidget(subtitle)
        panel_layout.addWidget(self.bridge_selector)
        panel_layout.addWidget(self.bridge_details)
        panel_layout.addLayout(actions)
        panel_layout.addStretch(1)

        layout.addWidget(header)
        layout.addWidget(hub_panel, 1)
        return page

    def apply_styles(self) -> None:
        app = QApplication.instance()
        if app is not None:
            font = QFont("Segoe UI", 10)
            app.setFont(font)

            palette = QPalette()
            palette.setColor(QPalette.Window, QColor("#111111"))
            palette.setColor(QPalette.WindowText, QColor("#f4f2ee"))
            palette.setColor(QPalette.Base, QColor("#202020"))
            palette.setColor(QPalette.Text, QColor("#f4f2ee"))
            palette.setColor(QPalette.ButtonText, QColor("#f4f2ee"))
            app.setPalette(palette)

        self.setStyleSheet(
            """
            QWidget { color: #f4f2ee; }
            QMainWindow {
                background:
                    qradialgradient(cx: 0.98, cy: 0.02, radius: 0.3, fx: 0.98, fy: 0.02,
                    stop: 0 rgba(243, 154, 35, 0.18), stop: 1 rgba(0, 0, 0, 0)),
                    qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #101010, stop: 0.48 #181818, stop: 1 #111111);
            }
            QFrame#DeviceRail {
                background:
                    qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(255, 255, 255, 0.03), stop: 1 rgba(255, 255, 255, 0.01)),
                    #141414;
                border-right: 1px solid rgba(255, 255, 255, 0.06);
            }
            QLabel#BrandMark {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 #ffffff, stop: 1 #ece8e1);
                color: #181818;
                border-radius: 22px;
                font-size: 30px;
                font-weight: 700;
            }
            QLabel#RailKicker, QLabel#WorkspaceKicker, QLabel#SurfaceKicker {
                color: #f39a23;
                text-transform: uppercase;
                letter-spacing: 0.18em;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#RailTitle {
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#SidebarSummary, QLabel#MetaBlock {
                color: #9b978f;
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
                padding: 14px;
            }
            QLabel#WorkspaceTitle {
                font-size: 38px;
                font-weight: 700;
            }
            QLabel#WorkspaceCopy, QLabel#HeroSubtitle {
                color: #9b978f;
                font-size: 14px;
            }
            QLabel#MetaPill, QLabel#StatusPill {
                background: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 18px;
                padding: 10px 14px;
                color: #f4f2ee;
            }
            QLabel#PanelTitle {
                font-size: 24px;
                font-weight: 700;
            }
            QFrame[panel="true"] {
                background: rgba(28, 28, 28, 0.92);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 22px;
            }
            QPushButton#RailButton {
                text-align: left;
                padding: 14px;
                border-radius: 18px;
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid transparent;
                color: #f4f2ee;
                font-weight: 600;
            }
            QPushButton#RailButton:checked {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(243, 154, 35, 0.14), stop: 1 rgba(243, 154, 35, 0.05));
                border: 1px solid rgba(243, 154, 35, 0.32);
            }
            QPushButton#GhostRailButton, QPushButton#SecondaryButton, QPushButton#CardButton {
                padding: 12px 14px;
                background: rgba(255, 255, 255, 0.06);
                color: #f4f2ee;
                border-radius: 14px;
                border: 1px solid rgba(255, 255, 255, 0.08);
                font-weight: 600;
            }
            QPushButton#AccentButton {
                padding: 12px 16px;
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #f39a23, stop: 1 #ffac43);
                color: #181818;
                border-radius: 14px;
                border: none;
                font-weight: 700;
            }
            QPushButton:hover {
                border-color: rgba(243, 154, 35, 0.35);
            }
            QComboBox {
                min-height: 44px;
                padding: 0 12px;
                border-radius: 14px;
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.08);
            }
            QSlider::groove:horizontal {
                border-radius: 5px;
                height: 10px;
                background: rgba(255, 255, 255, 0.12);
            }
            QSlider::handle:horizontal {
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
                background: #f39a23;
            }
            """
        )

    def run_task(
        self,
        fn: Callable[..., Any],
        on_success: Callable[[Any], None],
        *,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        worker = Worker(fn)
        self.active_workers.add(worker)

        def finish(result: Any) -> None:
            self.active_workers.discard(worker)
            on_success(result)

        def fail(message: str) -> None:
            self.active_workers.discard(worker)
            (on_error or self.show_error)(message)

        worker.signals.finished.connect(finish)
        worker.signals.failed.connect(fail)
        self.thread_pool.start(worker)

    def show_home_page(self) -> None:
        self.stack.setCurrentIndex(self.HOME_PAGE)
        self.nav_lights_button.setChecked(True)
        self.nav_add_hub_button.setChecked(False)

    def show_hub_page(self) -> None:
        self.stack.setCurrentIndex(self.HUB_PAGE)
        self.nav_lights_button.setChecked(False)
        self.nav_add_hub_button.setChecked(True)

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def set_hub_status(self, message: str) -> None:
        self.hub_status_label.setText(message)
        if "looking" in message.lower() or "discover" in message.lower():
            self.discovery_badge.setText("Scanning")
        elif "waiting" in message.lower():
            self.discovery_badge.setText("Waiting")
        else:
            self.discovery_badge.setText("Ready")

    def show_error(self, message: str) -> None:
        if self.stack.currentIndex() == self.HUB_PAGE:
            self.set_hub_status(message)
        else:
            self.set_status(message)
        QMessageBox.warning(self, "philips-hue-for-arch", message)

    def handle_task_error(self, message: str) -> None:
        self.set_controls_enabled(True)
        self.light_operation_in_flight = False
        if "unauthorized user" in message.lower():
            self.clear_saved_pairing()
            self.current_lights.clear()
            self.rebuild_light_cards()
            self.show_hub_page()
            self.set_hub_status(
                "The saved bridge pairing is no longer valid. Discover the bridge again, then press Pair Bridge."
            )
            return
        self.show_error(message)

    def persist_config(self) -> None:
        save_config(
            BridgeConfig(
                bridge_ip=self.client.bridge_ip,
                username=self.client.username,
            )
        )
        self.update_bridge_summary()

    def update_bridge_summary(self) -> None:
        if self.client.bridge_ip:
            self.sidebar_summary.setText(f"Hue Bridge\n{self.client.bridge_ip}")
            self.bridge_badge.setText(self.client.bridge_ip)
            self.home_meta.setText(
                f"Bridge IP: {self.client.bridge_ip}\n"
                f"Pairing: {'active' if self.client.username else 'not paired'}"
            )
        else:
            self.sidebar_summary.setText("No Hue bridge paired yet.")
            self.bridge_badge.setText("No hub")
            self.home_meta.setText("No Hue bridge paired.")

    def open_add_hub_flow(self) -> None:
        self.show_hub_page()
        self.set_hub_status("Discover a bridge to begin pairing.")

    def back_to_home(self) -> None:
        if self.client.is_configured():
            self.show_home_page()
            self.set_status("Ready.")
        else:
            self.set_hub_status("Add a hub to continue.")

    def discover_bridges(self) -> None:
        self.selected_bridge = None
        self.bridge_selector.clear()
        self.bridge_details.setText("Searching for bridges on your network...")
        self.pair_button.setEnabled(False)
        self.set_hub_status("Looking for Hue bridges...")
        self.run_task(self.client.discover_bridges, self.on_bridges_discovered, on_error=self.handle_task_error)

    def on_bridges_discovered(self, bridges: list[Bridge]) -> None:
        self.discovered_bridges = bridges
        self.bridge_selector.clear()
        self.discovery_badge.setText(f"{len(bridges)} found")
        for bridge in bridges:
            self.bridge_selector.addItem(f"Hue Bridge {bridge.ip_address}", bridge)
        if bridges:
            self.bridge_selector.setCurrentIndex(0)
            self.set_hub_status("Bridge found. Click Pair Bridge, then press the physical bridge button.")
        else:
            self.bridge_details.setText("No bridge selected yet.")
            self.pair_button.setEnabled(False)

    def on_bridge_selected(self, index: int) -> None:
        if index < 0:
            self.selected_bridge = None
            self.pair_button.setEnabled(False)
            self.bridge_details.setText("No bridge selected yet.")
            return
        bridge = self.bridge_selector.currentData()
        if not isinstance(bridge, Bridge):
            return
        self.selected_bridge = bridge
        self.pair_button.setEnabled(True)
        self.bridge_details.setText(
            f"Selected bridge\n\n"
            f"IP address: {bridge.ip_address}\n"
            f"Bridge ID: {bridge.bridge_id}\n\n"
            f"Click Pair Bridge and press the physical button on this bridge."
        )

    def pair_bridge(self) -> None:
        if self.selected_bridge is None:
            self.show_error("Select a bridge first.")
            return
        self.client.bridge_ip = self.selected_bridge.ip_address
        self.client.username = ""
        self.persist_config()
        self.pair_poll_attempts_remaining = 30
        self.pair_button.setEnabled(False)
        self.set_hub_status("Waiting for the bridge button. Press it now and the app will detect it automatically.")
        self.try_pair_bridge()

    def try_pair_bridge(self) -> None:
        self.run_task(self.client.create_user, self.on_bridge_paired, on_error=self.handle_pair_error)

    def on_bridge_paired(self, username: str) -> None:
        self.pair_poll_timer.stop()
        self.pair_poll_attempts_remaining = 0
        self.client.username = username
        self.persist_config()
        self.pair_button.setEnabled(True)
        self.show_home_page()
        self.set_status("Bridge paired successfully. Loading lights...")
        self.refresh_lights()

    def handle_pair_error(self, message: str) -> None:
        if "link button not pressed" in message.lower():
            self.pair_poll_attempts_remaining -= 1
            if self.pair_poll_attempts_remaining > 0:
                self.set_hub_status(
                    f"Waiting for bridge button press... {self.pair_poll_attempts_remaining}s remaining."
                )
                self.pair_poll_timer.start(1000)
                return
            self.pair_button.setEnabled(True)
            self.set_hub_status("Pairing timed out. Press the bridge button and try Pair Bridge again.")
            return
        self.pair_poll_timer.stop()
        self.pair_poll_attempts_remaining = 0
        self.pair_button.setEnabled(True)
        self.handle_task_error(message)

    def forget_hub(self) -> None:
        self.pair_poll_timer.stop()
        self.pair_poll_attempts_remaining = 0
        self.clear_saved_pairing()
        self.current_lights.clear()
        self.rebuild_light_cards()
        self.show_hub_page()
        self.set_hub_status("Hub removed. Discover a bridge to add it again.")
        self.light_count_badge.setText("0 lights")

    def clear_saved_pairing(self) -> None:
        self.client.bridge_ip = ""
        self.client.username = ""
        self.persist_config()

    def refresh_lights(self) -> None:
        if not self.client.is_configured():
            self.show_hub_page()
            self.set_hub_status("Add a hub before loading lights.")
            return
        if self.light_operation_in_flight:
            return
        self.set_status("Refreshing lights...")
        self.refresh_request_token += 1
        request_token = self.refresh_request_token
        self.run_task(
            self.client.list_lights,
            lambda lights, token=request_token: self.render_lights_if_current(token, lights),
            on_error=self.handle_task_error,
        )

    def render_lights_if_current(self, token: int, lights: list[Light]) -> None:
        if token != self.refresh_request_token:
            return
        self.current_lights = {light.light_id: light for light in lights}
        self.light_count_badge.setText(f"{len(lights)} lights")
        self.rebuild_light_cards()
        self.set_controls_enabled(True)
        self.light_operation_in_flight = False
        if lights:
            self.set_status(f"Loaded {len(lights)} light{'s' if len(lights) != 1 else ''}.")
        else:
            self.set_status("No lights available.")

    def set_controls_enabled(self, enabled: bool) -> None:
        self.refresh_button.setEnabled(enabled)
        self.add_hub_button.setEnabled(enabled)
        self.forget_button.setEnabled(enabled)
        self.sidebar_refresh_button.setEnabled(enabled)
        self.sidebar_forget_button.setEnabled(enabled)
        for widgets in self.light_widgets.values():
            widgets.container.setEnabled(enabled)

    def format_temperature(self, mirek: int | None) -> str:
        kelvin = HueBridgeClient.mirek_to_kelvin(mirek)
        return f"{kelvin}K"

    def rebuild_light_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.light_widgets.clear()

        lights = list(self.current_lights.values())
        self.empty_lights_label.setVisible(not lights)
        if not lights:
            return
        columns = 4 if len(lights) >= 4 else max(1, len(lights))
        for index, light in enumerate(lights):
            card = self.build_light_card(light)
            self.cards_layout.addWidget(card, index // columns, index % columns)

    def build_light_card(self, light: Light) -> QFrame:
        card = QFrame()
        card.setProperty("panel", True)
        card.setMinimumHeight(196)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        name_label = QLabel(light.name)
        name_label.setWordWrap(True)
        name_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        status_label = QLabel("Online" if light.reachable else "Unavailable")
        status_label.setStyleSheet(
            f"color: {'#70c18d' if light.reachable else '#d46b5f'}; font-size: 12px;"
        )

        power_button = QPushButton("Off" if light.is_on else "On")
        power_button.setObjectName("AccentButton" if light.is_on else "SecondaryButton")
        power_button.setMinimumHeight(40)
        power_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        power_button.clicked.connect(lambda _=False, light_id=light.light_id: self.toggle_light(light_id))

        brightness_title = QLabel("Brightness")
        brightness_title.setStyleSheet("color: #9b978f; font-size: 12px;")
        brightness_slider = QSlider(Qt.Horizontal)
        brightness_slider.setRange(1, 100)
        brightness_slider.setValue(max(1, light.brightness))
        brightness_value = QLabel(f"{light.brightness}%")
        brightness_value.setStyleSheet("font-weight: 700;")
        brightness_slider.valueChanged.connect(lambda value, label=brightness_value: label.setText(f"{value}%"))
        brightness_slider.sliderReleased.connect(
            lambda light_id=light.light_id, slider=brightness_slider: self.change_brightness(light_id, slider.value())
        )
        brightness_row = QHBoxLayout()
        brightness_row.addWidget(brightness_slider, 1)
        brightness_row.addWidget(brightness_value)

        temperature_slider: QSlider | None = None
        color_button: QPushButton | None = None

        layout.addWidget(name_label)
        layout.addWidget(status_label)
        layout.addWidget(power_button)
        layout.addWidget(brightness_title)
        layout.addLayout(brightness_row)

        if light.supports_temperature and not light.supports_color:
            temp_title = QLabel("Temperature")
            temp_title.setStyleSheet("color: #9b978f; font-size: 12px;")
            temperature_slider = QSlider(Qt.Horizontal)
            minimum_ct = light.temperature_min or 153
            maximum_ct = light.temperature_max or 500
            current_ct = light.color_temperature or minimum_ct
            temperature_slider.setRange(minimum_ct, maximum_ct)
            temperature_slider.setValue(current_ct)
            temp_value = QLabel(self.format_temperature(current_ct))
            temp_value.setStyleSheet("font-weight: 700;")
            temperature_slider.valueChanged.connect(
                lambda value, label=temp_value: label.setText(self.format_temperature(value))
            )
            temperature_slider.sliderReleased.connect(
                lambda light_id=light.light_id, slider=temperature_slider: self.change_temperature(light_id, slider.value())
            )
            temp_row = QHBoxLayout()
            temp_row.addWidget(temperature_slider, 1)
            temp_row.addWidget(temp_value)
            layout.addWidget(temp_title)
            layout.addLayout(temp_row)
        elif light.supports_color:
            color_button = QPushButton("Color Wheel")
            color_button.setObjectName("SecondaryButton")
            color_button.setMinimumHeight(40)
            if light.xy:
                red, green, blue = HueBridgeClient.xy_to_rgb(light.xy[0], light.xy[1], light.brightness)
                color_button.setStyleSheet(
                    "border-radius: 14px; font-weight: 700;"
                    f" background: rgb({red}, {green}, {blue}); color: #101010;"
                )
            color_button.clicked.connect(lambda _=False, light_id=light.light_id: self.pick_color(light_id))
            layout.addWidget(color_button)

        layout.addStretch(1)
        self.light_widgets[light.light_id] = LightWidgets(
            container=card,
            power_button=power_button,
            brightness_slider=brightness_slider,
            temperature_slider=temperature_slider,
            color_button=color_button,
        )
        return card

    def toggle_light(self, light_id: str) -> None:
        light = self.current_lights.get(light_id)
        if light is None or self.light_operation_in_flight:
            return
        self.light_operation_in_flight = True
        self.set_controls_enabled(False)
        self.set_status(f"Updating {light.name}...")
        self.run_task(
            lambda: self.client.set_power(light.light_id, not light.is_on),
            lambda _: self.after_light_change(f"{light.name} updated."),
            on_error=self.handle_task_error,
        )

    def change_brightness(self, light_id: str, brightness: int) -> None:
        light = self.current_lights.get(light_id)
        if light is None or self.light_operation_in_flight:
            return
        self.light_operation_in_flight = True
        self.set_controls_enabled(False)
        self.set_status(f"Setting {light.name} brightness to {brightness}%...")
        self.run_task(
            lambda: self.client.set_brightness(light.light_id, brightness),
            lambda _: self.after_light_change(f"{light.name} brightness updated."),
            on_error=self.handle_task_error,
        )

    def change_temperature(self, light_id: str, mirek: int) -> None:
        light = self.current_lights.get(light_id)
        if light is None or self.light_operation_in_flight:
            return
        self.light_operation_in_flight = True
        self.set_controls_enabled(False)
        self.set_status(f"Setting {light.name} temperature to {self.format_temperature(mirek)}...")
        self.run_task(
            lambda: self.client.set_color_temperature(light.light_id, mirek),
            lambda _: self.after_light_change(f"{light.name} temperature updated."),
            on_error=self.handle_task_error,
        )

    def pick_color(self, light_id: str) -> None:
        light = self.current_lights.get(light_id)
        if light is None or self.light_operation_in_flight:
            return
        start_color = QColor(255, 199, 122)
        if light.xy:
            red, green, blue = HueBridgeClient.xy_to_rgb(light.xy[0], light.xy[1], light.brightness)
            start_color = QColor(red, green, blue)
        dialog = ColorWheelDialog(start_color, self)
        if dialog.exec() != QDialog.Accepted:
            return
        color = dialog.selected_color()
        self.light_operation_in_flight = True
        self.set_controls_enabled(False)
        self.set_status(f"Updating {light.name} color...")
        self.run_task(
            lambda: self.client.set_color_rgb(light.light_id, color.red(), color.green(), color.blue()),
            lambda _: self.after_light_change(f"{light.name} color updated."),
            on_error=self.handle_task_error,
        )

    def after_light_change(self, message: str) -> None:
        self.set_status(message)
        self.refresh_request_token += 1
        request_token = self.refresh_request_token
        self.run_task(
            self.client.list_lights,
            lambda lights, token=request_token: self.render_lights_if_current(token, lights),
            on_error=self.handle_task_error,
        )


def main() -> int:
    app = QApplication(sys.argv)
    window = HueRemoteWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
