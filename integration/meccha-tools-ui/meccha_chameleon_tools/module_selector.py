import sys

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QCheckBox,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QTabWidget,
    QLineEdit,
    QTextEdit,
)

from meccha_chameleon_tools.module_registry import MODULES

from meccha_chameleon_tools.module_adapters import (
    get_module_display_status,
)

from meccha_chameleon_tools.module_details import (
    get_module_details,
)

from meccha_chameleon_tools.module_runtime import (
    ModuleRuntimeManager,
    build_runtime_summary,
)

from meccha_chameleon_tools.module_workspace import (
    ModuleWorkspace,
)


# ============================================================
# Module row
# ============================================================

class ModuleRow(QFrame):

    clicked = pyqtSignal(str)

    def __init__(
        self,
        module,
        on_selection_changed=None
    ):
        super().__init__()

        self.module = module
        self.module_id = module["id"]

        self.on_selection_changed = (
            on_selection_changed
        )

        self.base_status = (
            get_module_display_status(
                self.module_id
            )
        )

        self.ready = (
            self.base_status == "READY"
        )

        self.setObjectName(
            "moduleRow"
        )

        self.setProperty(
            "active",
            False
        )

        self.setProperty(
            "selected",
            False
        )

        self.setFixedHeight(58)

        layout = QHBoxLayout(self)

        layout.setContentsMargins(
            13,
            7,
            11,
            7
        )

        layout.setSpacing(9)

        # ----------------------------------------------------
        # Checkbox
        # ----------------------------------------------------

        self.checkbox = QCheckBox()

        self.checkbox.setObjectName(
            "moduleCheck"
        )

        if not self.ready:
            self.checkbox.setEnabled(
                False
            )

        self.checkbox.toggled.connect(
            self._selection_changed
        )

        # ----------------------------------------------------
        # Name / Description
        # ----------------------------------------------------

        text_layout = QVBoxLayout()

        text_layout.setSpacing(1)

        name = QLabel(
            module["name"]
        )

        name.setObjectName(
            "moduleName"
        )

        description = QLabel(
            module["description"]
        )

        description.setObjectName(
            "moduleSmallDescription"
        )

        text_layout.addWidget(
            name
        )

        text_layout.addWidget(
            description
        )

        # ----------------------------------------------------
        # Status
        # ----------------------------------------------------

        self.status_label = QLabel()

        self.update_status(
            self.base_status
        )

        layout.addWidget(
            self.checkbox
        )

        layout.addLayout(
            text_layout,
            1
        )

        layout.addWidget(
            self.status_label
        )

    # ========================================================
    # Status
    # ========================================================

    def update_status(
        self,
        status
    ):

        self.status_label.setText(
            status
        )

        if status in (
            "READY",
            "PREPARED",
        ):

            self.status_label.setObjectName(
                "statusReady"
            )

        elif status == "IDLE":

            self.status_label.setObjectName(
                "statusIdle"
            )

        else:

            self.status_label.setObjectName(
                "statusPending"
            )

        self.status_label.style().unpolish(
            self.status_label
        )

        self.status_label.style().polish(
            self.status_label
        )

        self.status_label.update()

    # ========================================================
    # Selection
    # ========================================================

    def _selection_changed(
        self,
        checked
    ):

        self.setProperty(
            "selected",
            checked
        )

        self.style().unpolish(
            self
        )

        self.style().polish(
            self
        )

        self.update()

        if self.on_selection_changed:
            self.on_selection_changed()

    def is_selected(self):

        return (
            self.ready
            and self.checkbox.isChecked()
        )

    # ========================================================
    # Detail selection
    # ========================================================

    def set_active(
        self,
        active
    ):

        self.setProperty(
            "active",
            active
        )

        self.style().unpolish(
            self
        )

        self.style().polish(
            self
        )

        self.update()

    def mousePressEvent(
        self,
        event
    ):

        if event.button() == Qt.LeftButton:

            self.clicked.emit(
                self.module_id
            )

        super().mousePressEvent(
            event
        )


# ============================================================
# Detail panel
# ============================================================

