import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QCheckBox,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFrame,
)

from meccha_chameleon_tools.module_registry import MODULES
from meccha_chameleon_tools.module_adapters import (
    get_module_display_status,
)
from meccha_chameleon_tools.module_runtime import (
    ModuleRuntimeManager,
    build_runtime_summary,
)


# ============================================================
# Module card
# ============================================================

class ModuleCard(QFrame):

    def __init__(
        self,
        module,
        on_changed=None
    ):
        super().__init__()

        self.module = module
        self.module_id = module["id"]
        self.on_changed = on_changed

        self.runtime_status = (
            get_module_display_status(
                self.module_id
            )
        )

        self.ready = (
            self.runtime_status == "READY"
        )

        self.setObjectName(
            "moduleCard"
        )

        self.setProperty(
            "selected",
            False
        )

        self.setMinimumWidth(300)
        self.setFixedHeight(105)

        layout = QVBoxLayout(self)

        layout.setContentsMargins(
            15,
            11,
            15,
            10,
        )

        layout.setSpacing(3)

        # ----------------------------------------------------
        # Header
        # ----------------------------------------------------

        header = QHBoxLayout()
        header.setSpacing(6)

        title = QLabel(
            module["name"]
        )

        title.setObjectName(
            "moduleTitle"
        )

        self.status_label = QLabel()

        self.update_runtime_badge(
            self.runtime_status
        )

        header.addWidget(
            title
        )

        header.addStretch()

        header.addWidget(
            self.status_label
        )

        # ----------------------------------------------------
        # Description
        # ----------------------------------------------------

        description = QLabel(
            module["description"]
        )

        description.setObjectName(
            "moduleDescription"
        )

        # ----------------------------------------------------
        # Bottom
        # ----------------------------------------------------

        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        path = QLabel(
            module["path"]
        )

        path.setObjectName(
            "modulePath"
        )

        self.checkbox = QCheckBox(
            "Enable"
        )

        self.checkbox.setObjectName(
            "moduleCheck"
        )

        if not self.ready:

            self.checkbox.setEnabled(
                False
            )

            self.checkbox.setText(
                "Unavailable"
            )

        self.checkbox.toggled.connect(
            self._selection_changed
        )

        bottom.addWidget(
            path
        )

        bottom.addStretch()

        bottom.addWidget(
            self.checkbox
        )

        layout.addLayout(
            header
        )

        layout.addWidget(
            description
        )

        layout.addStretch()

        layout.addLayout(
            bottom
        )

    # ========================================================
    # Badge
    # ========================================================

    def update_runtime_badge(
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

        if self.on_changed:

            self.on_changed()

    def mousePressEvent(
        self,
        event
    ):

        if (
            event.button()
            == Qt.LeftButton
            and self.ready
        ):

            self.checkbox.setChecked(
                not self.checkbox.isChecked()
            )

        super().mousePressEvent(
            event
        )

    def is_selected(self):

        return (
            self.ready
            and self.checkbox.isChecked()
        )


# ============================================================
# Main selector
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
            font-size: 13px;
            font-weight: bold;
            background: transparent;
        }

        QFrame#moduleCard {
            background-color: #1d212c;
            border: 1px solid #2b3140;
            border-radius: 9px;
        }

        QFrame#moduleCard:hover {
            background-color: #212633;
            border: 1px solid #46516a;
        }

        QFrame#moduleCard[selected="true"] {
            background-color: #20283a;
            border: 1px solid #5b8cff;
        }

        QLabel#moduleTitle {
            color: #e1e5ef;
            font-size: 13px;
            font-weight: bold;
            background: transparent;
        }

        QLabel#moduleDescription {
            color: #9ba3b8;
            font-size: 9px;
            background: transparent;
        }

        QLabel#modulePath {
            color: #606a80;
            font-size: 8px;
            background: transparent;
        }

        QLabel#statusReady {
            color: #63d9a4;
            background-color: #173328;
            border: 1px solid #285943;
            border-radius: 5px;
            padding: 2px 7px;
            font-size: 8px;
            font-weight: bold;
        }

        QLabel#statusPending {
            color: #ffbd62;
            background-color: #372b18;
            border: 1px solid #66502b;
            border-radius: 5px;
            padding: 2px 7px;
            font-size: 8px;
            font-weight: bold;
        }

        QLabel#statusIdle {
            color: #8b93a7;
            background-color: #20242e;
            border: 1px solid #343a49;
            border-radius: 5px;
            padding: 2px 7px;
            font-size: 8px;
            font-weight: bold;
        }

        QCheckBox#moduleCheck {
            color: #c9cedb;
            font-size: 9px;
            spacing: 6px;
            background: transparent;
        }

        QCheckBox#moduleCheck:disabled {
            color: #5b6275;
        }

        QCheckBox::indicator {
            width: 14px;
            height: 14px;
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

        QPushButton {
            background-color: #222737;
            color: #d7dbe6;
            border: 1px solid #2b3140;
            padding: 8px 14px;
            border-radius: 7px;
            font-size: 10px;
        }

        QPushButton:hover {
            background-color: #2c3349;
            border-color: #4a5470;
        }

        QPushButton:pressed {
            background-color: #353d57;
        }

        QPushButton#prepareButton {
            background-color: #3f6fe0;
            color: white;
            border: 1px solid #6d9bff;
            font-weight: bold;
            padding-left: 18px;
            padding-right: 18px;
        }

        QPushButton#prepareButton:hover {
            background-color: #5b8cff;
        }

        QPushButton#resetButton {
            background-color: #252a37;
        }

        QPushButton#resetButton:hover {
            background-color: #303746;
        }

        QLabel#statusLabel {
            color: #9ba3b8;
            font-size: 10px;
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

        self.cards = {}

        # 공통 Runtime Manager
        self.runtime_manager = (
            ModuleRuntimeManager()
        )

        self.setWindowTitle(
            "Meccha Chameleon Tools"
        )

        self.resize(
            900,
            690
        )

        self.setMinimumSize(
            820,
            650
        )

        self.setStyleSheet(
            self.STYLE
        )

        root = QVBoxLayout(self)

        root.setContentsMargins(
            14,
            14,
            14,
            14,
        )

        # ====================================================
        # Main frame
        # ====================================================

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
            26,
            20,
            26,
            18,
        )

        main_layout.setSpacing(
            9
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
            "Select the modules you want to prepare."
        )

        subtitle.setObjectName(
            "subtitle"
        )

        info_row = QHBoxLayout()

        version = QLabel(
            "Target Game Version: 4.0.2"
        )

        version.setObjectName(
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

        info_row.addWidget(
            version
        )

        info_row.addStretch()

        info_row.addWidget(
            module_count
        )

        main_layout.addWidget(
            title
        )

        main_layout.addWidget(
            subtitle
        )

        main_layout.addLayout(
            info_row
        )

        # ====================================================
        # Section
        # ====================================================

        section_row = QHBoxLayout()

        section_title = QLabel(
            "MODULES"
        )

        section_title.setObjectName(
            "sectionTitle"
        )

        hint = QLabel(
            "Click a card or checkbox to select"
        )

        hint.setObjectName(
            "subtitle"
        )

        section_row.addWidget(
            section_title
        )

        section_row.addStretch()

        section_row.addWidget(
            hint
        )

        main_layout.addLayout(
            section_row
        )

        # ====================================================
        # Grid
        # ====================================================

        grid = QGridLayout()

        grid.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        grid.setHorizontalSpacing(
            12
        )

        grid.setVerticalSpacing(
            10
        )

        grid.setColumnStretch(
            0,
            1
        )

        grid.setColumnStretch(
            1,
            1
        )

        for index, module in enumerate(
            MODULES
        ):

            card = ModuleCard(
                module,
                self.update_selection_status,
            )

            row = index // 2
            column = index % 2

            grid.addWidget(
                card,
                row,
                column,
            )

            self.cards[
                module["id"]
            ] = card

        main_layout.addLayout(
            grid
        )

        main_layout.addStretch()

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

        reset_button.setObjectName(
            "resetButton"
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

    # ========================================================
    # Selected IDs
    # ========================================================

    def get_selected_modules(self):

        return [
            module_id
            for module_id, card
            in self.cards.items()
            if card.is_selected()
        ]

    # ========================================================
    # Footer update
    # ========================================================

    def update_selection_status(self):

        selected = (
            self.get_selected_modules()
        )

        ready_count = sum(
            1
            for card
            in self.cards.values()
            if card.ready
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

        for card in self.cards.values():

            if card.ready:

                card.checkbox.setChecked(
                    True
                )

        self.update_selection_status()

    # ========================================================
    # Clear
    # ========================================================

    def clear_selection(self):

        for card in self.cards.values():

            card.checkbox.setChecked(
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

            card = self.cards.get(
                result.module_id
            )

            if card is not None:

                card.update_runtime_badge(
                    result.status
                )

        summary = (
            build_runtime_summary(
                results
            )
        )

        self.status_label.setText(
            summary
        )

        print()
        print(
            "MODULE PREPARE RESULT"
        )
        print(
            "=" * 60
        )

        for result in results:

            print(
                f"[{result.status}] "
                f"{result.name}"
            )

            print(
                f"  Adapter  : "
                f"{result.adapter_type}"
            )

            print(
                f"  Language : "
                f"{result.language}"
            )

            print(
                f"  Message  : "
                f"{result.message}"
            )

            print()

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

            card = self.cards.get(
                result.module_id
            )

            if card is not None:

                card.update_runtime_badge(
                    "IDLE"
                )

        self.status_label.setText(
            "Selected modules reset."
        )


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