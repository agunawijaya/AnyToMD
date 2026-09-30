from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from . import keystore
from .ai_client import AIClient, create_client
from .config import Config, PROVIDER_LABELS, ALL_PROVIDERS, Provider
from .converters import convert
from .options import ConvertOptions

SUPPORTED = {".pdf", ".pptx", ".docx", ".xlsx", ".html", ".htm",
             ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# Global QSS — kept minimal to avoid breaking native look on non-Windows.
# Focuses on the two elements users notice most: hero drop zone + option cards.
_APP_QSS = """
QMainWindow, QDialog {
    background: #f7f8fa;
}
QLabel#dropZone {
    border: 2px dashed #b6bdc8;
    border-radius: 14px;
    background: #ffffff;
    padding: 24px;
    color: #4a5566;
    font-size: 15px;
}
QLabel#dropZone:hover {
    border-color: #4a90e2;
    background: #f4f9ff;
}
QLabel#dropZoneActive {
    border-color: #4a90e2;
    background: #eaf3fc;
    color: #1e5aa4;
}
QGroupBox {
    border: 1px solid #e1e5eb;
    border-radius: 8px;
    background: #ffffff;
    margin-top: 12px;
    padding: 10px 12px 12px 12px;
    font-weight: 600;
    color: #2a3345;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    background: #f7f8fa;
}
QToolBar {
    background: #ffffff;
    border-bottom: 1px solid #e1e5eb;
    padding: 6px 8px;
    spacing: 6px;
}
QToolBar QToolButton {
    padding: 6px 10px;
    border-radius: 6px;
}
QToolBar QToolButton:hover {
    background: #eef1f5;
}
QStatusBar {
    background: #ffffff;
    border-top: 1px solid #e1e5eb;
    color: #4a5566;
}
QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #e1e5eb;
    border-radius: 8px;
    padding: 8px;
    font-family: "Consolas", "Menlo", monospace;
    font-size: 12px;
}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    padding: 4px 6px;
    border: 1px solid #d0d5dd;
    border-radius: 6px;
    background: #ffffff;
}
QPushButton {
    padding: 6px 14px;
    border-radius: 6px;
    background: #ffffff;
    border: 1px solid #d0d5dd;
}
QPushButton:hover { background: #eef1f5; }
QPushButton:pressed { background: #e4e8ee; }
QPushButton:default {
    background: #2f6de5;
    color: white;
    border: 1px solid #2f6de5;
}
QPushButton:default:hover { background: #2761d1; }
"""


class ConvertWorker(QObject):
    progress = Signal(str)
    finished = Signal(Path, str, str)  # output_path, markdown, stats_summary
    failed = Signal(str)

    def __init__(self, input_path: Path, output_path: Path, ai: AIClient,
                 options: ConvertOptions):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.ai = ai
        self.options = options

    def run(self) -> None:
        try:
            md = convert(self.input_path, self.ai,
                         progress=self.progress.emit, options=self.options)
            self.output_path.write_text(md, encoding="utf-8")
            self.finished.emit(self.output_path, md, self.options.stats_summary())
        except Exception:
            self.failed.emit(traceback.format_exc())


class DropZone(QLabel):
    """Hero drop-zone widget. Click to browse, drag+drop to convert.

    Uses `objectName` "dropZone" (idle) / "dropZoneActive" (during drag-over)
    for QSS styling. Big padding + rounded dashed border make it feel like
    the primary action of the app.
    """
    file_dropped = Signal(Path)
    browse_requested = Signal()

    _IDLE_TEXT = (
        "<div style='font-size:22px; font-weight:600;'>Drop file di sini</div>"
        "<div style='margin-top:6px;'>atau klik untuk browse</div>"
        "<div style='margin-top:14px; color:#7b8494; font-size:11px;'>"
        "PDF · PPTX · DOCX · XLSX · HTML · PNG · JPG · WEBP · GIF · BMP</div>"
    )
    _ACTIVE_TEXT = (
        "<div style='font-size:22px; font-weight:600;'>Lepas di sini</div>"
        "<div style='margin-top:6px;'>untuk mulai konversi</div>"
    )

    def __init__(self):
        super().__init__()
        self.setObjectName("dropZone")
        self.setTextFormat(Qt.RichText)
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setCursor(Qt.PointingHandCursor)
        self.setText(self._IDLE_TEXT)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = Path(url.toLocalFile())
                if p.suffix.lower() in SUPPORTED:
                    event.acceptProposedAction()
                    self._set_active(True)
                    return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._set_active(False)
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.suffix.lower() in SUPPORTED:
                self.file_dropped.emit(p)
                return

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.browse_requested.emit()
        super().mousePressEvent(event)

    def _set_active(self, active: bool) -> None:
        self.setObjectName("dropZoneActive" if active else "dropZone")
        # Re-apply stylesheet to reflect changed selector
        self.setText(self._ACTIVE_TEXT if active else self._IDLE_TEXT)
        self.style().unpolish(self)
        self.style().polish(self)


class ApiKeyDialog(QDialog):
    """Masked API key input dialog.

    - Password-masked by default (dots).
    - 👁 toggle to peek at content (default hidden).
    - Save button disabled until non-empty.
    - Info label reminds where the key is stored + threat model.
    """

    def __init__(self, parent, provider_label: str, existing_masked_hint: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Set API key — {provider_label}")
        self.setMinimumWidth(460)
        self.setModal(True)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Kunci akan disimpan terenkripsi di <b>Windows Credential Manager</b> "
            "(DPAPI). Hanya akun Windows ini di komputer ini yang bisa akses.<br><br>"
            "<i>Kunci tidak akan pernah ditampilkan lagi setelah disimpan.</i>"
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.RichText)
        layout.addWidget(info)

        if existing_masked_hint:
            hint = QLabel(f"Nilai sekarang: <code>{existing_masked_hint}</code>")
            hint.setTextFormat(Qt.RichText)
            layout.addWidget(hint)

        row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("Paste API key di sini")
        self.key_edit.textChanged.connect(self._on_text_changed)
        row.addWidget(self.key_edit)

        self.peek_btn = QPushButton("👁")
        self.peek_btn.setCheckable(True)
        self.peek_btn.setFixedWidth(36)
        self.peek_btn.setToolTip("Tampilkan / sembunyikan sementara")
        self.peek_btn.toggled.connect(self._on_peek)
        row.addWidget(self.peek_btn)
        layout.addLayout(row)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, self
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.save_btn = self.buttons.button(QDialogButtonBox.Save)
        self.save_btn.setEnabled(False)
        layout.addWidget(self.buttons)

    def _on_text_changed(self, text: str) -> None:
        self.save_btn.setEnabled(bool(text.strip()))

    def _on_peek(self, checked: bool) -> None:
        self.key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def key(self) -> str:
        return self.key_edit.text().strip()


class ProviderPanel(QGroupBox):
    """Dropdown for main AI provider + per-image-kind hybrid routing.

    Provider list matches `config.ALL_PROVIDERS`. API keys stay in .env (this
    panel only picks which provider to use per conversion, not enters keys).
    Warns if API key for the currently-selected provider is missing.
    """

    provider_changed = Signal()

    def __init__(self, config: Config):
        super().__init__("Pilih AI provider")
        self.config = config
        outer = QVBoxLayout(self)

        form = QFormLayout()
        # Main provider
        self.main_combo = QComboBox()
        for prov in ALL_PROVIDERS:
            self.main_combo.addItem(PROVIDER_LABELS[prov], userData=prov)
        _select_provider(self.main_combo, config.provider)
        self.main_combo.currentIndexChanged.connect(self._on_change)
        form.addRow("Provider utama:", self.main_combo)

        # Hybrid per-kind combos: each has "(sama dengan utama)" + all providers
        self._kind_combos: dict[str, QComboBox] = {}
        for kind_label, kind_key in [
            ("Diagram → ",    "diagram"),
            ("Chart → ",      "chart"),
            ("Screenshot → ", "screenshot"),
            ("Foto → ",       "photo"),
        ]:
            combo = QComboBox()
            combo.addItem("(sama dengan utama)", userData=None)
            for prov in ALL_PROVIDERS:
                combo.addItem(PROVIDER_LABELS[prov], userData=prov)
            preset = config.image_provider_by_kind.get(kind_key)
            if preset:
                _select_provider(combo, preset)
            combo.currentIndexChanged.connect(self._on_change)
            self._kind_combos[kind_key] = combo
            form.addRow(kind_label, combo)

        outer.addLayout(form)

        # Warning label (updated by _on_change)
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #b06000;")
        outer.addWidget(self.warning_label)

        self._on_change()

    def refresh(self) -> None:
        """Called by MainWindow after Settings dialog closes so warning label
        reflects newly-saved keys without needing an app restart."""
        self._on_change()

    def _on_change(self) -> None:
        # Push selections back into config so `start_conversion` picks them up
        self.config.provider = self.main_combo.currentData() or "anthropic"
        per_kind: dict[str, Provider] = {}
        for kind, combo in self._kind_combos.items():
            val = combo.currentData()
            if val:
                per_kind[kind] = val
        self.config.image_provider_by_kind = per_kind

        # Warn about missing keys
        missing = [PROVIDER_LABELS[p] for p in self.config.all_providers_in_use()
                   if not self.config.has_key_for(p)]
        if missing:
            self.warning_label.setText(
                f"⚠️  API key belum diisi untuk: {', '.join(missing)}. "
                f"Buka Settings (⚙) untuk mengisinya."
            )
        else:
            self.warning_label.setText("")
        self.provider_changed.emit()


class _KeyRow(QWidget):
    """One row per provider inside ProviderPanel's "API keys" group.

    Shows: provider label · status icon · [Set/Ubah] [Hapus]
    Never displays the key itself.
    """

    _STATUS_ICON = {
        "env":     "📄 dari environment (.env atau shell)",
        "keyring": "🔑 tersimpan (keyring)",
        "missing": "❌ belum ada",
    }

    def __init__(self, provider: Provider, config: Config, on_change):
        super().__init__()
        self.provider = provider
        self.config = config
        self._on_change = on_change

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)

        self.label = QLabel(PROVIDER_LABELS[provider])
        self.label.setMinimumWidth(160)
        row.addWidget(self.label)

        self.status_label = QLabel("")
        self.status_label.setMinimumWidth(240)
        row.addWidget(self.status_label, stretch=1)

        self.set_btn = QPushButton("Set…")
        self.set_btn.clicked.connect(self._on_set)
        row.addWidget(self.set_btn)

        self.delete_btn = QPushButton("Hapus")
        self.delete_btn.clicked.connect(self._on_delete)
        row.addWidget(self.delete_btn)

        self.refresh()

    def refresh(self) -> None:
        src = self.config.key_source(self.provider)
        self.status_label.setText(self._STATUS_ICON.get(src, "?"))
        # Buttons: "Set…" turns into "Ubah…" when a key exists via keyring.
        # Env-var-sourced keys can't be modified from the UI (must edit .env
        # or shell env), so we disable "Ubah" and only allow "Set…" that will
        # save to keyring (but env still wins on read).
        if src == "keyring":
            self.set_btn.setText("Ubah…")
            self.delete_btn.setEnabled(True)
        elif src == "env":
            self.set_btn.setText("Simpan ke keyring…")
            self.set_btn.setToolTip(
                "Env var akan tetap menang saat dibaca. "
                "Hapus env dari .env kalau ingin murni pakai keyring."
            )
            self.delete_btn.setEnabled(bool(keystore.get_key(self.provider)))
        else:
            self.set_btn.setText("Set…")
            self.set_btn.setToolTip("")
            self.delete_btn.setEnabled(False)

    def _on_set(self) -> None:
        if not keystore.is_available():
            QMessageBox.warning(
                self, "Keyring tidak tersedia",
                "Backend keyring tidak aktif di sistem ini. "
                "Silakan set API key via .env atau environment variable."
            )
            return
        dlg = ApiKeyDialog(self, PROVIDER_LABELS[self.provider])
        if dlg.exec() != QDialog.Accepted:
            return
        key = dlg.key()
        if not key:
            return
        ok = keystore.set_key(self.provider, key)
        if not ok:
            QMessageBox.critical(self, "Gagal menyimpan", "Keyring backend menolak penyimpanan.")
            return
        self._on_change()

    def _on_delete(self) -> None:
        # Only touch keyring — env-based key must be removed manually.
        stored = keystore.get_key(self.provider)
        if not stored:
            QMessageBox.information(
                self, "Tidak ada di keyring",
                "Tidak ada kunci di keyring untuk provider ini. "
                "Kalau kunci datang dari .env, edit file .env langsung."
            )
            return
        ret = QMessageBox.question(
            self, "Hapus kunci?",
            f"Hapus kunci {PROVIDER_LABELS[self.provider]} dari Windows Credential Manager?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        keystore.delete_key(self.provider)
        self._on_change()


class SettingsDialog(QDialog):
    """Non-modal settings: API keys, models & endpoints, advanced.

    Design intent: everything that changes rarely (per-provider keys, model
    IDs, base URLs, Ollama host, cost cap) lives here. The main window keeps
    only per-conversion choices (which provider, hybrid routing, image filters).
    """

    settings_changed = Signal()  # main window listens to refresh status

    def __init__(self, parent, config: Config):
        super().__init__(parent)
        self.setWindowTitle("Settings — AnyToMD")
        self.setMinimumSize(640, 480)
        self.config = config

        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_keys_tab(),    "🔐  API Keys")
        tabs.addTab(self._build_routing_tab(), "🎯  Hybrid routing")
        tabs.addTab(self._build_models_tab(),  "🔧  Model & endpoint")
        tabs.addTab(self._build_advanced_tab(),"⚙  Advanced")
        tabs.addTab(self._build_info_tab(),    "ℹ  Info")
        outer.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        # "Close" is the only visible action — settings save on-change per-widget.
        close_btn = buttons.button(QDialogButtonBox.Close)
        close_btn.clicked.connect(self.accept)
        outer.addWidget(buttons)

    # ---- Tabs ----

    def _build_keys_tab(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)

        backend_text = keystore.backend_name() if keystore.is_available() else "tidak tersedia"
        intro = QLabel(
            f"Storage backend: <b>{backend_text}</b>.<br>"
            f"Kunci disimpan terenkripsi per-user, per-komputer. "
            f"Environment variable (shell atau <code>.env</code>) selalu menang "
            f"kalau keduanya di-set. Kunci tidak pernah ditampilkan lagi setelah disimpan."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        layout.addWidget(intro)

        layout.addWidget(_hline())

        self._key_rows: dict[Provider, _KeyRow] = {}
        for prov in ("anthropic", "openai", "gemini", "moonshot"):
            row = _KeyRow(prov, self.config, on_change=self._on_key_changed)
            self._key_rows[prov] = row
            layout.addWidget(row)

        layout.addStretch(1)
        return wrap

    def _build_routing_tab(self) -> QWidget:
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.addWidget(QLabel(
            "<i>Route each image kind ke provider yang paling cocok. "
            "Contoh: diagram → Anthropic (kualitas), foto → Gemini (paling murah).<br>"
            "Pilih '(sama dengan utama)' agar mengikuti Provider utama di toolbar.</i>"
        ))
        outer.addWidget(_hline())

        form = QFormLayout()
        self._route_combos: dict[str, QComboBox] = {}
        for label, kind in [
            ("Diagram → ",    "diagram"),
            ("Chart → ",      "chart"),
            ("Screenshot → ", "screenshot"),
            ("Foto → ",       "photo"),
        ]:
            combo = QComboBox()
            combo.addItem("(sama dengan utama)", userData=None)
            for prov in ALL_PROVIDERS:
                combo.addItem(PROVIDER_LABELS[prov], userData=prov)
            preset = self.config.image_provider_by_kind.get(kind)
            if preset:
                _select_provider(combo, preset)
            combo.currentIndexChanged.connect(
                lambda _i, k=kind, c=combo: self._on_route_changed(k, c)
            )
            self._route_combos[kind] = combo
            form.addRow(label, combo)
        outer.addLayout(form)
        outer.addStretch(1)
        return wrap

    def _on_route_changed(self, kind: str, combo: QComboBox) -> None:
        val = combo.currentData()
        if val:
            self.config.image_provider_by_kind[kind] = val
        else:
            self.config.image_provider_by_kind.pop(kind, None)
        self.settings_changed.emit()

    def _build_models_tab(self) -> QWidget:
        wrap = QWidget()
        form = QFormLayout(wrap)
        form.addRow(QLabel(
            "<i>Model ID + endpoint. Perubahan disimpan ke memori untuk sesi "
            "ini; untuk persistent set via <code>.env</code>.</i>"
        ))
        form.addRow(_hline())

        # Anthropic
        self.ed_anthropic_model = QLineEdit(self.config.anthropic_model)
        self.ed_anthropic_model.textChanged.connect(
            lambda v: setattr(self.config, "anthropic_model", v.strip())
        )
        form.addRow("Anthropic model:", self.ed_anthropic_model)

        # OpenAI
        self.ed_openai_model = QLineEdit(self.config.openai_model)
        self.ed_openai_model.textChanged.connect(
            lambda v: setattr(self.config, "openai_model", v.strip())
        )
        form.addRow("OpenAI model:", self.ed_openai_model)

        self.ed_openai_base = QLineEdit(self.config.openai_base_url)
        self.ed_openai_base.setPlaceholderText("(kosong = default)")
        self.ed_openai_base.textChanged.connect(
            lambda v: setattr(self.config, "openai_base_url", v.strip())
        )
        form.addRow("OpenAI base URL:", self.ed_openai_base)

        # Gemini
        self.ed_gemini_model = QLineEdit(self.config.gemini_model)
        self.ed_gemini_model.textChanged.connect(
            lambda v: setattr(self.config, "gemini_model", v.strip())
        )
        form.addRow("Gemini model:", self.ed_gemini_model)

        # Moonshot
        self.ed_moonshot_model = QLineEdit(self.config.moonshot_model)
        self.ed_moonshot_model.textChanged.connect(
            lambda v: setattr(self.config, "moonshot_model", v.strip())
        )
        form.addRow("Moonshot model:", self.ed_moonshot_model)

        self.ed_moonshot_base = QLineEdit(self.config.moonshot_base_url)
        self.ed_moonshot_base.textChanged.connect(
            lambda v: setattr(self.config, "moonshot_base_url", v.strip())
        )
        form.addRow("Moonshot base URL:", self.ed_moonshot_base)

        # Ollama
        self.ed_ollama_host = QLineEdit(self.config.ollama_host)
        self.ed_ollama_host.textChanged.connect(
            lambda v: setattr(self.config, "ollama_host", v.strip())
        )
        form.addRow("Ollama host:", self.ed_ollama_host)

        self.ed_ollama_model = QLineEdit(self.config.ollama_model)
        self.ed_ollama_model.textChanged.connect(
            lambda v: setattr(self.config, "ollama_model", v.strip())
        )
        form.addRow("Ollama model:", self.ed_ollama_model)

        return wrap

    def _build_advanced_tab(self) -> QWidget:
        wrap = QWidget()
        form = QFormLayout(wrap)
        form.addRow(QLabel(
            "<i>Budget cap otomatis stop panggilan cloud saat batas biaya "
            "tercapai. Sisa gambar di-downgrade ke Ollama (kalau tersedia) "
            "atau di-skip. 0 = tanpa cap.</i>"
        ))
        form.addRow(_hline())

        self.spin_cap = QDoubleSpinBox()
        self.spin_cap.setRange(0.0, 1000.0)
        self.spin_cap.setDecimals(2)
        self.spin_cap.setSingleStep(0.10)
        self.spin_cap.setPrefix("$")
        self.spin_cap.setValue(self.config.cost_cap_usd)
        self.spin_cap.valueChanged.connect(
            lambda v: setattr(self.config, "cost_cap_usd", float(v))
        )
        form.addRow("Budget cap (USD):", self.spin_cap)

        return wrap

    def _build_info_tab(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        text = QLabel(
            "<h3>Where API keys live</h3>"
            "<p>Precedence saat app membaca kunci:</p>"
            "<ol>"
            "<li><b>Environment variable</b> (shell atau <code>.env</code>) — paling tinggi.</li>"
            "<li><b>Keyring</b> — disimpan via Windows Credential Manager (DPAPI).</li>"
            "<li><b>Kosong</b> — provider tidak bisa dipakai.</li>"
            "</ol>"
            f"<p>Backend keyring aktif: <b>{keystore.backend_name()}</b></p>"
            "<h3>Threat model — apa yang dijamin</h3>"
            "<ul>"
            "<li>✅ Encrypted at rest via OS-native store (DPAPI di Windows).</li>"
            "<li>✅ Only same user + same machine bisa decrypt.</li>"
            "<li>✅ Tidak plain-text di folder project (aman dari git commit / backup).</li>"
            "<li>✅ Tidak pernah ditampilkan di UI setelah disimpan.</li>"
            "</ul>"
            "<h3>Yang TIDAK dijamin</h3>"
            "<ul>"
            "<li>❌ Malware jalan sebagai user yang sama bisa akses (sama seperti <code>.env</code>).</li>"
            "<li>❌ Process memory dump saat app running.</li>"
            "</ul>"
        )
        text.setWordWrap(True)
        text.setTextFormat(Qt.RichText)
        text.setAlignment(Qt.AlignTop)
        layout.addWidget(text)
        layout.addStretch(1)
        return wrap

    # ---- Signal glue ----

    def _on_key_changed(self) -> None:
        """A _KeyRow saved or deleted — reload config + tell parent to refresh."""
        self.config.reload_keys()
        for row in self._key_rows.values():
            row.refresh()
        self.settings_changed.emit()


def _hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def _select_provider(combo: QComboBox, provider: Provider) -> None:
    for i in range(combo.count()):
        if combo.itemData(i) == provider:
            combo.setCurrentIndex(i)
            return


class OptionsPanel(QWidget):
    """Per-conversion knobs: image filter + OCR. Two group boxes side-by-side."""

    _IMG_MODE_LABELS = [
        ("Semua gambar dikirim ke AI",     "all"),
        ("Hanya diagram-like (heuristik)", "diagrams_only"),
        ("Skip semua gambar",              "none"),
    ]
    _OCR_MODE_LABELS = [
        ("Never — jangan OCR",                        "never"),
        ("Auto — hanya page yang tampak scan",        "auto"),
        ("Always — OCR setiap page (paksa)",          "always"),
    ]
    _PAGE_VISION_LABELS = [
        ("Auto — PDF slide deck, provider cloud",     "auto"),
        ("Selalu — setiap halaman (juga Ollama)",     "always"),
        ("Tidak pernah — ekstraksi teks biasa",       "never"),
    ]
    _SLIDE_VISION_LABELS = [
        ("Auto — provider cloud",                      "auto"),
        ("Selalu — setiap slide (juga Ollama)",       "always"),
        ("Tidak pernah — susunan dari XML saja",      "never"),
    ]
    _OCR_ENGINE_LABELS = [
        ("Tesseract (offline, ringan)",               "tesseract"),
        ("PaddleOCR (offline, akurat, heavy)",        "paddleocr"),
        ("AI Vision (pakai token)",                    "ai"),
        ("Hybrid — Tesseract dulu, AI fallback",      "hybrid"),
    ]

    def __init__(self, defaults: ConvertOptions):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        # --- Filter gambar ---
        img_grp = QGroupBox("Filter gambar")
        img_form = QFormLayout(img_grp)
        img_form.setLabelAlignment(Qt.AlignRight)
        self.mode_combo = QComboBox()
        for label, _val in self._IMG_MODE_LABELS:
            self.mode_combo.addItem(label)
        for i, (_lbl, val) in enumerate(self._IMG_MODE_LABELS):
            if val == defaults.image_mode:
                self.mode_combo.setCurrentIndex(i)
                break
        img_form.addRow("Mode:", self.mode_combo)

        self.dedup_check = QCheckBox("Dedup gambar identik (SHA-256)")
        self.dedup_check.setChecked(defaults.image_dedup)
        img_form.addRow("", self.dedup_check)

        self.min_dim_spin = QSpinBox()
        self.min_dim_spin.setRange(0, 4096)
        self.min_dim_spin.setSuffix(" px")
        self.min_dim_spin.setSpecialValueText("tanpa filter")
        self.min_dim_spin.setValue(defaults.image_min_dim)
        img_form.addRow("Skip gambar <:", self.min_dim_spin)

        self.min_bytes_spin = QSpinBox()
        self.min_bytes_spin.setRange(0, 10_000_000)
        self.min_bytes_spin.setSingleStep(1024)
        self.min_bytes_spin.setSuffix(" B")
        self.min_bytes_spin.setSpecialValueText("tanpa filter")
        self.min_bytes_spin.setValue(defaults.image_min_bytes)
        img_form.addRow("Skip bytes <:", self.min_bytes_spin)

        row.addWidget(img_grp, stretch=1)

        # --- OCR ---
        ocr_grp = QGroupBox("OCR untuk PDF scan")
        ocr_form = QFormLayout(ocr_grp)
        ocr_form.setLabelAlignment(Qt.AlignRight)

        self.ocr_mode_combo = QComboBox()
        for label, _val in self._OCR_MODE_LABELS:
            self.ocr_mode_combo.addItem(label)
        for i, (_lbl, val) in enumerate(self._OCR_MODE_LABELS):
            if val == defaults.ocr_mode:
                self.ocr_mode_combo.setCurrentIndex(i)
                break
        ocr_form.addRow("Mode:", self.ocr_mode_combo)

        self.ocr_engine_combo = QComboBox()
        for label, _val in self._OCR_ENGINE_LABELS:
            self.ocr_engine_combo.addItem(label)
        for i, (_lbl, val) in enumerate(self._OCR_ENGINE_LABELS):
            if val == defaults.ocr_engine:
                self.ocr_engine_combo.setCurrentIndex(i)
                break
        ocr_form.addRow("Engine:", self.ocr_engine_combo)

        self.ocr_lang_edit = QLineEdit(defaults.ocr_lang)
        self.ocr_lang_edit.setPlaceholderText("ind+eng")
        ocr_form.addRow("Bahasa:", self.ocr_lang_edit)

        row.addWidget(ocr_grp, stretch=1)

        # --- Slides (PDF slide decks, PPTX): AI reads each rendered page ---
        pv_grp = QGroupBox("Slide → AI per halaman")
        pv_form = QFormLayout(pv_grp)
        pv_form.setLabelAlignment(Qt.AlignRight)
        self.page_vision_combo = QComboBox()
        for label, _val in self._PAGE_VISION_LABELS:
            self.page_vision_combo.addItem(label)
        for i, (_lbl, val) in enumerate(self._PAGE_VISION_LABELS):
            if val == defaults.pdf_page_vision:
                self.page_vision_combo.setCurrentIndex(i)
                break
        self.page_vision_combo.setToolTip(
            "Diagram, tabel tanpa garis, dan layout kolom di slide yang di-export ke PDF\n"
            "dibaca AI dari gambar halaman, dengan teks asli PDF sebagai acuan.\n"
            "Satu panggilan AI per halaman."
        )
        pv_form.addRow("PDF:", self.page_vision_combo)
        self.slide_vision_combo = QComboBox()
        for label, _val in self._SLIDE_VISION_LABELS:
            self.slide_vision_combo.addItem(label)
        for i, (_lbl, val) in enumerate(self._SLIDE_VISION_LABELS):
            if val == defaults.pptx_slide_vision:
                self.slide_vision_combo.setCurrentIndex(i)
                break
        self.slide_vision_combo.setToolTip(
            "Setiap slide PPTX dirender (PowerPoint bila terpasang) dan disusun AI:\n"
            "judul, kolom, panel. Teks, tabel, gambar, dan diagram tetap diambil\n"
            "persis dari file. Satu panggilan AI per slide."
        )
        pv_form.addRow("PPTX:", self.slide_vision_combo)
        row.addWidget(pv_grp, stretch=1)

    def collect(self) -> ConvertOptions:
        img_idx = self.mode_combo.currentIndex()
        img_mode = self._IMG_MODE_LABELS[img_idx][1] if 0 <= img_idx < len(self._IMG_MODE_LABELS) else "all"

        ocr_idx = self.ocr_mode_combo.currentIndex()
        ocr_mode = self._OCR_MODE_LABELS[ocr_idx][1] if 0 <= ocr_idx < len(self._OCR_MODE_LABELS) else "never"

        eng_idx = self.ocr_engine_combo.currentIndex()
        ocr_engine = self._OCR_ENGINE_LABELS[eng_idx][1] if 0 <= eng_idx < len(self._OCR_ENGINE_LABELS) else "tesseract"

        ocr_lang = self.ocr_lang_edit.text().strip() or "ind+eng"

        pv_idx = self.page_vision_combo.currentIndex()
        page_vision = (self._PAGE_VISION_LABELS[pv_idx][1]
                       if 0 <= pv_idx < len(self._PAGE_VISION_LABELS) else "auto")
        sv_idx = self.slide_vision_combo.currentIndex()
        slide_vision = (self._SLIDE_VISION_LABELS[sv_idx][1]
                        if 0 <= sv_idx < len(self._SLIDE_VISION_LABELS) else "auto")

        return ConvertOptions(
            image_dedup=self.dedup_check.isChecked(),
            image_min_dim=int(self.min_dim_spin.value()),
            image_min_bytes=int(self.min_bytes_spin.value()),
            image_mode=img_mode,  # type: ignore[arg-type]
            ocr_mode=ocr_mode,  # type: ignore[arg-type]
            ocr_engine=ocr_engine,  # type: ignore[arg-type]
            ocr_lang=ocr_lang,
            pdf_page_vision=page_vision,  # type: ignore[arg-type]
            pptx_slide_vision=slide_vision,  # type: ignore[arg-type]
        )


class MainWindow(QMainWindow):
    def __init__(self, config: Config, default_options: ConvertOptions):
        super().__init__()
        self.setWindowTitle("AnyToMD")
        self.resize(1080, 780)
        self.config = config
        self.default_options = default_options
        self.worker_thread: QThread | None = None
        self.worker: ConvertWorker | None = None
        self._last_output_path: Path | None = None
        self._last_markdown: str = ""

        # --- Menu bar ---
        self._build_menu()

        # --- Toolbar ---
        self._build_toolbar()

        # --- Status bar ---
        self.status_msg = QLabel("Siap.")
        self.cost_label = QLabel("")
        self.cost_label.setStyleSheet("color: #4a5566;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(140)
        self.progress.setVisible(False)
        sb = self.statusBar()
        sb.addWidget(self.status_msg, stretch=1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.cost_label)

        # --- Central: vertical splitter ---
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.addWidget(self._build_workspace())
        splitter.addWidget(self._build_preview_area())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 500])
        self.setCentralWidget(splitter)

        self._refresh_status()

    # ---- UI construction ----

    def _build_menu(self) -> None:
        mb = self.menuBar()

        m_file = mb.addMenu("&File")
        act_open = QAction("&Open…", self)
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self.on_browse)
        m_file.addAction(act_open)

        act_save = QAction("&Simpan .md…", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(self.on_save_as)
        m_file.addAction(act_save)
        self.act_save = act_save
        act_save.setEnabled(False)

        m_file.addSeparator()
        act_exit = QAction("&Exit", self)
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)
        m_file.addAction(act_exit)

        m_set = mb.addMenu("&Settings")
        act_prefs = QAction("&Preferences…", self)
        act_prefs.setShortcut("Ctrl+,")
        act_prefs.triggered.connect(self.on_settings)
        m_set.addAction(act_prefs)

        m_help = mb.addMenu("&Help")
        act_about = QAction("&About AnyToMD", self)
        act_about.triggered.connect(self._on_about)
        m_help.addAction(act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.TopToolBarArea, tb)

        act_open = QAction("📂  Open", self)
        act_open.setToolTip("Buka file untuk dikonversi (Ctrl+O)")
        act_open.triggered.connect(self.on_browse)
        tb.addAction(act_open)

        self.act_save_tb = QAction("💾  Save", self)
        self.act_save_tb.setToolTip("Simpan output ke lokasi lain (Ctrl+S)")
        self.act_save_tb.setEnabled(False)
        self.act_save_tb.triggered.connect(self.on_save_as)
        tb.addAction(self.act_save_tb)

        tb.addSeparator()

        # Provider dropdown (compact)
        tb.addWidget(QLabel("  Provider: "))
        self.toolbar_provider_combo = QComboBox()
        for prov in ALL_PROVIDERS:
            self.toolbar_provider_combo.addItem(PROVIDER_LABELS[prov], userData=prov)
        _select_provider(self.toolbar_provider_combo, self.config.provider)
        self.toolbar_provider_combo.currentIndexChanged.connect(self._on_toolbar_provider_changed)
        self.toolbar_provider_combo.setMinimumWidth(220)
        tb.addWidget(self.toolbar_provider_combo)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)

        act_settings = QAction("⚙  Settings", self)
        act_settings.setToolTip("API keys, hybrid routing, model IDs, budget cap")
        act_settings.triggered.connect(self.on_settings)
        tb.addAction(act_settings)

    def _build_workspace(self) -> QWidget:
        """Top half of the splitter: drop zone (hero) + options row."""
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(16, 12, 16, 8)
        outer.setSpacing(10)

        self.drop = DropZone()
        self.drop.file_dropped.connect(self.start_conversion)
        self.drop.browse_requested.connect(self.on_browse)
        outer.addWidget(self.drop)

        # Options row: two group boxes side-by-side
        self.options_panel = OptionsPanel(self.default_options)
        outer.addWidget(self.options_panel)
        return wrap

    def _build_preview_area(self) -> QWidget:
        """Bottom half of the splitter: preview textarea + copy button."""
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(16, 4, 16, 12)
        outer.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("<b>Preview</b>")
        header.addWidget(title)
        header.addStretch()
        self.copy_btn = QPushButton("📋  Copy semua")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._on_copy)
        header.addWidget(self.copy_btn)
        outer.addLayout(header)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText(
            "Preview markdown akan muncul di sini setelah konversi."
        )
        outer.addWidget(self.preview, stretch=1)
        return wrap

    # ---- Status ----

    def _refresh_status(self) -> None:
        provider_line = f"{PROVIDER_LABELS.get(self.config.provider, self.config.provider)} · {self.config.display_model}"
        if self.config.is_ready:
            self.status_msg.setText(f"Siap.  {provider_line}")
            self.status_msg.setStyleSheet("")
        else:
            self.status_msg.setText(f"⚠  {self.config.not_ready_reason}  ·  {provider_line}")
            self.status_msg.setStyleSheet("color: #b06000;")
        # Sync toolbar dropdown selection if config was mutated elsewhere
        if self.toolbar_provider_combo.currentData() != self.config.provider:
            self.toolbar_provider_combo.blockSignals(True)
            _select_provider(self.toolbar_provider_combo, self.config.provider)
            self.toolbar_provider_combo.blockSignals(False)

    def _on_toolbar_provider_changed(self, _idx: int) -> None:
        self.config.provider = self.toolbar_provider_combo.currentData() or "anthropic"
        self._refresh_status()

    def _on_about(self) -> None:
        QMessageBox.about(
            self, "AnyToMD",
            "<h3>AnyToMD</h3>"
            "<p>Convert mixed-format documents into clean Markdown while "
            "spending as little AI as possible.</p>"
            "<p>Supports PDF, PPTX, DOCX, XLSX, HTML, and standalone images. "
            "AI backends: Anthropic Claude, OpenAI, Google Gemini, Moonshot Kimi, "
            "or offline via Ollama.</p>"
        )

    def _on_copy(self) -> None:
        if self._last_markdown:
            QGuiApplication.clipboard().setText(self._last_markdown)
            self.status_msg.setText(f"Markdown disalin ke clipboard.  ({len(self._last_markdown)} char)")

    # ---- UI actions ----

    def on_browse(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Pilih file",
            "",
            "Supported (*.pdf *.pptx *.docx *.xlsx *.html *.htm *.png *.jpg *.jpeg *.webp *.gif *.bmp)",
        )
        if path_str:
            self.start_conversion(Path(path_str))

    def on_settings(self) -> None:
        dlg = SettingsDialog(self, self.config)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.exec()
        self._on_settings_changed()

    def _on_settings_changed(self) -> None:
        self.config.reload_keys()
        self._refresh_status()

    def on_save_as(self) -> None:
        if not self._last_markdown:
            return
        default = str(self._last_output_path) if self._last_output_path else ""
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Simpan sebagai", default, "Markdown (*.md)"
        )
        if path_str:
            Path(path_str).write_text(self._last_markdown, encoding="utf-8")
            self.status_msg.setText(f"Disimpan: {path_str}")

    def start_conversion(self, path: Path) -> None:
        if not self.config.is_ready:
            QMessageBox.warning(
                self,
                "Konfigurasi belum siap",
                self.config.not_ready_reason
                + "\n\nBuka Settings (Ctrl+,) untuk mengatur API key.",
            )
            return
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, "Sedang berjalan", "Tunggu proses selesai dulu.")
            return

        output = path.with_suffix(".md")
        try:
            ai = create_client(self.config)
        except Exception as e:
            QMessageBox.critical(self, "Gagal init AI", str(e))
            return

        options = self.options_panel.collect()

        self.status_msg.setText(f"Memproses: {path.name}")
        self.progress.setVisible(True)
        self.act_save.setEnabled(False)
        self.act_save_tb.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.preview.clear()

        self.worker_thread = QThread(self)
        self.worker = ConvertWorker(path, output, ai, options)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._teardown_worker)
        self.worker_thread.start()

    # ---- Worker callbacks ----

    def on_progress(self, msg: str) -> None:
        self.status_msg.setText(msg)

    def on_finished(self, output_path: Path, markdown: str, stats: str) -> None:
        self._last_output_path = output_path
        self._last_markdown = markdown
        self.preview.setPlainText(markdown)
        self.status_msg.setText(f"Selesai · {output_path.name} · {stats}")
        # Split cost hint (if present) into the dedicated cost label
        if "$" in stats:
            for token in stats.split(","):
                if "$" in token:
                    self.cost_label.setText(token.strip())
                    break
        self.progress.setVisible(False)
        self.act_save.setEnabled(True)
        self.act_save_tb.setEnabled(True)
        self.copy_btn.setEnabled(True)

    def on_failed(self, trace: str) -> None:
        self.progress.setVisible(False)
        self.status_msg.setText("Gagal. Lihat detail di preview.")
        self.status_msg.setStyleSheet("color: #c53030;")
        self.preview.setPlainText(trace)
        QMessageBox.critical(self, "Konversi gagal", trace.splitlines()[-1])

    def _teardown_worker(self) -> None:
        if self.worker:
            self.worker.deleteLater()
        if self.worker_thread:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AnyToMD")
    app.setOrganizationName("AnyToMD")
    app.setStyleSheet(_APP_QSS)
    config = Config.from_env()
    default_options = ConvertOptions.from_env()
    win = MainWindow(config, default_options)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