class ModuleDetailPanel(QFrame):

    def __init__(
        self,
        workspace
    ):
        super().__init__()

        self.workspace = workspace

        self.current_module_id = None

        self.loading_settings = False

        self.setObjectName(
            "detailPanel"
        )

        main_layout = QVBoxLayout(self)

        main_layout.setContentsMargins(
            20,
            18,
            20,
            18
        )

        main_layout.setSpacing(9)

        # ====================================================
        # Header
        # ====================================================

        header = QHBoxLayout()

        self.title = QLabel(
            "Select a module"
        )

        self.title.setObjectName(
            "detailTitle"
        )

        self.runtime_status = QLabel(
            "-"
        )

        self.runtime_status.setObjectName(
            "detailStatusReady"
        )

        header.addWidget(
            self.title
        )

        header.addStretch()

        header.addWidget(
            self.runtime_status
        )

        main_layout.addLayout(
            header
        )

        self.description = QLabel(
            "Click a module on the left."
        )

        self.description.setObjectName(
            "detailDescription"
        )

        self.description.setWordWrap(
            True
        )

        main_layout.addWidget(
            self.description
        )

        # ====================================================
        # Tabs
        # ====================================================

        self.tabs = QTabWidget()

        self.tabs.setObjectName(
            "detailTabs"
        )

        self.overview_tab = QWidget()
        self.settings_tab = QWidget()
        self.logs_tab = QWidget()

        self.tabs.addTab(
            self.overview_tab,
            "Overview"
        )

        self.tabs.addTab(
            self.settings_tab,
            "Settings"
        )

        self.tabs.addTab(
            self.logs_tab,
            "Logs"
        )

        main_layout.addWidget(
            self.tabs,
            1
        )

        self._build_overview_tab()
        self._build_settings_tab()
        self._build_logs_tab()

    # ========================================================
    # Overview tab
    # ========================================================

    def _build_overview_tab(self):

        layout = QVBoxLayout(
            self.overview_tab
        )

        layout.setContentsMargins(
            12,
            15,
            12,
            12
        )

        layout.setSpacing(9)

        info_title = QLabel(
            "MODULE INFORMATION"
        )

        info_title.setObjectName(
            "detailSection"
        )

        layout.addWidget(
            info_title
        )

        self.type_label = QLabel(
            "Type        -"
        )

        self.language_label = QLabel(
            "Language    -"
        )

        self.path_label = QLabel(
            "Path        -"
        )

        for label in (
            self.type_label,
            self.language_label,
            self.path_label,
        ):

            label.setObjectName(
                "detailValue"
            )

            label.setWordWrap(
                True
            )

            layout.addWidget(
                label
            )

        implementation_title = QLabel(
            "IMPLEMENTATION"
        )

        implementation_title.setObjectName(
            "detailSection"
        )

        layout.addWidget(
            implementation_title
        )

        self.implementation_label = QLabel(
            "-"
        )

        self.implementation_label.setObjectName(
            "implementationText"
        )

        self.implementation_label.setWordWrap(
            True
        )

        layout.addWidget(
            self.implementation_label
        )

        files_title = QLabel(
            "FILE CHECK"
        )

        files_title.setObjectName(
            "detailSection"
        )

        layout.addWidget(
            files_title
        )

        self.files_label = QLabel(
            "-"
        )

        self.files_label.setObjectName(
            "detailValue"
        )

        layout.addWidget(
            self.files_label
        )

        layout.addStretch()

    # ========================================================
    # Settings tab
    # ========================================================

    def _build_settings_tab(self):

        layout = QVBoxLayout(
            self.settings_tab
        )

        layout.setContentsMargins(
            12,
            15,
            12,
            12
        )

        layout.setSpacing(10)

        section = QLabel(
            "MODULE SETTINGS"
        )

        section.setObjectName(
            "detailSection"
        )

        layout.addWidget(
            section
        )

        # ----------------------------------------------------
        # Enabled
        # ----------------------------------------------------

        self.setting_enabled = QCheckBox(
            "Enabled"
        )

        self.setting_enabled.setObjectName(
            "settingsCheck"
        )

        layout.addWidget(
            self.setting_enabled
        )

        # ----------------------------------------------------
        # Profile
        # ----------------------------------------------------

        profile_label = QLabel(
            "Profile"
        )

        profile_label.setObjectName(
            "settingsLabel"
        )

        self.setting_profile = QLineEdit()

        self.setting_profile.setPlaceholderText(
            "default"
        )

        layout.addWidget(
            profile_label
        )

        layout.addWidget(
            self.setting_profile
        )

        # ----------------------------------------------------
        # Notes
        # ----------------------------------------------------

        notes_label = QLabel(
            "Notes"
        )

        notes_label.setObjectName(
            "settingsLabel"
        )

        self.setting_notes = QTextEdit()

        self.setting_notes.setPlaceholderText(
            "Module notes..."
        )

        self.setting_notes.setFixedHeight(
            110
        )

        layout.addWidget(
            notes_label
        )

        layout.addWidget(
            self.setting_notes
        )

        # ----------------------------------------------------
        # Buttons
        # ----------------------------------------------------

        buttons = QHBoxLayout()

        reset_button = QPushButton(
            "Reset Settings"
        )

        reset_button.clicked.connect(
            self.reset_settings
        )

        save_button = QPushButton(
            "Save Settings"
        )

        save_button.setObjectName(
            "primaryButton"
        )

        save_button.clicked.connect(
            self.save_settings
        )

        buttons.addStretch()

        buttons.addWidget(
            reset_button
        )

        buttons.addWidget(
            save_button
        )

        layout.addLayout(
            buttons
        )

        self.settings_message = QLabel(
            ""
        )

        self.settings_message.setObjectName(
            "settingsMessage"
        )

        layout.addWidget(
            self.settings_message
        )

        layout.addStretch()

    # ========================================================
    # Logs tab
    # ========================================================

    def _build_logs_tab(self):

        layout = QVBoxLayout(
            self.logs_tab
        )

        layout.setContentsMargins(
            12,
            15,
            12,
            12
        )

        layout.setSpacing(9)

        header = QHBoxLayout()

        title = QLabel(
            "MODULE LOGS"
        )

        title.setObjectName(
            "detailSection"
        )

        clear_button = QPushButton(
            "Clear Logs"
        )

        clear_button.clicked.connect(
            self.clear_logs
        )

        header.addWidget(
            title
        )

        header.addStretch()

        header.addWidget(
            clear_button
        )

        layout.addLayout(
            header
        )

        self.log_view = QTextEdit()

        self.log_view.setReadOnly(
            True
        )

        self.log_view.setObjectName(
            "logView"
        )

        layout.addWidget(
            self.log_view,
            1
        )

    # ========================================================
    # Show module
    # ========================================================

    def show_module(
        self,
        module_id,
        runtime_status=None
    ):

        self.current_module_id = (
            module_id
        )

        details = get_module_details(
            module_id
        )

        self.title.setText(
            details["name"]
        )

        self.description.setText(
            details["description"]
            or "No description."
        )

        if runtime_status is None:

            runtime_status = (
                details["status"]
            )

        self.set_runtime_status(
            runtime_status
        )

        # ----------------------------------------------------
        # Overview
        # ----------------------------------------------------

        self.type_label.setText(
            f"Type        {details['type']}"
        )

        self.language_label.setText(
            f"Language    {details['language']}"
        )

        self.path_label.setText(
            "Path        "
            + str(
                details["path"]
                or "-"
            )
        )

        primary_files = (
            details["primary_files"]
        )

        if primary_files:

            lines = []

            for item in primary_files:

                marker = (
                    "✓"
                    if item["exists"]
                    else "✕"
                )

                lines.append(
                    f"{marker} "
                    f"{item['name']}"
                )

            self.implementation_label.setText(
                "\n".join(lines)
            )

        else:

            self.implementation_label.setText(
                "No implementation files detected."
            )

        self.files_label.setText(
            f"Found: "
            f"{details['existing_file_count']}    "
            f"Missing: "
            f"{details['missing_file_count']}"
        )

        # ----------------------------------------------------
        # Settings
        # ----------------------------------------------------

        self.load_settings()

        # ----------------------------------------------------
        # Logs
        # ----------------------------------------------------

        self.refresh_logs()

    # ========================================================
    # Runtime status
    # ========================================================

    def set_runtime_status(
        self,
        status
    ):

        self.runtime_status.setText(
            status
        )

        if status in (
            "READY",
            "PREPARED",
        ):

            self.runtime_status.setObjectName(
                "detailStatusReady"
            )

        elif status == "IDLE":

            self.runtime_status.setObjectName(
                "detailStatusIdle"
            )

        else:

            self.runtime_status.setObjectName(
                "detailStatusPending"
            )

        self.runtime_status.style().unpolish(
            self.runtime_status
        )

        self.runtime_status.style().polish(
            self.runtime_status
        )

        self.runtime_status.update()

    # ========================================================
    # Settings
    # ========================================================

    def load_settings(self):

        if self.current_module_id is None:
            return

        settings = (
            self.workspace.get_settings(
                self.current_module_id
            )
        )

        if settings is None:
            return

        self.loading_settings = True

        self.setting_enabled.setChecked(
            bool(
                settings.get(
                    "enabled",
                    False
                )
            )
        )

        self.setting_profile.setText(
            str(
                settings.get(
                    "profile",
                    "default"
                )
            )
        )

        self.setting_notes.setPlainText(
            str(
                settings.get(
                    "notes",
                    ""
                )
            )
        )

        self.loading_settings = False

        self.settings_message.setText(
            ""
        )

    def save_settings(self):

        if self.current_module_id is None:
            return

        module_id = (
            self.current_module_id
        )

        self.workspace.set_setting(
            module_id,
            "enabled",
            self.setting_enabled.isChecked()
        )

        self.workspace.set_setting(
            module_id,
            "profile",
            self.setting_profile.text().strip()
            or "default"
        )

        self.workspace.set_setting(
            module_id,
            "notes",
            self.setting_notes.toPlainText()
        )

        self.workspace.add_log(
            module_id,
            "Settings saved."
        )

        self.settings_message.setText(
            "Settings saved."
        )

        self.refresh_logs()

    def reset_settings(self):

        if self.current_module_id is None:
            return

        self.workspace.reset_settings(
            self.current_module_id
        )

        self.load_settings()

        self.settings_message.setText(
            "Settings reset."
        )

        self.refresh_logs()

    # ========================================================
    # Logs
    # ========================================================

    def refresh_logs(self):

        if self.current_module_id is None:

            self.log_view.setPlainText(
                "No module selected."
            )

            return

        log_text = (
            self.workspace.get_log_text(
                self.current_module_id
            )
        )

        self.log_view.setPlainText(
            log_text
        )

        scrollbar = (
            self.log_view.verticalScrollBar()
        )

        scrollbar.setValue(
            scrollbar.maximum()
        )

    def clear_logs(self):

        if self.current_module_id is None:
            return

        self.workspace.clear_logs(
            self.current_module_id
        )

        self.refresh_logs()


# ============================================================
# Main window
# ============================================================

class ModuleSelector(QWidget):

    STYLE = """
        QWidget {
            background-color: #0f1117;
            color: #d7dbe6;
            font-family: "Segoe UI";
        }

        QFrame#mainFrame {
            background-color: #171a23;
            border: 1px solid #2b3140;
            border-radius: 14px;
        }

        QLabel#title {
            color: #aeb9ff;
            font-size: 23px;
            font-weight: bold;
            background: transparent;
        }

        QLabel#subtitle {
            color: #8b93a7;
            font-size: 11px;
            background: transparent;
        }

        QLabel#moduleCount {
            color: #5b8cff;
            font-size: 11px;
            font-weight: bold;
            background: transparent;
        }

        QLabel#sectionTitle {
            color: #d7dbe6;
            font-size: 12px;
            font-weight: bold;
            background: transparent;
        }

        QFrame#moduleRow {
            background-color: #1d212c;
            border: 1px solid #2b3140;
            border-radius: 8px;
        }

        QFrame#moduleRow:hover {
            background-color: #212633;
            border-color: #46516a;
        }

        QFrame#moduleRow[active="true"] {
            background-color: #20283a;
            border-color: #5b8cff;
        }

        QFrame#moduleRow[selected="true"] {
            background-color: #202638;
        }

        QLabel#moduleName {
            color: #e1e5ef;
            font-size: 12px;
            font-weight: bold;
            background: transparent;
        }

        QLabel#moduleSmallDescription {
            color: #6f788e;
            font-size: 8px;
            background: transparent;
        }

        QCheckBox#moduleCheck {
            background: transparent;
        }

        QCheckBox::indicator {
            width: 15px;
            height: 15px;
            border-radius: 4px;
            border: 1px solid #3a4253;
            background: #14171f;
        }

        QCheckBox::indicator:hover {
            border-color: #5b8cff;
        }

        QCheckBox::indicator:checked {
            background-color: #5b8cff;
            border: 1px solid #7d9bff;
        }

        QLabel#statusReady {
            color: #63d9a4;
            background-color: #173328;
            border: 1px solid #285943;
            border-radius: 5px;
            padding: 2px 6px;
            font-size: 7px;
            font-weight: bold;
        }

        QLabel#statusPending {
            color: #ffbd62;
            background-color: #372b18;
            border: 1px solid #66502b;
            border-radius: 5px;
            padding: 2px 6px;
            font-size: 7px;
            font-weight: bold;
        }

        QLabel#statusIdle {
            color: #8b93a7;
            background-color: #20242e;
            border: 1px solid #343a49;
            border-radius: 5px;
            padding: 2px 6px;
            font-size: 7px;
            font-weight: bold;
        }

        QFrame#detailPanel {
            background-color: #151821;
            border: 1px solid #2b3140;
            border-radius: 10px;
        }

        QLabel#detailTitle {
            color: #e4e7ef;
            font-size: 20px;
            font-weight: bold;
            background: transparent;
        }

        QLabel#detailDescription {
            color: #8b93a7;
            font-size: 10px;
            background: transparent;
        }

        QLabel#detailStatusReady {
            color: #63d9a4;
            background-color: #173328;
            border: 1px solid #285943;
            border-radius: 5px;
            padding: 4px 9px;
            font-size: 8px;
            font-weight: bold;
        }

        QLabel#detailStatusPending {
            color: #ffbd62;
            background-color: #372b18;
            border: 1px solid #66502b;
            border-radius: 5px;
            padding: 4px 9px;
            font-size: 8px;
            font-weight: bold;
        }

        QLabel#detailStatusIdle {
            color: #8b93a7;
            background-color: #20242e;
            border: 1px solid #343a49;
            border-radius: 5px;
            padding: 4px 9px;
            font-size: 8px;
            font-weight: bold;
        }

        QLabel#detailSection {
            color: #aeb9ff;
            font-size: 10px;
            font-weight: bold;
            background: transparent;
        }

        QLabel#detailValue {
            color: #b9bfce;
            font-size: 10px;
            background: transparent;
        }

        QLabel#implementationText {
            color: #9fdabf;
            font-size: 10px;
            background: transparent;
        }

        QTabWidget::pane {
            border: 1px solid #2b3140;
            border-radius: 7px;
            background-color: #13161e;
            top: -1px;
        }

        QTabBar::tab {
            background-color: #1b1f29;
            color: #8b93a7;
            border: 1px solid #2b3140;
            padding: 8px 18px;
            min-width: 75px;
        }

        QTabBar::tab:selected {
            background-color: #252b3b;
            color: #dce2ff;
            border-bottom: 2px solid #5b8cff;
        }

        QTabBar::tab:hover {
            color: #d7dbe6;
        }

        QLabel#settingsLabel {
            color: #9ba3b8;
            font-size: 10px;
            background: transparent;
        }

        QCheckBox#settingsCheck {
            color: #c9cedb;
            font-size: 10px;
            background: transparent;
        }

        QLineEdit,
        QTextEdit {
            background-color: #0f1219;
            color: #d7dbe6;
            border: 1px solid #303747;
            border-radius: 6px;
            padding: 7px;
            selection-background-color: #5b8cff;
        }

        QLineEdit:focus,
        QTextEdit:focus {
            border-color: #5b8cff;
        }

        QTextEdit#logView {
            font-family: Consolas;
            font-size: 9px;
            background-color: #0c0f15;
        }

        QLabel#settingsMessage {
            color: #63d9a4;
            font-size: 9px;
            background: transparent;
        }

        QPushButton {
            background-color: #222737;
            color: #d7dbe6;
            border: 1px solid #2b3140;
            padding: 8px 13px;
            border-radius: 7px;
            font-size: 10px;
        }

        QPushButton:hover {
            background-color: #2c3349;
            border-color: #4a5470;
        }

        QPushButton#primaryButton,
        QPushButton#prepareButton {
            background-color: #3f6fe0;
            color: white;
            border: 1px solid #6d9bff;
            font-weight: bold;
        }

        QPushButton#primaryButton:hover,
        QPushButton#prepareButton:hover {
            background-color: #5b8cff;
        }

        QLabel#statusLabel {
            color: #8b93a7;
            font-size: 9px;
            background: transparent;
        }

        QLabel#selectedCount {
            color: #aeb9ff;
            font-size: 10px;
            font-weight: bold;
            background: transparent;
        }
    """

    def __init__(self):
        super().__init__()

        self.rows = {}

        self.active_module_id = None

        self.runtime_manager = (
            ModuleRuntimeManager()
        )

        self.workspace = (
            ModuleWorkspace()
        )

        self.setWindowTitle(
            "Meccha Chameleon Tools"
        )

        self.resize(
            1100,
            720
        )

        self.setMinimumSize(
            980,
            670
        )

        self.setStyleSheet(
            self.STYLE
        )

        root = QVBoxLayout(self)

        root.setContentsMargins(
            14,
            14,
            14,
            14
        )

        main_frame = QFrame()

        main_frame.setObjectName(
            "mainFrame"
        )

        root.addWidget(
            main_frame
        )

        main_layout = QVBoxLayout(
            main_frame
        )

        main_layout.setContentsMargins(
            24,
            20,
            24,
            18
        )

        main_layout.setSpacing(
            10
        )

        # ====================================================
        # Header
        # ====================================================

        title = QLabel(
            "MECCHA CHAMELEON TOOLS"
        )

        title.setObjectName(
            "title"
        )

        subtitle = QLabel(
            "Module manager / Target Game Version: 4.0.2"
        )

        subtitle.setObjectName(
            "subtitle"
        )

        ready_count = sum(
            1
            for module in MODULES
            if (
                get_module_display_status(
                    module["id"]
                )
                == "READY"
            )
        )

        module_count = QLabel(
            f"{ready_count} / "
            f"{len(MODULES)} Modules Ready"
        )

        module_count.setObjectName(
            "moduleCount"
        )

        header = QHBoxLayout()

        header_text = QVBoxLayout()

        header_text.addWidget(
            title
        )

        header_text.addWidget(
            subtitle
        )

        header.addLayout(
            header_text
        )

        header.addStretch()

        header.addWidget(
            module_count
        )

        main_layout.addLayout(
            header
        )

        # ====================================================
        # Main content
        # ====================================================

        content = QHBoxLayout()

        content.setSpacing(
            14
        )

        # ----------------------------------------------------
        # Left
        # ----------------------------------------------------

        left = QVBoxLayout()

        left.setSpacing(
            7
        )

        left_title = QLabel(
            "MODULES"
        )

        left_title.setObjectName(
            "sectionTitle"
        )

        left.addWidget(
            left_title
        )

        for module in MODULES:

            row = ModuleRow(
                module,
                self.update_selection_status
            )

            row.clicked.connect(
                self.show_module_details
            )

            self.rows[
                module["id"]
            ] = row

            left.addWidget(
                row
            )

        left.addStretch()

        left_container = QFrame()

        left_container.setLayout(
            left
        )

        left_container.setFixedWidth(
            385
        )

        # ----------------------------------------------------
        # Right
        # ----------------------------------------------------

        self.detail_panel = (
            ModuleDetailPanel(
                self.workspace
            )
        )

        content.addWidget(
            left_container
        )

        content.addWidget(
            self.detail_panel,
            1
        )

        main_layout.addLayout(
            content,
            1
        )

        # ====================================================
        # Footer
        # ====================================================

        footer = QHBoxLayout()

        footer.setSpacing(
            8
        )

        self.selected_count = QLabel()

        self.selected_count.setObjectName(
            "selectedCount"
        )

        self.status_label = QLabel(
            "Select one or more modules."
        )

        self.status_label.setObjectName(
            "statusLabel"
        )

        select_all_button = QPushButton(
            "Select All"
        )

        select_all_button.clicked.connect(
            self.select_all
        )

        clear_button = QPushButton(
            "Clear"
        )

        clear_button.clicked.connect(
            self.clear_selection
        )

        reset_button = QPushButton(
            "Reset"
        )

        reset_button.clicked.connect(
            self.reset_selected
        )

        prepare_button = QPushButton(
            "Prepare Selected"
        )

        prepare_button.setObjectName(
            "prepareButton"
        )

        prepare_button.clicked.connect(
            self.prepare_selected
        )

        footer.addWidget(
            self.selected_count
        )

        footer.addWidget(
            self.status_label
        )

        footer.addStretch()

        footer.addWidget(
            select_all_button
        )

        footer.addWidget(
            clear_button
        )

        footer.addWidget(
            reset_button
        )

        footer.addWidget(
            prepare_button
        )

        main_layout.addLayout(
            footer
        )

        self.update_selection_status()

        # ====================================================
        # Initialize workspace logs
        # ====================================================

        for module in MODULES:

            self.workspace.add_log(
                module["id"],
                "Module workspace initialized."
            )

        # 첫 번째 모듈 자동 선택
        if MODULES:

            self.show_module_details(
                MODULES[0]["id"]
            )

    # ========================================================
    # Selected modules
    # ========================================================

    def get_selected_modules(self):

        return [
            module_id
            for module_id, row
            in self.rows.items()
            if row.is_selected()
        ]

    # ========================================================
    # Details
    # ========================================================

    def show_module_details(
        self,
        module_id
    ):

        self.active_module_id = (
            module_id
        )

        for current_id, row in (
            self.rows.items()
        ):

            row.set_active(
                current_id == module_id
            )

        row = self.rows.get(
            module_id
        )

        runtime_status = None

        if row is not None:

            runtime_status = (
                row.status_label.text()
            )

        self.detail_panel.show_module(
            module_id,
            runtime_status
        )

    # ========================================================
    # Selection status
    # ========================================================

    def update_selection_status(self):

        selected = (
            self.get_selected_modules()
        )

        ready_count = sum(
            1
            for row in self.rows.values()
            if row.ready
        )

        self.selected_count.setText(
            f"Selected "
            f"{len(selected)} / "
            f"{ready_count}"
        )

        if selected:

            self.status_label.setText(
                "Ready to prepare."
            )

        else:

            self.status_label.setText(
                "Select one or more modules."
            )

    # ========================================================
    # Select all
    # ========================================================

    def select_all(self):

        for row in self.rows.values():

            if row.ready:

                row.checkbox.setChecked(
                    True
                )

        self.update_selection_status()

    # ========================================================
    # Clear
    # ========================================================

    def clear_selection(self):

        for row in self.rows.values():

            row.checkbox.setChecked(
                False
            )

        self.update_selection_status()

    # ========================================================
    # Prepare
    # ========================================================

    def prepare_selected(self):

        selected_ids = (
            self.get_selected_modules()
        )

        if not selected_ids:

            self.status_label.setText(
                "No modules selected."
            )

            return

        results = (
            self.runtime_manager
            .prepare_selected(
                selected_ids
            )
        )

        for result in results:

            row = self.rows.get(
                result.module_id
            )

            if row:

                row.update_status(
                    result.status
                )

            self.workspace.add_log(
                result.module_id,
                (
                    f"Prepare result: "
                    f"{result.status} - "
                    f"{result.message}"
                )
            )

        self.status_label.setText(
            build_runtime_summary(
                results
            )
        )

        if (
            self.active_module_id
            in selected_ids
        ):

            row = self.rows.get(
                self.active_module_id
            )

            self.detail_panel.set_runtime_status(
                row.status_label.text()
            )

            self.detail_panel.refresh_logs()

    # ========================================================
    # Reset
    # ========================================================

    def reset_selected(self):

        selected_ids = (
            self.get_selected_modules()
        )

        if not selected_ids:

            self.status_label.setText(
                "No modules selected."
            )

            return

        results = (
            self.runtime_manager
            .stop_selected(
                selected_ids
            )
        )

        for result in results:

            row = self.rows.get(
                result.module_id
            )

            if row:

                row.update_status(
                    "IDLE"
                )

            self.workspace.add_log(
                result.module_id,
                "Runtime reset."
            )

        self.status_label.setText(
            "Selected modules reset."
        )

        if (
            self.active_module_id
            in selected_ids
        ):

            self.detail_panel.set_runtime_status(
                "IDLE"
            )

            self.detail_panel.refresh_logs()


# ============================================================
# Application
# ============================================================

def main():

    app = QApplication(
        sys.argv
    )

    window = ModuleSelector()

    window.show()

    sys.exit(
        app.exec_()
    )


if __name__ == "__main__":

    main()